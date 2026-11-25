//! Desktop host commands the slides call: open a path, open a terminal,
//! save or open a text file, pick a folder.
//!
//! Network paths are refused before any filesystem access (the same rule as
//! `open_path` in the Tauri host). Dialogs go through RemedyUI's file dialog,
//! bound to the live window with [`bind_host`].

use remedy_ui::host::FileDialog;
use remedy_ui::{App, Window};
use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};

thread_local! {
    static HOST: RefCell<Option<(App, Window)>> = const { RefCell::new(None) };
}

/// Remember the window file dialogs should stay modal to.
pub fn bind_host(app: &App, window: &Window) {
    HOST.with(|h| *h.borrow_mut() = Some((app.clone(), window.clone())));
}

fn current_host() -> Option<(App, Window)> {
    HOST.with(|h| h.borrow().clone())
}

pub fn current_window() -> Option<Window> {
    current_host().map(|(_, w)| w)
}

pub fn current_app() -> Option<App> {
    current_host().map(|(a, _)| a).or_else(crate::settings::host)
}

/// Empty names become `remedy-export.txt`. The characters a filename cannot hold become `_`.
pub fn sanitize_export_filename(default_name: &str) -> String {
    if default_name.trim().is_empty() {
        return "remedy-export.txt".to_string();
    }
    default_name
        .chars()
        .map(|c| match c {
            '<' | '>' | ':' | '"' | '/' | '\\' | '|' | '?' | '*' => '_',
            c if c.is_control() => '_',
            c => c,
        })
        .collect()
}

/// Native save dialog, then write `contents`. `Ok(None)` is a cancel.
pub fn save_text_file(
    default_name: String,
    contents: String,
    done: impl FnOnce(Result<Option<String>, String>) + 'static,
) {
    let name = sanitize_export_filename(&default_name);
    let Some((app, window)) = current_host() else {
        done(Err("no window for save dialog".into()));
        return;
    };
    let mut dialog = FileDialog::default();
    dialog.title = "Export Remedy session".into();
    dialog.save = true;
    dialog.file_name = Some(name);
    dialog.filters = vec![
        ("Text".into(), vec!["txt".into()]),
        ("Markdown".into(), vec!["md".into()]),
        ("All files".into(), vec!["*".into()]),
    ];
    app.file_dialog(Some(&window), dialog, move |paths| {
        let Some(path) = paths.into_iter().next() else {
            done(Ok(None));
            return;
        };
        match std::fs::write(&path, contents.as_bytes()) {
            Ok(()) => done(Ok(Some(path.to_string_lossy().to_string()))),
            Err(e) => done(Err(format!("write failed: {e}"))),
        }
    });
}

/// Native open dialog. Files over 8 MB are refused. `Ok(None)` is a cancel.
#[allow(dead_code)]
pub fn open_text_file(done: impl FnOnce(Result<Option<OpenedText>, String>) + 'static) {
    let Some((app, window)) = current_host() else {
        done(Err("no window for open dialog".into()));
        return;
    };
    let mut dialog = FileDialog::default();
    dialog.title = "Import Remedy session".into();
    dialog.filters = vec![
        ("Session export".into(), vec!["txt".into(), "md".into()]),
        ("All files".into(), vec!["*".into()]),
    ];
    app.file_dialog(Some(&window), dialog, move |paths| {
        let Some(path) = paths.into_iter().next() else {
            done(Ok(None));
            return;
        };
        let meta = match std::fs::metadata(&path) {
            Ok(m) => m,
            Err(e) => {
                done(Err(format!("stat failed: {e}")));
                return;
            }
        };
        if meta.len() > 8_000_000 {
            done(Err(
                "File too large (8 MB max). Export without embedded images or split the session."
                    .into(),
            ));
            return;
        }
        match std::fs::read_to_string(&path) {
            Ok(text) => {
                let name = path
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("import.txt")
                    .to_string();
                done(Ok(Some(OpenedText {
                    path: path.to_string_lossy().to_string(),
                    text,
                    name,
                })));
            }
            Err(e) => done(Err(format!("read failed: {e}"))),
        }
    });
}

/// A text file chosen in [`open_text_file`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct OpenedText {
    pub path: String,
    pub text: String,
    pub name: String,
}

/// Native folder picker. `Ok(None)` is a cancel.
#[allow(dead_code)]
pub fn pick_folder(done: impl FnOnce(Result<Option<String>, String>) + 'static) {
    let Some((app, window)) = current_host() else {
        done(Err("no window for folder picker".into()));
        return;
    };
    let mut dialog = FileDialog::default();
    dialog.title = "Select project folder".into();
    dialog.folders = true;
    app.file_dialog(Some(&window), dialog, move |paths| {
        done(Ok(paths
            .into_iter()
            .next()
            .map(|p| p.to_string_lossy().to_string())))
    });
}

