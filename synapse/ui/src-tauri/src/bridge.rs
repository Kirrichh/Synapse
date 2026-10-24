//! Negotiation and correlated control exchanges, independent of renderer delivery.
use crate::{
    protocol::{self, Failure, PROFILE},
    transport::PipeConnection,
};
use serde_json::Value;
use std::time::{Duration, Instant};

pub struct Session {
    connection: PipeConnection,
    pub last: Value,
}
impl Session {
    pub fn domain_exchange(&mut self, method: &str, params: Value,
                           grant: Option<(&str, u64)>, deadline: Instant)
                           -> Result<Result<Value, &'static str>, Failure> {
        let request = crate::domain_protocol::request(method, params, grant)?;
        self.connection.send_before(&request, deadline)?;
        let remaining = deadline.checked_duration_since(Instant::now())
            .ok_or(Failure("TRANSPORT_TIMEOUT"))?;
        let reply = self.connection.receive(remaining)?;
        // Do not copy domain data into the control/diagnostic publication.
        crate::domain_protocol::result(&request, reply)
    }

    pub fn connect(connection: PipeConnection, version: &str) -> Result<Self, Failure> {
        let deadline = Instant::now() + Duration::from_secs(PROFILE.handshake_timeout_seconds);
        let remaining = || {
            deadline
                .checked_duration_since(Instant::now())
                .ok_or(Failure("HANDSHAKE_TIMEOUT"))
        };
        let peer = connection.receive(remaining()?).map_err(|failure| {
            if failure.0 == "TRANSPORT_TIMEOUT" {
                Failure("HANDSHAKE_TIMEOUT")
            } else {
                failure
            }
        })?;
        let limit = protocol::check_hello(&peer, version)?;
        connection.send_before(&protocol::hello(env!("CARGO_PKG_VERSION")), deadline)?;
        remaining()?;
        connection.set_limit(limit);
        connection.send_before(&protocol::select(limit), deadline)?;
        let ready = connection.receive(remaining()?).map_err(|failure| {
            if failure.0 == "TRANSPORT_TIMEOUT" {
                Failure("HANDSHAKE_TIMEOUT")
            } else {
                failure
            }
        })?;
        protocol::check_ready(&ready, limit)?;
        Ok(Self {
            connection,
            last: ready,
        })
    }
    /// None acknowledges the command; Some(code) is a correlated, nonfatal
    /// refusal. It leaves the control session usable and proves no shutdown.
    pub fn exchange(
        &mut self,
        method: &str,
        deadline: Instant,
    ) -> Result<Option<&'static str>, Failure> {
        let id = uuid::Uuid::new_v4().to_string();
        self.connection
            .send_before(&protocol::request(method, &id), deadline)?;
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .ok_or(Failure("TRANSPORT_TIMEOUT"))?;
        let reply = self.connection.receive(remaining)?;
        if let Some((code, fatal)) = protocol::check_refusal(&reply, Some(&id))? {
            self.last = reply;
            return if fatal {
                Err(Failure(code))
            } else {
                Ok(Some(code))
            };
        }
        protocol::check_result(&reply, method, &id)?;
        self.last = reply;
        Ok(None)
    }
}
