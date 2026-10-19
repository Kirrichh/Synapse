//! Command JCS values. Historical evidence codecs and control v1 are unchanged.
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::fmt;

pub const MAX_BYTES: usize = 65536;
pub const MAX_DEPTH: usize = 32;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InvalidCommandValue;
impl fmt::Display for InvalidCommandValue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("INVALID_COMMAND_VALUE")
    }
}
impl std::error::Error for InvalidCommandValue {}

// Decimal normalization compares the intended JSON value before and after
// binary64 conversion without depending on a second floating point parser.
fn decimal(token: &str) -> Result<(bool, String, i64), InvalidCommandValue> {
    let negative = token.starts_with('-');
    let unsigned = token.strip_prefix('-').unwrap_or(token);
    let (mantissa, exponent) = unsigned.split_once(['e', 'E']).unwrap_or((unsigned, "0"));
    let fractional = mantissa.split_once('.').map_or(0, |(_, tail)| tail.len());
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    if digits.is_empty() || !digits.bytes().all(|c| c.is_ascii_digit()) {
        return Err(InvalidCommandValue);
    }
    let significant = digits.trim_start_matches('0');
    if significant.is_empty() {
        return Ok((false, String::new(), 0));
    }
    let exponent = exponent.parse::<i64>().map_err(|_| InvalidCommandValue)?;
    let trimmed = significant.trim_end_matches('0');
    let shift = (significant.len() - trimmed.len()) as i64 - fractional as i64;
    Ok((negative, trimmed.to_owned(), exponent.checked_add(shift).ok_or(InvalidCommandValue)?))
}

fn check_number(token: &str) -> Result<(), InvalidCommandValue> {
    let number = token.parse::<f64>().map_err(|_| InvalidCommandValue)?;
    if !number.is_finite() {
        return Err(InvalidCommandValue);
    }
    let encoded = serde_json_canonicalizer::to_string(&number).map_err(|_| InvalidCommandValue)?;
    if decimal(token)? != decimal(&encoded)? {
        return Err(InvalidCommandValue);
    }
    Ok(())
}

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(decoder: D) -> Result<Self, D::Error> {
        decoder.deserialize_any(CommandVisitor)
    }
}
struct CommandVisitor;
impl<'de> Visitor<'de> for CommandVisitor {
    type Value = Strict;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("unambiguous command JSON")
    }
    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Strict, E> { Ok(Strict(json!(value))) }
    fn visit_unit<E: de::Error>(self) -> Result<Strict, E> { Ok(Strict(Value::Null)) }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Strict, E> { Ok(Strict(json!(value))) }
    fn visit_string<E: de::Error>(self, value: String) -> Result<Strict, E> { Ok(Strict(json!(value))) }
    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Strict, E> {
        if !(-9_007_199_254_740_991..=9_007_199_254_740_991).contains(&value) {
            return Ok(Strict(json!(value as f64)));
        }
        Ok(Strict(json!(value)))
    }
    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Strict, E> {
        if value > 9_007_199_254_740_991 { return Ok(Strict(json!(value as f64))); }
        Ok(Strict(json!(value)))
    }
    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Strict, E> {
        if !value.is_finite() { return Err(E::custom("nonfinite number")); }
        Ok(Strict(json!(value)))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> Result<Strict, A::Error> {
        let mut values = Vec::new();
        while let Some(Strict(value)) = sequence.next_element()? { values.push(value); }
        Ok(Strict(Value::Array(values)))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut object: A) -> Result<Strict, A::Error> {
        let mut values = Map::new();
        while let Some(key) = object.next_key::<String>()? {
            if values.contains_key(&key) { return Err(de::Error::custom("duplicate property")); }
            let Strict(value) = object.next_value()?;
            values.insert(key, value);
        }
        Ok(Strict(Value::Object(values)))
    }
}

pub fn decode(bytes: &[u8]) -> Result<Value, InvalidCommandValue> {
    if bytes.is_empty() || bytes.len() > MAX_BYTES { return Err(InvalidCommandValue); }
    let text = std::str::from_utf8(bytes).map_err(|_| InvalidCommandValue)?;
    let (mut depth, mut quoted, mut escaped, mut offset) = (0usize, false, false, 0usize);
    while offset < bytes.len() {
        let byte = bytes[offset];
        if quoted {
            if escaped { escaped = false; }
            else if byte == b'\\' { escaped = true; }
            else if byte == b'"' { quoted = false; }
        } else if byte == b'"' { quoted = true; }
        else if byte == b'[' || byte == b'{' {
            depth += 1;
            if depth > MAX_DEPTH { return Err(InvalidCommandValue); }
        } else if byte == b']' || byte == b'}' { depth = depth.saturating_sub(1); }
        else if byte == b'-' || byte.is_ascii_digit() {
            let start = offset;
            while offset < bytes.len() && (bytes[offset].is_ascii_digit() || b"-+.eE".contains(&bytes[offset])) {
                offset += 1;
            }
            check_number(&text[start..offset])?;
            continue;
        }
        offset += 1;
    }
    let Strict(value) = serde_json::from_str(text).map_err(|_| InvalidCommandValue)?;
    canonical_bytes(&value)?;
    Ok(value)
}

pub fn canonical_bytes(value: &Value) -> Result<Vec<u8>, InvalidCommandValue> {
    let mut pending = vec![(value, 0usize)];
    let mut count = 0;
    while let Some((item, depth)) = pending.pop() {
        count += 1;
        if count > MAX_BYTES { return Err(InvalidCommandValue); }
        match item {
            Value::Array(items) => {
                if depth >= MAX_DEPTH { return Err(InvalidCommandValue); }
                pending.extend(items.iter().map(|v| (v, depth + 1)));
            }
            Value::Object(items) => {
                if depth >= MAX_DEPTH { return Err(InvalidCommandValue); }
                pending.extend(items.values().map(|v| (v, depth + 1)));
            }
            Value::Number(number) => {
                if number.as_i64().is_some_and(|v| !(-9_007_199_254_740_991..=9_007_199_254_740_991).contains(&v))
                    || number.as_u64().is_some_and(|v| v > 9_007_199_254_740_991) {
                    return Err(InvalidCommandValue);
                }
            }
            _ => {}
        }
    }
    let bytes = serde_json_canonicalizer::to_vec(value).map_err(|_| InvalidCommandValue)?;
    if bytes.len() > MAX_BYTES { return Err(InvalidCommandValue); }
    Ok(bytes)
}

pub fn parameter_hash(params: &Value) -> Result<String, InvalidCommandValue> {
    if !params.is_object() { return Err(InvalidCommandValue); }
    Ok(format!("{:x}", Sha256::digest(canonical_bytes(params)?)))
}
