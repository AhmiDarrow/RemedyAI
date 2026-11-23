//! Pending rail context, the same contract as `desktop/src/workspace/railNav.ts`.
//! Browser URL stays with the computer-use port (PR #77).

use std::cell::{Cell, RefCell};
use std::rc::Rc;

struct Slot {
    id: u64,
    cb: Rc<dyn Fn(&str)>,
}

struct ReloadSlot {
    id: u64,
    cb: Rc<dyn Fn()>,
}

thread_local! {
    static NEXT: Cell<u64> = const { Cell::new(1) };
    static FILES_PENDING: RefCell<Option<String>> = RefCell::new(None);
    static CWD_PENDING: RefCell<Option<String>> = RefCell::new(None);
    static FILES_LISTEN: RefCell<Vec<Slot>> = RefCell::new(Vec::new());
    static CWD_LISTEN: RefCell<Vec<Slot>> = RefCell::new(Vec::new());
    static SCRATCH_LISTEN: RefCell<Vec<ReloadSlot>> = RefCell::new(Vec::new());
}

fn next_id() -> u64 {
    NEXT.with(|n| {
        let id = n.get();
        n.set(id.wrapping_add(1));
        id
    })
}

/// Drops the listener it registered.
pub struct NavGuard {
    kind: u8,
    id: u64,
}

impl Drop for NavGuard {
    fn drop(&mut self) {
        match self.kind {
            0 => FILES_LISTEN.with(|l| l.borrow_mut().retain(|s| s.id != self.id)),
            1 => CWD_LISTEN.with(|l| l.borrow_mut().retain(|s| s.id != self.id)),
            _ => SCRATCH_LISTEN.with(|l| l.borrow_mut().retain(|s| s.id != self.id)),
        }
    }
}

/// Remember a project path and tell every open Files slide.
pub fn request_files_path(path: &str) {
    let p = path.trim();
    if p.is_empty() {
        return;
    }
    FILES_PENDING.with(|s| *s.borrow_mut() = Some(p.to_string()));
    let cbs: Vec<_> = FILES_LISTEN.with(|l| l.borrow().iter().map(|s| s.cb.clone()).collect());
    for cb in cbs {
        cb(p);
    }
}

/// Take the pending Files path once. The next call is empty until another request.
pub fn take_pending_files_path() -> Option<String> {
    FILES_PENDING.with(|s| s.borrow_mut().take())
}

pub fn on_files_path(cb: impl Fn(&str) + 'static) -> NavGuard {
    let id = next_id();
    FILES_LISTEN.with(|l| {
        l.borrow_mut().push(Slot {
            id,
            cb: Rc::new(cb),
        })
    });
    NavGuard { kind: 0, id }
}

/// Remember a working directory and tell every open Terminal slide.
pub fn request_terminal_cwd(path: &str) {
    let p = path.trim();
    if p.is_empty() {
        return;
    }
    CWD_PENDING.with(|s| *s.borrow_mut() = Some(p.to_string()));
    let cbs: Vec<_> = CWD_LISTEN.with(|l| l.borrow().iter().map(|s| s.cb.clone()).collect());
    for cb in cbs {
        cb(p);
    }
}

pub fn take_pending_terminal_cwd() -> Option<String> {
    CWD_PENDING.with(|s| s.borrow_mut().take())
}

pub fn on_terminal_cwd(cb: impl Fn(&str) + 'static) -> NavGuard {
    let id = next_id();
    CWD_LISTEN.with(|l| {
        l.borrow_mut().push(Slot {
            id,
            cb: Rc::new(cb),
        })
    });
    NavGuard { kind: 1, id }
}

/// Tell every open Scratch slide to reload from the server.
pub fn request_scratch_reload() {
    let cbs: Vec<_> = SCRATCH_LISTEN.with(|l| l.borrow().iter().map(|s| s.cb.clone()).collect());
    for cb in cbs {
        cb();
    }
}

pub fn on_scratch_reload(cb: impl Fn() + 'static) -> NavGuard {
    let id = next_id();
    SCRATCH_LISTEN.with(|l| {
        l.borrow_mut().push(ReloadSlot {
            id,
            cb: Rc::new(cb),
        })
    });
    NavGuard { kind: 2, id }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::Cell;

    #[test]
    fn pending_paths_are_taken_once() {
        request_files_path("  ");
        assert!(take_pending_files_path().is_none());
        request_files_path(" desktop/src ");
        assert_eq!(take_pending_files_path().as_deref(), Some("desktop/src"));
        assert!(take_pending_files_path().is_none());

        request_terminal_cwd(" /tmp ");
        assert_eq!(take_pending_terminal_cwd().as_deref(), Some("/tmp"));
        assert!(take_pending_terminal_cwd().is_none());
    }

    #[test]
    fn listeners_see_requests_and_drop() {
        let n = Rc::new(Cell::new(0));
        let n2 = n.clone();
        let guard = on_files_path(move |_| n2.set(n2.get() + 1));
        request_files_path("a");
        assert_eq!(n.get(), 1);
        drop(guard);
        request_files_path("b");
        assert_eq!(n.get(), 1);
        let _ = take_pending_files_path();

        let hits = Rc::new(Cell::new(0));
        let h2 = hits.clone();
        let guard = on_scratch_reload(move || h2.set(h2.get() + 1));
        request_scratch_reload();
        assert_eq!(hits.get(), 1);
        drop(guard);
        request_scratch_reload();
        assert_eq!(hits.get(), 1);
    }
}
