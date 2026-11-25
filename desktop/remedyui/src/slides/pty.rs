//! In-app terminal via portable-pty (ConPTY on Windows, a Unix PTY elsewhere).
//! The same shell choice as `desktop/src-tauri/src/pty_host.rs`. The widget does
//! not start a process; this host does, and the caller feeds bytes to the screen.
//!
//! Opening, resizing, writing, and closing all run on a worker thread. ConPTY
//! `CreatePseudoConsole` / `CreateProcessW`, `ResizePseudoConsole`,
//! `ClosePseudoConsole`, and `child.wait()` can block for a long time — especially
//! in an elevated Windows process — and must never run on the UI thread. A hang
//! after opening Terminal and then resizing the window was `Responding=False`
//! with no panic: the UI thread had blocked in one of those calls.

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn portable_pty::Child + Send + Sync>>,
}

enum Op {
    Open {
        cwd: Option<String>,
        cols: u16,
        rows: u16,
        on_data: Box<dyn Fn(&[u8]) + Send>,
        on_exit: Box<dyn FnOnce() + Send>,
        on_done: Box<dyn FnOnce(Result<String, String>) + Send>,
    },
    Write {
        id: String,
        data: Vec<u8>,
    },
    Resize {
        id: String,
        cols: u16,
        rows: u16,
    },
    Close {
        id: String,
    },
}

/// Live PTY sessions. Cheap to clone; sessions live until [`PtyHost::close`].
#[derive(Clone)]
pub struct PtyHost {
    tx: Sender<Op>,
    live: Arc<Mutex<HashSet<String>>>,
}

impl Default for PtyHost {
    fn default() -> Self {
        Self::new()
    }
}

impl PtyHost {
    pub fn new() -> Self {
        let (tx, rx) = mpsc::channel();
        let live = Arc::new(Mutex::new(HashSet::new()));
        let live_worker = Arc::clone(&live);
        thread::Builder::new()
            .name("pty-host".into())
            .spawn(move || worker_loop(rx, live_worker))
            .expect("pty-host thread");
        Self { tx, live }
    }

    /// Spawn a shell. Blocks the caller until the worker has opened the PTY
    /// (tests). The UI path is [`Self::open_async`].
    pub fn open(
        &self,
        cwd: Option<&str>,
        cols: u16,
        rows: u16,
        on_data: impl Fn(&[u8]) + Send + 'static,
        on_exit: impl Fn() + Send + 'static,
    ) -> Result<String, String> {
        let (tx, rx) = mpsc::channel();
        self.open_async(cwd, cols, rows, on_data, on_exit, move |result| {
            let _ = tx.send(result);
        });
        rx.recv_timeout(Duration::from_secs(20))
            .map_err(|_| "timed out starting the shell".to_string())?
    }

    /// Spawn a shell on the worker thread. `on_data` runs on the reader thread.
    /// `on_exit` and `on_done` run on worker/reader threads — hop UI work with
    /// a waker, the same as the previous `on_exit` contract.
    pub fn open_async(
        &self,
        cwd: Option<&str>,
        cols: u16,
        rows: u16,
        on_data: impl Fn(&[u8]) + Send + 'static,
        on_exit: impl Fn() + Send + 'static,
        on_done: impl FnOnce(Result<String, String>) + Send + 'static,
    ) {
        let _ = self.tx.send(Op::Open {
            cwd: cwd.map(str::to_string),
            cols,
            rows,
            on_data: Box::new(on_data),
            on_exit: Box::new(on_exit),
            on_done: Box::new(on_done),
        });
    }

    pub fn write(&self, id: &str, data: &str) -> Result<(), String> {
        self.write_bytes(id, data.as_bytes())
    }

