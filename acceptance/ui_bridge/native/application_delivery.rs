use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use synapse_desktop::{application_delivery::ApplicationDelivery, protocol::Failure};

#[test]
fn result_slot_requires_the_exact_delivered_receipt_before_another_acceptance() {
    let delivery = ApplicationDelivery::default();
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    let copy = received.clone();
    let subscription = delivery.subscribe(Arc::new(move |text| { copy.lock().unwrap().push(text); Ok(()) }));
    let ticket = delivery.reserve(&subscription).unwrap();
    let request = ticket.request_id().to_owned();
    assert!(matches!(delivery.reserve(&subscription), Err(Failure("BUSY"))));
    assert!(delivery.acknowledge(&subscription, &request).is_err());
    delivery.deliver(ticket, Ok(json!({"grants":[]}))).unwrap();
    assert!(matches!(delivery.reserve(&subscription), Err(Failure("BUSY"))));
    assert!(delivery.acknowledge(&subscription, "unrelated").is_err());
    let value: Value = serde_json::from_str(&received.lock().unwrap()[0]).unwrap();
    assert_eq!(value["subscription_id"], subscription);
    assert_eq!(value["request_id"], request);
    assert_eq!(value["result"]["grants"], json!([]));
    delivery.acknowledge(&subscription, &request).unwrap();
    assert!(delivery.acknowledge(&subscription, &request).is_err());
    let second = delivery.reserve(&subscription).unwrap();
    delivery.refused_before_enqueue(&second);
    assert!(delivery.reserve(&subscription).is_ok());
}

#[test]
fn replaced_or_unsubscribed_channels_cannot_receive_a_previous_completion() {
    let delivery = ApplicationDelivery::default();
    let old = delivery.subscribe(Arc::new(|_| panic!("obsolete result was delivered")));
    let ticket = delivery.reserve(&old).unwrap();
    let current = delivery.subscribe(Arc::new(|_| Ok(())));
    assert_eq!(delivery.deliver(ticket, Ok(json!({"private":"old scope"}))), Err(Failure("SUBSCRIPTION_UNKNOWN")));
    delivery.unsubscribe(&old);
    let ticket = delivery.reserve(&current).unwrap();
    delivery.unsubscribe(&current);
    assert_eq!(delivery.deliver(ticket, Ok(json!({}))), Err(Failure("SUBSCRIPTION_UNKNOWN")));
    assert!(delivery.reserve(&current).is_err());
}

#[test]
fn lost_sink_stays_blocked_and_oversized_content_has_a_bounded_named_refusal() {
    let delivery = ApplicationDelivery::default();
    let id = delivery.subscribe(Arc::new(|_| Err(())));
    let ticket = delivery.reserve(&id).unwrap();
    assert_eq!(delivery.deliver(ticket.clone(), Ok(json!({}))), Err(Failure("DELIVERY_FAILED")));
    delivery.refused_before_enqueue(&ticket);
    assert!(matches!(delivery.reserve(&id), Err(Failure("BUSY"))));
    let received = Arc::new(Mutex::new(String::new()));
    let copy = received.clone();
    let id = delivery.subscribe(Arc::new(move |text| { *copy.lock().unwrap() = text; Ok(()) }));
    let ticket = delivery.reserve(&id).unwrap();
    delivery.deliver(ticket, Ok(json!({"sensitive":"x".repeat(65536)}))).unwrap();
    let value: Value = serde_json::from_str(&received.lock().unwrap()).unwrap();
    assert_eq!(value["type"], "error");
    assert_eq!(value["code"], "RESPONSE_TOO_LARGE");
    assert!(value.get("result").is_none());
}
