//! Time source for rekey intervals and reconnect backoff, injectable so tests
//! run without real waiting.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub trait Clock: Send + Sync + 'static {
    /// Monotonic time since an arbitrary origin.
    fn now(&self) -> Duration;
    /// Wait up to `d`. Returns false if `stop` was set before the time passed.
    fn sleep(&self, d: Duration, stop: &AtomicBool) -> bool;
}

pub struct SystemClock {
    origin: Instant,
}

impl SystemClock {
    pub fn new() -> Self {
        SystemClock { origin: Instant::now() }
    }
}

impl Default for SystemClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemClock {
    fn now(&self) -> Duration {
        self.origin.elapsed()
    }

    fn sleep(&self, d: Duration, stop: &AtomicBool) -> bool {
        let end = Instant::now() + d;
        loop {
            if stop.load(Ordering::SeqCst) {
                return false;
            }
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return true;
            }
            std::thread::sleep(left.min(Duration::from_millis(25)));
        }
    }
}

/// A clock that only moves when told to, or when something sleeps on it
/// (a sleep advances it by the requested time and returns at once).
#[derive(Default)]
pub struct FakeClock {
    now: Mutex<Duration>,
    sleeps: Mutex<Vec<Duration>>,
}

impl FakeClock {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn advance(&self, d: Duration) {
        *self.now.lock().unwrap() += d;
    }

    /// Every duration passed to `sleep`, in order.
    pub fn sleeps(&self) -> Vec<Duration> {
        self.sleeps.lock().unwrap().clone()
    }
}

impl Clock for FakeClock {
    fn now(&self) -> Duration {
        *self.now.lock().unwrap()
    }

    fn sleep(&self, d: Duration, stop: &AtomicBool) -> bool {
        if stop.load(Ordering::SeqCst) {
            return false;
        }
        self.sleeps.lock().unwrap().push(d);
        self.advance(d);
        // Let other threads run so a tight reconnect loop stays polite.
        std::thread::sleep(Duration::from_millis(1));
        !stop.load(Ordering::SeqCst)
    }
}
