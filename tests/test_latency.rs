use std::io::{self, Write};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use bot_tele::diagnostics::measure;
use tracing::instrument::WithSubscriber;

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[tokio::test]
async fn latency_records_wait_outcomes_and_correlation_without_result_contents() {
    let capture = Capture::default();
    let writer = capture.clone();
    let subscriber = tracing_subscriber::fmt()
        .json()
        .with_max_level(tracing::Level::DEBUG)
        .with_writer(move || writer.clone())
        .finish();

    async {
        let result = measure("navigation_test", async {
            measure("supplier_test", async {
                tokio::time::sleep(Duration::from_millis(20)).await;
                Err::<(), _>("SECRET_ERROR_WITH_TOKEN")
            })
            .await
        })
        .await;
        assert_eq!(result, Err("SECRET_ERROR_WITH_TOKEN"));
        assert_eq!(measure("success_test", async { Ok::<_, ()>(42) }).await, Ok(42));
    }
    .with_subscriber(subscriber)
    .await;

    let output = String::from_utf8(capture.0.lock().unwrap().clone()).unwrap();
    assert!(!output.contains("SECRET_ERROR_WITH_TOKEN"));
    let events: Vec<serde_json::Value> = output
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    assert_eq!(events.len(), 3);
    assert_eq!(events[0]["fields"]["outcome"], "error");
    assert!(events[0]["fields"]["elapsed_ms"].as_f64().unwrap() >= 15.0);
    assert_eq!(events[1]["fields"]["outcome"], "error");
    assert_eq!(events[2]["fields"]["outcome"], "ok");
    let parent_id = &events[1]["span"]["diagnostic_id"];
    assert!(parent_id.as_u64().is_some());
    assert_eq!(&events[0]["spans"][0]["diagnostic_id"], parent_id);
}

#[tokio::test]
async fn latency_is_silent_at_normal_info_level() {
    let capture = Capture::default();
    let writer = capture.clone();
    let subscriber = tracing_subscriber::fmt()
        .with_max_level(tracing::Level::INFO)
        .with_writer(move || writer.clone())
        .finish();
    measure("hidden_test", async { Ok::<_, ()>(()) })
        .with_subscriber(subscriber)
        .await
        .unwrap();
    assert!(capture.0.lock().unwrap().is_empty());
}

#[tokio::test]
async fn latency_records_cancellation_with_the_operation_span() {
    let capture = Capture::default();
    let writer = capture.clone();
    let subscriber = tracing_subscriber::fmt()
        .json()
        .with_max_level(tracing::Level::DEBUG)
        .with_writer(move || writer.clone())
        .finish();
    let result = tokio::time::timeout(
        Duration::from_millis(10),
        measure("cancel_test", std::future::pending::<Result<(), ()>>()),
    )
    .with_subscriber(subscriber)
    .await;
    assert!(result.is_err());
    let output = String::from_utf8(capture.0.lock().unwrap().clone()).unwrap();
    let event: serde_json::Value = serde_json::from_str(output.trim()).unwrap();
    assert_eq!(event["fields"]["outcome"], "cancelled_or_panicked");
    assert_eq!(event["span"]["operation"], "cancel_test");
    assert!(event["span"]["diagnostic_id"].as_u64().is_some());
}