/// UNC shares (`\\host\share`, `//host/share`) and Win32 device namespaces
/// (`\\?\`, `\\.\`), also behind a `file:` prefix.
pub fn is_network_path(raw: &str) -> bool {
    let trimmed = raw
        .trim()
        .trim_matches(|c| c == '<' || c == '>' || c == '"' || c == '\'');
    let mut v = trimmed;
    if v.len() >= 5 && v[..5].eq_ignore_ascii_case("file:") {
        let rest = &v[5..];
        let body = rest.trim_start_matches(['/', '\\']);
        let lead = rest.len() - body.len();
        let drive = body.len() >= 2
            && body.as_bytes()[1] == b':'
            && body.as_bytes()[0].is_ascii_alphabetic();
        if (lead == 2 || lead >= 4) && !drive {
            return true;
        }
        v = body;
    }
    let b = v.as_bytes();
    b.len() >= 2 && (b[0] == b'\\' || b[0] == b'/') && (b[1] == b'\\' || b[1] == b'/')
}

/// AppleScript that opens Terminal at `dir`. The shell sees one single-quoted word.
pub fn macos_terminal_cd_script(dir: &str) -> String {
    let shell = format!("cd '{}'", dir.replace('\'', "'\\''"));
    let applescript = shell.replace('\\', "\\\\").replace('"', "\\\"");
    format!("tell application \"Terminal\" to do script \"{applescript}\"")
}

/// Open a folder, or reveal a file in its parent. Never launches the file.
/// Refuses network paths before touching the filesystem.
pub fn open_path(path: String) -> Result<String, String> {
    if is_network_path(&path) {
        return Err("network paths are not opened from Remedy".into());
    }
    let p = PathBuf::from(path.trim());
    if !p.exists() {
        return Err(format!("path not found: {}", p.display()));
    }
    #[cfg(target_os = "windows")]
    {
        return open_path_windows(&p);
    }
    #[cfg(target_os = "macos")]
    {
        return open_path_macos(&p);
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return open_path_unix(&p);
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = p;
        Err("open_path unsupported on this platform".into())
    }
}

/// Open a file or folder with the OS default app (not just reveal it).
/// Network paths are refused, same as [`open_path`].
pub fn launch_path(path: String) -> Result<String, String> {
    if is_network_path(&path) {
        return Err("network paths are not opened from Remedy".into());
    }
    let p = PathBuf::from(path.trim());
    if !p.exists() {
        return Err(format!("path not found: {}", p.display()));
    }
    #[cfg(target_os = "windows")]
    {
        return launch_path_windows(&p);
    }
    #[cfg(target_os = "macos")]
    {
        return launch_path_macos(&p);
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return launch_path_unix(&p);
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = p;
        Err("launch_path unsupported on this platform".into())
    }
}

#[cfg(target_os = "windows")]
fn launch_path_windows(p: &Path) -> Result<String, String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    // `start` is a cmd builtin. The empty quoted title is required so paths
    // with spaces are not taken as the window title.
    let status = Command::new("cmd")
        .args(["/c", "start", "", &p.display().to_string()])
        .creation_flags(CREATE_NO_WINDOW)
        .status()
        .map_err(|e| format!("open: {e}"))?;
    let _ = status;
    Ok(format!("Opened {}", p.display()))
}

#[cfg(target_os = "macos")]
fn launch_path_macos(p: &Path) -> Result<String, String> {
    let status = Command::new("open")
        .arg(p)
        .status()
        .map_err(|e| format!("open: {e}"))?;
    if status.success() {
        Ok(format!("Opened {}", p.display()))
    } else {
        Err(format!("open failed: {status}"))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
fn launch_path_unix(p: &Path) -> Result<String, String> {
    let status = Command::new("xdg-open")
        .arg(p)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("open: {e}"))?;
    if status.success() {
        Ok(format!("Opened {}", p.display()))
    } else {
        Err(format!("open failed: {status}"))
    }
}

#[cfg(target_os = "windows")]
fn open_path_windows(p: &Path) -> Result<String, String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let status = if p.is_dir() {
        Command::new("explorer.exe")
            .arg(p.as_os_str())
            .creation_flags(CREATE_NO_WINDOW)
            .status()
    } else {
        let arg = format!("/select,{}", p.display());
        Command::new("explorer.exe")
            .arg(arg)
            .creation_flags(CREATE_NO_WINDOW)
            .status()
    }
    .map_err(|e| format!("open_path: {e}"))?;
    let _ = status;
    Ok(format!("Opened {}", p.display()))
}

