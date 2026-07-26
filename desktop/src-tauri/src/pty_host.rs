//! In-app PowerShell via ConPTY (portable-pty).

use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use tauri::{AppHandle, Emitter, State};

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    _child: Box<dyn portable_pty::Child + Send + Sync>,
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

fn pick_shell() -> (&'static str, Vec<&'static str>) {
    // Prefer PowerShell 7+, then Windows PowerShell 5.1
    if which_exists("pwsh.exe") {
        return ("pwsh.exe", vec!["-NoLogo", "-NoExit"]);
    }
    ("powershell.exe", vec!["-NoLogo", "-NoExit"])
}

fn which_exists(name: &str) -> bool {
    std::process::Command::new("where")
        .arg(name)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
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
    let mut cmd = CommandBuilder::new(shell);
    for a in args {
        cmd.arg(a);
    }
    if let Some(dir) = cwd.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        cmd.cwd(dir);
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
        _child: child,
    });

    {
        let mut map = state.sessions.lock().map_err(|e| e.to_string())?;
        map.insert(id.clone(), Arc::clone(&session));
    }

    let app2 = app.clone();
    let id2 = id.clone();
    let state_flag = Arc::clone(&session);
    thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    // Lossy UTF-8 is fine for terminal output
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
        drop(state_flag);
    });

    Ok(id)
}

#[tauri::command]
pub fn pty_write(state: State<'_, PtyState>, id: String, data: String) -> Result<(), String> {
    let map = state.sessions.lock().map_err(|e| e.to_string())?;
    let session = map
        .get(&id)
        .ok_or_else(|| format!("unknown pty session {id}"))?;
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
    let map = state.sessions.lock().map_err(|e| e.to_string())?;
    let session = map
        .get(&id)
        .ok_or_else(|| format!("unknown pty session {id}"))?;
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
    let mut map = state.sessions.lock().map_err(|e| e.to_string())?;
    map.remove(&id);
    Ok(())
}
