//! In-app terminal via portable-pty (ConPTY on Windows, a Unix PTY elsewhere).
//! The same shell choice as `desktop/src-tauri/src/pty_host.rs`. The widget does
//! not start a process; this host does, and the caller feeds bytes to the screen.

use portable_pty::{CommandBuilder, MasterPty, PtySize, native_pty_system};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn portable_pty::Child + Send + Sync>>,
}

/// Live PTY sessions. Cheap to clone; sessions live until [`PtyHost::close`].
#[derive(Clone, Default)]
pub struct PtyHost {
    sessions: Arc<Mutex<HashMap<String, Arc<PtySession>>>>,
}

impl PtyHost {
    pub fn new() -> Self {
        Self::default()
    }

    /// Spawn a shell. `on_data` runs on the reader thread. `on_exit` runs on that
    /// thread too, after the last byte (hop status updates back to the UI).
    pub fn open(
        &self,
        cwd: Option<&str>,
        cols: u16,
        rows: u16,
        on_data: impl Fn(&[u8]) + Send + 'static,
        on_exit: impl Fn() + Send + 'static,
    ) -> Result<String, String> {
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
        if let Some(dir) = cwd.map(str::trim).filter(|s| !s.is_empty()) {
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
        self.sessions
            .lock()
            .map_err(|e| e.to_string())?
            .insert(id.clone(), Arc::clone(&session));

        thread::Builder::new()
            .name(format!("pty-{id}"))
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
        Ok(id)
    }

    pub fn write(&self, id: &str, data: &str) -> Result<(), String> {
        self.write_bytes(id, data.as_bytes())
    }

    /// Write raw PTY input. Keystrokes from the terminal widget are bytes, not text.
    pub fn write_bytes(&self, id: &str, data: &[u8]) -> Result<(), String> {
        let session = self.session(id)?;
        let mut w = session.writer.lock().map_err(|e| e.to_string())?;
        w.write_all(data).map_err(|e| format!("pty write: {e}"))?;
        w.flush().map_err(|e| format!("pty flush: {e}"))?;
        Ok(())
    }

    pub fn resize(&self, id: &str, cols: u16, rows: u16) -> Result<(), String> {
        let session = self.session(id)?;
        let master = session.master.lock().map_err(|e| e.to_string())?;
        master
            .resize(PtySize {
                rows: rows.max(5),
                cols: cols.max(20),
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|e| format!("pty resize: {e}"))?;
        Ok(())
    }

    pub fn close(&self, id: &str) -> Result<(), String> {
        let session = self
            .sessions
            .lock()
            .map_err(|e| e.to_string())?
            .remove(id);
        if let Some(session) = session {
            if let Ok(mut child) = session.child.lock() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        Ok(())
    }

    fn session(&self, id: &str) -> Result<Arc<PtySession>, String> {
        self.sessions
            .lock()
            .map_err(|e| e.to_string())?
            .get(id)
            .cloned()
            .ok_or_else(|| format!("unknown pty session {id}"))
    }
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
    use std::time::Duration;

    #[test]
    fn pick_shell_is_a_real_file_on_unix() {
        let (shell, args) = pick_shell();
        assert!(Path::new(&shell).is_file(), "{shell}");
        assert!(!shell.to_ascii_lowercase().ends_with(".exe"));
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
}
