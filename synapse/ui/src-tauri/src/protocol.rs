//! Strict machine boundary. The shared schema and release profile own vocabulary.
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::{json, Map, Value};
use std::{fmt, sync::LazyLock};

pub const SCHEMA: &str = include_str!("../../../machine/schemas/envelope.schema.json");
pub const RELEASE: &str = include_str!("../../../machine/release_profile.json");

#[derive(Debug, Clone, Deserialize)]
pub struct Profile {
    pub profile_id: String,
    pub protocol_version: u32,
    pub bootstrap_max_frame_bytes: usize,
    pub max_frame_bytes: usize,
    pub min_frame_bytes: usize,
    pub max_json_depth: usize,
    pub queue_capacity: usize,
    pub handshake_timeout_seconds: u64,
    pub write_timeout_seconds: u64,
    pub shutdown_timeout_seconds: u64,
}

pub static PROFILE: LazyLock<Profile> =
    LazyLock::new(|| serde_json::from_str(RELEASE).expect("release profile"));
static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| {
    let schema: Value = serde_json::from_str(SCHEMA).expect("envelope schema");
    jsonschema::options()
        .should_validate_formats(true)
        .build(&schema)
        .expect("valid schema")
});

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure(pub &'static str);
impl fmt::Display for Failure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for Failure {}

struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(decoder: D) -> Result<Self, D::Error> {
        decoder.deserialize_any(StrictVisitor)
    }
}
struct StrictVisitor;
impl<'de> Visitor<'de> for StrictVisitor {
    type Value = Strict;
    fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str("unambiguous control JSON")
    }
    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Strict, E> {
        Ok(Strict(json!(value)))
    }
    fn visit_unit<E: de::Error>(self) -> Result<Strict, E> {
        Ok(Strict(Value::Null))
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Strict, E> {
        Ok(Strict(json!(value)))
    }
    fn visit_string<E: de::Error>(self, value: String) -> Result<Strict, E> {
        Ok(Strict(json!(value)))
    }
    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Strict, E> {
        if !(-9_007_199_254_740_991..=9_007_199_254_740_991).contains(&value) {
            return Err(E::custom("integer precision"));
        }
        Ok(Strict(json!(value)))
    }
    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Strict, E> {
        if value > 9_007_199_254_740_991 {
            return Err(E::custom("integer precision"));
        }
        Ok(Strict(json!(value)))
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<Strict, E> {
        Err(E::custom("integer tokens required"))
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut sequence: A) -> Result<Strict, A::Error> {
        let mut values = Vec::new();
        while let Some(Strict(value)) = sequence.next_element()? {
            values.push(value);
        }
        Ok(Strict(Value::Array(values)))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut object: A) -> Result<Strict, A::Error> {
        let mut values = Map::new();
        while let Some(key) = object.next_key::<String>()? {
            if values.contains_key(&key) {
                return Err(de::Error::custom("duplicate property"));
            }
            let Strict(value) = object.next_value()?;
            values.insert(key, value);
        }
        Ok(Strict(Value::Object(values)))
    }
}

pub fn decode(bytes: &[u8]) -> Result<Value, Failure> {
    if bytes.is_empty() || bytes.len() > PROFILE.max_frame_bytes {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    let text = std::str::from_utf8(bytes).map_err(|_| Failure("PROTOCOL_ERROR"))?;
    let (mut depth, mut quoted, mut escaped) = (0usize, false, false);
    for byte in text.bytes() {
        if quoted {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                quoted = false;
            }
        } else if byte == b'"' {
            quoted = true;
        } else if byte == b'[' || byte == b'{' {
            depth += 1;
            if depth > PROFILE.max_json_depth {
                return Err(Failure("PROTOCOL_ERROR"));
            }
        } else if byte == b']' || byte == b'}' {
            depth = depth.saturating_sub(1);
        }
    }
    let Strict(value) = serde_json::from_str(text).map_err(|_| Failure("PROTOCOL_ERROR"))?;
    if !VALIDATOR.is_valid(&value) {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    Ok(value)
}

pub fn hello(version: &str) -> Value {
    json!({"schema_version":"1.0", "type":"hello", "component":"shell",
        "component_version":version, "protocol_min":PROFILE.protocol_version,
        "protocol_max":PROFILE.protocol_version, "profile":PROFILE.profile_id,
        "max_frame_bytes":PROFILE.max_frame_bytes})
}
pub fn select(limit: usize) -> Value {
    json!({"schema_version":"1.0", "type":"select", "profile":PROFILE.profile_id,
        "protocol_version":PROFILE.protocol_version, "max_frame_bytes":limit,
        "encoding":"utf-8", "framing":"u32be"})
}
pub fn request(method: &str, id: &str) -> Value {
    json!({"schema_version":"1.0", "type":"request", "request_id":id, "method":method, "params":{}})
}
pub fn check_hello(value: &Value, runtime_version: &str) -> Result<usize, Failure> {
    if value["type"] != "hello"
        || value["component"] != "core"
        || value["profile"] != PROFILE.profile_id
        || value["protocol_min"]
            .as_u64()
            .is_none_or(|v| v > PROFILE.protocol_version as u64)
        || value["protocol_max"]
            .as_u64()
            .is_none_or(|v| v < PROFILE.protocol_version as u64)
    {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    if value["component_version"] != runtime_version {
        return Err(Failure("CORE_VERSION"));
    }
    let limit = value["max_frame_bytes"]
        .as_u64()
        .ok_or(Failure("PROTOCOL_ERROR"))? as usize;
    if limit < PROFILE.min_frame_bytes || limit > PROFILE.max_frame_bytes {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    Ok(limit)
}
pub fn check_ready(value: &Value, limit: usize) -> Result<(), Failure> {
    if value["type"] != "ready"
        || value["profile"] != PROFILE.profile_id
        || value["protocol_version"] != PROFILE.protocol_version
        || value["max_frame_bytes"] != limit
        || value["command_classes"] != json!(["control"])
        || value["domain_ready"] != false
    {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    Ok(())
}
pub fn check_result(value: &Value, method: &str, id: &str) -> Result<(), Failure> {
    if value["type"] != "result" || value["method"] != method || value["request_id"] != id {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    Ok(())
}