#[cfg(target_os = "macos")]
fn open_path_macos(p: &Path) -> Result<String, String> {
    let mut c = Command::new("open");
    if !p.is_dir() {
        c.arg("-R");
    }
    let status = c.arg(p).status().map_err(|e| format!("open_path: {e}"))?;
    if status.success() {
        Ok(format!("Opened {}", p.display()))
    } else {
        Err(format!("open_path failed: {status}"))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
fn open_path_unix(p: &Path) -> Result<String, String> {
    let target = if p.is_dir() {
        p.to_path_buf()
    } else {
        p.parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| p.to_path_buf())
    };
    let child = Command::new("xdg-open")
        .arg(&target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("open_path: {e}"))?;
    reap_detached(child);
    Ok(format!("Opened {}", target.display()))
}

#[cfg(unix)]
fn reap_detached(child: Child) {
    std::thread::spawn(move || {
        let mut c = child;
        let _ = c.wait();
    });
}

/// Open a host terminal at `cwd`. Tests must not call this: it spawns a terminal emulator.
#[allow(dead_code)]
pub fn open_terminal(cwd: Option<String>) -> Result<String, String> {
    let dir = cwd
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let dir_s = dir.to_string_lossy().to_string();

    #[cfg(target_os = "windows")]
    {
        return open_terminal_windows(&dir_s);
    }
    #[cfg(target_os = "macos")]
    {
        return open_terminal_macos(&dir_s);
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        return open_terminal_unix(&dir_s);
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = dir_s;
        Err("open_terminal unsupported on this platform".into())
    }
}

#[cfg(target_os = "windows")]
fn open_terminal_windows(dir_s: &str) -> Result<String, String> {
    use std::os::windows::process::CommandExt;
    const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;
    let cd_cmd = format!("Set-Location -LiteralPath '{}'", dir_s.replace('\'', "''"));
    for (exe, label) in [
        ("pwsh.exe", "PowerShell 7+"),
        ("powershell.exe", "Windows PowerShell"),
    ] {
        let mut c = Command::new(exe);
        c.args(["-NoExit", "-NoLogo", "-Command", &cd_cmd]);
        c.creation_flags(CREATE_NEW_CONSOLE);
        if c.spawn().is_ok() {
            return Ok(format!("Opened {label} in {dir_s}"));
        }
    }
    if Command::new("wt.exe")
        .args(["-d", dir_s, "powershell.exe", "-NoExit", "-NoLogo"])
        .spawn()
        .is_ok()
    {
        return Ok(format!("Opened Windows Terminal (PowerShell) in {dir_s}"));
    }
    let status = Command::new("cmd.exe")
        .args([
            "/c",
            "start",
            "cmd.exe",
            "/k",
            &format!("cd /d \"{dir_s}\""),
        ])
        .status()
        .map_err(|e| format!("open terminal failed: {e}"))?;
    if status.success() {
        Ok(format!("Opened cmd in {dir_s}"))
    } else {
        Err(format!("Could not open a terminal (exit {status})"))
    }
}

#[cfg(target_os = "macos")]
fn open_terminal_macos(dir_s: &str) -> Result<String, String> {
    let script = macos_terminal_cd_script(dir_s);
    let status = Command::new("osascript")
        .args(["-e", &script])
        .status()
        .map_err(|e| format!("open terminal failed: {e}"))?;
    if status.success() {
        Ok(format!("Opened Terminal in {dir_s}"))
    } else {
        Err(format!("osascript failed: {status}"))
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
fn open_terminal_unix(dir_s: &str) -> Result<String, String> {
    let gnome = ["--working-directory", dir_s];
    let konsole = ["--workdir", dir_s];
    let safe_shell = safe_unix_shell();
    let xterm_cmd = format!(
        "cd {} && exec {} -l",
        sh_single_quote(dir_s),
        sh_single_quote(&safe_shell)
    );
    let xterm = ["-e", "sh", "-lc", xterm_cmd.as_str()];
    let terms: &[(&str, &[&str])] = &[
        ("gnome-terminal", &gnome),
        ("xfce4-terminal", &gnome),
        ("konsole", &konsole),
        ("xterm", &xterm),
    ];
    for (term, args) in terms {
        if Command::new(term).args(*args).spawn().is_ok() {
            return Ok(format!("Opened {term} in {dir_s}"));
        }
    }
    if linux_env_is_wslg() {
        if let Some(ps) = wslg_powershell_exe() {
            let win = wslg_windows_path(Path::new(dir_s));
            let loc = win.to_string_lossy().replace('\'', "''");
            let cmd = format!("Set-Location -LiteralPath '{loc}'");
            if Command::new(&ps)
                .args(["-NoLogo", "-NoExit", "-Command", &cmd])
                .spawn()
                .is_ok()
            {
                return Ok(format!("Opened Windows PowerShell in {dir_s}"));
            }
        }
    }
    Err("No terminal emulator found".into())
}

fn sh_single_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// `$SHELL` when it is a real file and not a WSL interop path, else bash.
pub fn safe_unix_shell() -> String {
    let raw = std::env::var("SHELL").unwrap_or_default();
    let s = raw.trim();
    let low = s.to_ascii_lowercase();
    let interop = low.ends_with(".exe")
        || low.contains("/mnt/")
        || low.contains(":\\")
        || low.contains("//wsl");
    if !s.is_empty() && Path::new(s).is_file() && !interop {
        s.to_string()
    } else {
        "/bin/bash".to_string()
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
fn linux_env_is_wslg() -> bool {
    std::env::var_os("WSL_DISTRO_NAME").is_some()
        || std::env::var_os("WSL_INTEROP").is_some()
        || Path::new("/mnt/wslg").exists()
}

#[cfg(all(unix, not(target_os = "macos")))]
fn wslg_powershell_exe() -> Option<PathBuf> {
    let mut cands: Vec<PathBuf> = Vec::new();
    if let Ok(root) = std::env::var("SYSTEMROOT") {
        cands.push(PathBuf::from(root).join("System32/WindowsPowerShell/v1.0/powershell.exe"));
    }
    for letter in b'c'..=b'z' {
        cands.push(PathBuf::from(format!(
            "/mnt/{}/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            letter as char
        )));
    }
    cands.into_iter().find(|p| p.is_file())
}

#[cfg(all(unix, not(target_os = "macos")))]
fn wslg_windows_path(p: &Path) -> PathBuf {
    if let Ok(out) = Command::new("wslpath").args(["-w"]).arg(p).output() {
        if out.status.success() {
            let s = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !s.is_empty() {
                return PathBuf::from(s);
            }
        }
    }
    p.to_path_buf()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn network_paths_are_refused_before_any_filesystem_access() {
        for p in [
            r"\\attacker.example\share\x.png",
            "//attacker.example/share/x.png",
            r"\\?\UNC\attacker.example\share\x.png",
            r"\\.\pipe\x",
            "file://attacker.example/share/x.png",
            "file:////attacker.example/share/x.png",
            r"file:\\attacker.example\share\x.png",
            r"<\\attacker.example\share\x.png>",
        ] {
            assert!(is_network_path(p), "{p} should be refused");
            let err = open_path(p.to_string()).unwrap_err();
            assert!(err.contains("network paths"), "{p}: {err}");
        }
        for p in [
            r"C:\Users\me\a.png",
            "file:///C:/Users/me/a.png",
            "/home/me/a.png",
            "assets/a.png",
        ] {
            assert!(!is_network_path(p), "{p} is local");
        }
    }

    fn shell_from_script(script: &str) -> String {
        let prefix = "tell application \"Terminal\" to do script \"";
        assert!(
            script.starts_with(prefix) && script.ends_with('"'),
            "{script}"
        );
        let inner = &script[prefix.len()..script.len() - 1];
        let mut out = String::new();
        let mut chars = inner.chars();
        while let Some(c) = chars.next() {
            if c == '\\' {
                out.push(chars.next().expect("dangling escape"));
            } else {
                assert!(
                    c != '"',
                    "unescaped quote ends the AppleScript string early: {script}"
                );
                out.push(c);
            }
        }
        out
    }

    #[test]
    fn macos_terminal_script_keeps_folder_names_literal() {
        for dir in [
            "/Users/me/proj",
            "/Users/me/x$(touch /tmp/pwned)",
            "/Users/me/x`id`",
            "/Users/me/it's \"quoted\"",
            r"/Users/me/back\slash",
        ] {
            let shell = shell_from_script(&macos_terminal_cd_script(dir));
            let expected = format!("cd '{}'", dir.replace('\'', "'\\''"));
            assert_eq!(shell, expected, "dir={dir}");
            assert!(
                !shell.contains("cd \""),
                "double-quoted cd lets $(…) run: {shell}"
            );
        }
    }

    #[test]
    fn export_names_drop_reserved_characters() {
        assert_eq!(sanitize_export_filename("  "), "remedy-export.txt");
        assert_eq!(
            sanitize_export_filename("a<b>c:d\"e/f\\g|h?i*j"),
            "a_b_c_d_e_f_g_h_i_j"
        );
        assert_eq!(
            sanitize_export_filename("scratch-global.md"),
            "scratch-global.md"
        );
    }
}