    /// Queue raw PTY input on the worker. Returns immediately so a full pipe
    /// cannot stall the UI thread. Keystrokes from the terminal widget are
    /// bytes, not text.
    pub fn write_bytes(&self, id: &str, data: &[u8]) -> Result<(), String> {
        self.ensure_live(id)?;
        self.tx
            .send(Op::Write {
                id: id.to_string(),
                data: data.to_vec(),
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    /// Queue a resize on the worker. `ResizePseudoConsole` must not run on the
    /// UI thread: it can block while the reader is in a ConPTY read.
    pub fn resize(&self, id: &str, cols: u16, rows: u16) -> Result<(), String> {
        self.ensure_live(id)?;
        self.tx
            .send(Op::Resize {
                id: id.to_string(),
                cols,
                rows,
            })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    /// Request the worker to kill the session. Does not wait for the child
    /// (`WaitForSingleObject` with `INFINITE` used to run on the caller).
    pub fn close(&self, id: &str) -> Result<(), String> {
        if let Ok(mut live) = self.live.lock() {
            live.remove(id);
        }
        self.tx
            .send(Op::Close { id: id.to_string() })
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    fn ensure_live(&self, id: &str) -> Result<(), String> {
        let live = self.live.lock().map_err(|e| e.to_string())?;
        if live.contains(id) {
            Ok(())
        } else {
            Err(format!("unknown pty session {id}"))
        }
    }
}

fn worker_loop(rx: Receiver<Op>, live: Arc<Mutex<HashSet<String>>>) {
    let mut sessions: HashMap<String, Arc<PtySession>> = HashMap::new();
    while let Ok(op) = rx.recv() {
        match op {
            Op::Open {
                cwd,
                cols,
                rows,
                on_data,
                on_exit,
                on_done,
            } => match open_session(&cwd, cols, rows, on_data, on_exit) {
                Ok((id, session)) => {
                    if let Ok(mut ids) = live.lock() {
                        ids.insert(id.clone());
                    }
                    sessions.insert(id.clone(), session);
                    on_done(Ok(id));
                }
                Err(e) => on_done(Err(e)),
            },
            Op::Write { id, data } => {
                if let Some(session) = sessions.get(&id) {
                    if let Ok(mut w) = session.writer.lock() {
                        let _ = w.write_all(&data);
                        let _ = w.flush();
                    }
                }
            }
            Op::Resize { id, cols, rows } => {
                if let Some(session) = sessions.get(&id) {
                    if let Ok(master) = session.master.lock() {
                        let _ = master.resize(PtySize {
                            rows: rows.max(5),
                            cols: cols.max(20),
                            pixel_width: 0,
                            pixel_height: 0,
                        });
                    }
                }
            }
            Op::Close { id } => {
                if let Ok(mut ids) = live.lock() {
                    ids.remove(&id);
                }
                if let Some(session) = sessions.remove(&id) {
                    // Reap off this worker too: `child.wait()` and
                    // `ClosePseudoConsole` (on drop) can both block.
                    let _ =
                        thread::Builder::new()
                            .name(format!("pty-reap-{id}"))
                            .spawn(move || {
                                if let Ok(mut child) = session.child.lock() {
                                    let _ = child.kill();
                                    let _ = child.wait();
                                }
                                drop(session);
                            });
                }
            }
        }
    }
}

fn open_session(
    cwd: &Option<String>,
    cols: u16,
    rows: u16,
    on_data: Box<dyn Fn(&[u8]) + Send>,
    on_exit: Box<dyn FnOnce() + Send>,
) -> Result<(String, Arc<PtySession>), String> {
    let cols = cols.max(20);
    let rows = rows.max(5);
    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| format!("openpty: {e}"))?;

    let (shell, args) = pick_shell();
    let mut cmd = CommandBuilder::new(&shell);
    for a in &args {
        cmd.arg(a);
    }
    if let Some(dir) = cwd.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        if Path::new(dir).is_dir() {
            cmd.cwd(dir);
        }
    }

    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| format!("spawn {shell}: {e}"))?;
    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| format!("clone reader: {e}"))?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|e| format!("take writer: {e}"))?;

