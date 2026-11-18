use std::collections::{BTreeMap, HashMap};

use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyModule, PyString};
use unicode_normalization::UnicodeNormalization;

const MAX_DEPTH: usize = 128;
const SAFE_INTEGER_MIN: i64 = -9_007_199_254_740_991;
const SAFE_INTEGER_MAX: i64 = 9_007_199_254_740_991;

create_exception!(_stage4_native, NativeError, PyValueError);

enum Node {
    Null,
    Bool(bool),
    Int(i64),
    Float(String),
    String(String),
    List(Vec<Node>),
    Dict(BTreeMap<String, Node>),
}

fn refused<T>(code: &'static str, detail: String) -> PyResult<T> {
    Err(NativeError::new_err((code, detail)))
}

fn text(value: &Bound<'_, PyAny>, path: &str) -> PyResult<String> {
    match value.extract::<String>() {
        Ok(text) => Ok(text),
        Err(_) => refused(
            "INVALID_UTF8",
            format!("{path} contains a lone surrogate"),
        ),
    }
}

fn normalized(text: String) -> String {
    if text.is_ascii() {
        text
    } else {
        text.nfc().collect()
    }
}

fn project(
    value: &Bound<'_, PyAny>,
    path: &str,
    depth: usize,
    seen: &mut HashMap<usize, String>,
) -> PyResult<Node> {
    if depth > MAX_DEPTH {
        return refused(
            "INVALID_CANONICAL_VALUE",
            "canonical payload is too deep".to_owned(),
        );
    }
    if value.is_none() {
        return Ok(Node::Null);
    }
    if value.is_exact_instance_of::<PyBool>() {
        return Ok(Node::Bool(value.extract::<bool>()?));
    }
    if value.is_exact_instance_of::<PyString>() {
        return Ok(Node::String(normalized(text(value, path)?)));
    }
    if value.is_exact_instance_of::<PyInt>() {
        return match value.extract::<i64>() {
            Ok(number) if (SAFE_INTEGER_MIN..=SAFE_INTEGER_MAX).contains(&number) => {
                Ok(Node::Int(number))
            }
            _ => refused(
                "INVALID_CANONICAL_VALUE",
                format!("unsafe integer at {path}"),
            ),
        };
    }
    if value.is_exact_instance_of::<PyFloat>() {
        let number = value.extract::<f64>()?;
        if !number.is_finite() {
            return refused(
                "INVALID_CANONICAL_VALUE",
                format!("non-finite float at {path}"),
            );
        }
        if number == 0.0 {
            return Ok(Node::Float("0.0".to_owned()));
        }
        return Ok(Node::Float(value.repr()?.extract::<String>()?));
    }
    let is_list = value.is_exact_instance_of::<PyList>();
    let is_dict = value.is_exact_instance_of::<PyDict>();
    if !is_list && !is_dict {
        return refused(
            "INVALID_CANONICAL_VALUE",
            format!(
                "unsupported canonical type at {path}: {}",
                value.get_type().name()?
            ),
        );
    }

    let identity = value.as_ptr() as usize;
    if seen.contains_key(&identity) {
        return refused(
            "INVALID_CANONICAL_VALUE",
            format!("cycle detected at {path}"),
        );
    }
    seen.insert(identity, path.to_owned());
    let result = (|| {
        if is_list {
            let list = value.downcast::<PyList>()?;
            let mut items = Vec::with_capacity(list.len());
            for (index, item) in list.iter().enumerate() {
                items.push(project(
                    &item,
                    &format!("{path}[{index}]"),
                    depth + 1,
                    seen,
                )?);
            }
            return Ok(Node::List(items));
        }

        let dict = value.downcast::<PyDict>()?;
        let mut items = BTreeMap::new();
        for (key, item) in dict.iter() {
            if !key.is_exact_instance_of::<PyString>() {
                return refused(
                    "INVALID_CANONICAL_VALUE",
                    format!("non-string key at {path}"),
                );
            }
            let canonical_key = normalized(text(&key, &format!("{path}.<key>"))?);
            if items.contains_key(&canonical_key) {
                return refused(
                    "INVALID_CANONICAL_VALUE",
                    format!("dict key collision after NFC normalization at {path}"),
                );
            }
            let projected = project(
                &item,
                &format!("{path}.{canonical_key}"),
                depth + 1,
                seen,
            )?;
            items.insert(canonical_key, projected);
        }
        Ok(Node::Dict(items))
    })();
    seen.remove(&identity);
    result
}

fn emit_string(output: &mut Vec<u8>, value: &str) {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    output.push(b'"');
    for character in value.chars() {
        match character {
            '"' => output.extend_from_slice(b"\\\""),
            '\\' => output.extend_from_slice(b"\\\\"),
            '\u{08}' => output.extend_from_slice(b"\\b"),
            '\u{09}' => output.extend_from_slice(b"\\t"),
            '\u{0a}' => output.extend_from_slice(b"\\n"),
            '\u{0c}' => output.extend_from_slice(b"\\f"),
            '\u{0d}' => output.extend_from_slice(b"\\r"),
            '\u{00}'..='\u{1f}' => {
                let code = character as u8;
                output.extend_from_slice(b"\\u00");
                output.push(HEX[(code >> 4) as usize]);
                output.push(HEX[(code & 0x0f) as usize]);
            }
            _ => {
                let mut encoded = [0_u8; 4];
                output.extend_from_slice(character.encode_utf8(&mut encoded).as_bytes());
            }
        }
    }
    output.push(b'"');
}

fn emit(output: &mut Vec<u8>, node: &Node) {
    match node {
        Node::Null => output.extend_from_slice(b"null"),
        Node::Bool(true) => output.extend_from_slice(b"true"),
        Node::Bool(false) => output.extend_from_slice(b"false"),
        Node::Int(number) => output.extend_from_slice(number.to_string().as_bytes()),
        Node::Float(repr) => output.extend_from_slice(repr.as_bytes()),
        Node::String(value) => emit_string(output, value),
        Node::List(items) => {
            output.push(b'[');
            for (index, item) in items.iter().enumerate() {
                if index != 0 {
                    output.push(b',');
                }
                emit(output, item);
            }
            output.push(b']');
        }
        Node::Dict(items) => {
            output.push(b'{');
            for (index, (key, item)) in items.iter().enumerate() {
                if index != 0 {
                    output.push(b',');
                }
                emit_string(output, key);
                output.push(b':');
                emit(output, item);
            }
            output.push(b'}');
        }
    }
}

#[pyfunction]
fn encode(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Py<PyBytes>> {
    let projected = project(value, "$", 0, &mut HashMap::new())?;
    let mut output = Vec::new();
    emit(&mut output, &projected);
    Ok(PyBytes::new_bound(py, &output).unbind())
}

#[pymodule]
fn _stage4_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(encode, module)?)?;
    module.add("NativeError", module.py().get_type_bound::<NativeError>())?;
    Ok(())
}
