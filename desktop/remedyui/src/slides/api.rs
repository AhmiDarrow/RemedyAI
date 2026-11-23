//! JSON calls for the slides. The worker thread never touches a signal;
//! the reply is posted back onto the UI.

use crate::runtime::Client;
use remedy_ui::net::Request;
use remedy_ui::reactive::{Cancel, waker};
use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

thread_local! {
    /// Callbacks stay on the UI thread. The worker only posts a Send result.
    static PENDING: RefCell<HashMap<u64, Box<dyn FnOnce(Result<serde_json::Value, String>)>>> =
        RefCell::new(HashMap::new());
}

static NEXT_REQUEST: AtomicU64 = AtomicU64::new(1);

pub fn request(
    client: &Client,
    req: Request,
    done: impl FnOnce(Result<serde_json::Value, String>) + 'static,
) {
    let Ok(wake) = waker() else {
        done(Err("ui is not running".into()));
        return;
    };
    let id = NEXT_REQUEST.fetch_add(1, Ordering::Relaxed);
    PENDING.with(|slot| slot.borrow_mut().insert(id, Box::new(done)));
    let http = client.http().clone();
    let _ = std::thread::Builder::new()
        .name("remedy-slide".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(req, &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<serde_json::Value>().map_err(|e| e.to_string()));
            let _ = wake.post(move || {
                let cb = PENDING.with(|slot| slot.borrow_mut().remove(&id));
                if let Some(cb) = cb {
                    cb(out);
                }
            });
        });
}
