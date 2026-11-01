//! Opt-in wall-clock timing. Never pass user-controlled data as operation labels.

use std::future::Future;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;
use tracing::Instrument;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct Timing {
    operation: &'static str,
    started: Instant,
    outcome: &'static str,
    dispatch: tracing::Dispatch,
    span: tracing::Span,
}

impl Drop for Timing {
    fn drop(&mut self) {
        // A cancelled future may be dropped outside the task's subscriber.
        // Retain both dispatch and span so cancellation is still observable.
        tracing::dispatcher::with_default(&self.dispatch, || self.span.in_scope(|| {
            tracing::debug!(
                target: "bot_latency",
                operation = self.operation,
                elapsed_ms = self.started.elapsed().as_secs_f64() * 1000.0,
                outcome = self.outcome,
                "operation finished"
            );
        }));
    }
}

/// Time a result-producing future without inspecting or logging either value.
/// Nested calls inherit the current diagnostic span across asynchronous waits.
pub async fn measure<T, E>(
    operation: &'static str,
    future: impl Future<Output = Result<T, E>>,
) -> Result<T, E> {
    if !tracing::enabled!(target: "bot_latency", tracing::Level::DEBUG) {
        return future.await;
    }
    let span = tracing::debug_span!(
        target: "bot_latency",
        "latency",
        operation,
        diagnostic_id = NEXT_ID.fetch_add(1, Ordering::Relaxed)
    );
    async move {
        let mut timing = Timing {
            operation,
            started: Instant::now(),
            outcome: "cancelled_or_panicked",
            dispatch: tracing::dispatcher::get_default(Clone::clone),
            span: tracing::Span::current(),
        };
        let result = future.await;
        timing.outcome = if result.is_ok() { "ok" } else { "error" };
        result
    }
    .instrument(span)
    .await
}
