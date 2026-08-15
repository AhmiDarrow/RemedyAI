//! In-app terminal via portable-pty (ConPTY on Windows, Unix PTY elsewhere).

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, Emitter, State};

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn portable_pty::Child + Send + Sync>>,
}

pub struct PtyState {
    sessions: Mutex<HashMap<String, Arc<PtySession>>>,
}

impl Default for PtyState {
    fn default() -> Self {
        Self {
            sessions: Mutex::new(HashMap::new()),
        }
    }
}

/// Resolve the interactive shell for this OS.
/// Windows: PowerShell (absolute path — GUI PATH is unreliable).
/// Unix: `$SHELL`, then bash/zsh/sh. Never launch `powershell.exe` via WSL interop.
fn pick_shell() -> (String, Vec<String>) {
    #[cfg(target_os = "windows")]
    {
        let mut candidates: Vec<String> = Vec::new();

        // PowerShell 7+ common install locations
        if let Ok(pf) = std::env::var("ProgramFiles") {
            candidates.push(format!(r"{pf}\PowerShell\7\pwsh.exe"));
            candidates.push(format!(r"{pf}\PowerShell\7-preview\pwsh.exe"));
        }
        if let Ok(pf86) = std::env::var("ProgramFiles(x86)") {
            candidates.push(format!(r"{pf86}\PowerShell\7\pwsh.exe"));
        }
        // Windows PowerShell 5.1 (always present on modern Windows)
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
                log::info!("pty: using shell {c}");
                return (
                    c.clone(),
                    vec!["-NoLogo".into(), "-NoExit".into()],
                );
            }
        }

        // Last resort — PATH lookup (may fail in packaged GUI)
        log::warn!("pty: absolute PowerShell path not found; trying powershell.exe on PATH");
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
            // WSL profiles sometimes point $SHELL at /mnt/c/.../powershell.exe
            // — interop hangs the in-app terminal.
            let windows_interop = low.ends_with(".exe")
                || low.contains("/mnt/")
                || low.contains(":\\")
                || low.contains("//wsl");
            if !s.is_empty() && Path::new(s).is_file() && !windows_interop {
                log::info!("pty: using $SHELL {s}");
                return (s.to_string(), vec!["-i".into()]);
            }
            if windows_interop {
                log::warn!("pty: ignoring Windows-interop $SHELL {s}");
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
                log::info!("pty: using shell {c}");
                return (c.to_string(), vec!["-i".into()]);
            }
        }
        log::warn!("pty: no known Unix shell; falling back to /bin/sh");
        ("/bin/sh".into(), Vec::new())
    }
}

#[tauri::command]
pub fn pty_open(
    app: AppHandle,
    state: State<'_, PtyState>,
    cwd: Option<String>,
    cols: Option<u16>,
    rows: Option<u16>,
) -> Result<String, String> {
    let cols = cols.unwrap_or(100).max(20);
    let rows = rows.unwrap_or(28).max(5);
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
        } else {
            log::warn!("pty: cwd not a directory, ignoring: {dir}");
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

    {
        let mut map = state.sessions.lock().map_err(|e| e.to_string())?;
        map.insert(id.clone(), Arc::clone(&session));
    }

    let app2 = app.clone();
    let id2 = id.clone();
    thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let data = String::from_utf8_lossy(&buf[..n]).to_string();
                    let _ = app2.emit(
                        "pty-data",
                        serde_json::json!({ "id": id2, "data": data }),
                    );
                }
                Err(_) => break,
            }
        }
        let _ = app2.emit("pty-exit", serde_json::json!({ "id": id2 }));
    });

    log::info!("pty_open: {id} shell={shell} cols={cols} rows={rows}");
    Ok(id)
}

#[tauri::command]
pub fn pty_write(state: State<'_, PtyState>, id: String, data: String) -> Result<(), String> {
    let session = {
        let map = state.sessions.lock().map_err(|e| e.to_string())?;
        map.get(&id)
            .cloned()
            .ok_or_else(|| format!("unknown pty session {id}"))?
    };
    let mut w = session.writer.lock().map_err(|e| e.to_string())?;
    w.write_all(data.as_bytes())
        .map_err(|e| format!("pty write: {e}"))?;
    w.flush().map_err(|e| format!("pty flush: {e}"))?;
    Ok(())
}

#[tauri::command]
pub fn pty_resize(
    state: State<'_, PtyState>,
    id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    let session = {
        let map = state.sessions.lock().map_err(|e| e.to_string())?;
        map.get(&id)
            .cloned()
            .ok_or_else(|| format!("unknown pty session {id}"))?
    };
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

#[tauri::command]
pub fn pty_close(state: State<'_, PtyState>, id: String) -> Result<(), String> {
    let session = {
        let mut map = state.sessions.lock().map_err(|e| e.to_string())?;
        map.remove(&id)
    };
    if let Some(session) = session {
        // Kill outside the map lock — wait() must not block resize/write.
        if let Ok(mut child) = session.child.lock() {
            let _ = child.kill();
            let _ = child.wait();
        }
        log::info!("pty_close: {id}");
    }
    Ok(())
}