    let id = format!("pty-{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
    let session = Arc::new(PtySession {
        writer: Mutex::new(writer),
        master: Mutex::new(pair.master),
        child: Mutex::new(child),
    });

    let reader_id = id.clone();
    thread::Builder::new()
        .name(format!("pty-{reader_id}"))
        .spawn(move || {
            let mut buf = [0u8; 8192];
            loop {
                match reader.read(&mut buf) {
                    Ok(0) => break,
                    Ok(n) => on_data(&buf[..n]),
                    Err(_) => break,
                }
            }
            on_exit();
        })
        .map_err(|e| format!("pty reader: {e}"))?;
    Ok((id, session))
}

/// Resolve the interactive shell. Unix never launches a WSL-interop `powershell.exe`.
pub fn pick_shell() -> (String, Vec<String>) {
    #[cfg(target_os = "windows")]
    {
        let mut candidates: Vec<String> = Vec::new();
        if let Ok(pf) = std::env::var("ProgramFiles") {
            candidates.push(format!(r"{pf}\PowerShell\7\pwsh.exe"));
            candidates.push(format!(r"{pf}\PowerShell\7-preview\pwsh.exe"));
        }
        if let Ok(pf86) = std::env::var("ProgramFiles(x86)") {
            candidates.push(format!(r"{pf86}\PowerShell\7\pwsh.exe"));
        }
        if let Ok(sys) = std::env::var("SystemRoot") {
            candidates.push(format!(
                r"{sys}\System32\WindowsPowerShell\v1.0\powershell.exe"
            ));
            candidates.push(format!(
                r"{sys}\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
            ));
        }
        candidates.push(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe".into());
        for c in &candidates {
            if Path::new(c).is_file() {
                return (c.clone(), vec!["-NoLogo".into(), "-NoExit".into()]);
            }
        }
        return (
            "powershell.exe".into(),
            vec!["-NoLogo".into(), "-NoExit".into()],
        );
    }

    #[cfg(not(target_os = "windows"))]
    {
        if let Ok(shell) = std::env::var("SHELL") {
            let s = shell.trim();
            let low = s.to_ascii_lowercase();
            let windows_interop = low.ends_with(".exe")
                || low.contains("/mnt/")
                || low.contains(":\\")
                || low.contains("//wsl");
            if !s.is_empty() && Path::new(s).is_file() && !windows_interop {
                return (s.to_string(), vec!["-i".into()]);
            }
        }
        for c in [
            "/bin/bash",
            "/usr/bin/bash",
            "/bin/zsh",
            "/usr/bin/zsh",
            "/bin/fish",
            "/usr/bin/fish",
            "/bin/sh",
        ] {
            if Path::new(c).is_file() {
                return (c.to_string(), vec!["-i".into()]);
            }
        }
        ("/bin/sh".into(), Vec::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    use std::time::{Duration, Instant};

    #[test]
    fn pick_shell_is_a_real_file() {
        let (shell, args) = pick_shell();
        assert!(Path::new(&shell).is_file(), "{shell}");
        // Windows picks PowerShell; everywhere else a shell is never an .exe.
        assert_eq!(shell.to_ascii_lowercase().ends_with(".exe"), cfg!(windows), "{shell}");
        if shell != "/bin/sh" || args.is_empty() {
            assert!(args.iter().any(|a| a == "-i" || a == "-NoLogo"));
        }
    }

    #[test]
    fn unknown_session_errors_and_a_shell_echoes() {
        let host = PtyHost::new();
        let err = host.write("pty-missing", "x").unwrap_err();
        assert!(err.contains("unknown pty session"), "{err}");

        let (tx, rx) = mpsc::channel::<Vec<u8>>();
        let id = host
            .open(
                None,
                80,
                24,
                move |bytes| {
                    let _ = tx.send(bytes.to_vec());
                },
                || {},
            )
            .expect("pty open");
        host.write(&id, "printf 'remedy-pty-ok\\n'\n")
            .expect("write");
        let mut got = String::new();
        let deadline = std::time::Instant::now() + Duration::from_secs(4);
        while std::time::Instant::now() < deadline && !got.contains("remedy-pty-ok") {
            match rx.recv_timeout(Duration::from_millis(200)) {
                Ok(chunk) => {
                    got.push_str(&String::from_utf8_lossy(&chunk));
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
        host.close(&id).expect("close");
        assert!(
            got.contains("remedy-pty-ok"),
            "shell did not echo, saw {got:?}"
        );
        let err = host.write(&id, "x").unwrap_err();
        assert!(err.contains("unknown pty session"), "{err}");
    }

    #[test]
    fn close_returns_without_waiting_for_the_child() {
        let host = PtyHost::new();
        let id = host.open(None, 80, 24, |_| {}, || {}).expect("pty open");
        let start = Instant::now();
        host.close(&id).expect("close");
        assert!(
            start.elapsed() < Duration::from_millis(250),
            "close blocked the caller for {:?}",
            start.elapsed()
        );
        // A second close is a no-op (same as before) and must also stay quick.
        let start = Instant::now();
        host.close(&id).expect("close again");
        assert!(start.elapsed() < Duration::from_millis(250));
    }
}
