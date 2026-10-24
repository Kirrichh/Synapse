//! The schema bounds stderr retention without truncating unknown data into a code.
use std::{
    io::Cursor,
    time::{Duration, Instant},
};
use synapse_desktop::{diagnostics::Diagnostics, protocol::ERROR_CODES};
#[test]
fn longest_code_is_retained_but_its_overlong_prefix_is_never_trusted() {
    let longest = ERROR_CODES.iter().max_by_key(|code| code.len()).unwrap();
    let diagnostics = Diagnostics::default();
    diagnostics.drain(Cursor::new(
        format!(
            "machine: {longest}\nmachine: {longest}{}\nmachine: PIPE_CLOSED\n",
            "X".repeat(10000)
        )
        .into_bytes(),
    ));
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let events = diagnostics.snapshot();
        if events.last().is_some_and(|code| code == "PIPE_CLOSED") {
            assert_eq!(
                events,
                vec![longest.as_str(), "CORE_DIAGNOSTIC", "PIPE_CLOSED"]
            );
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::yield_now();
    }
}
