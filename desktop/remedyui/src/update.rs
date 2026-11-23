//! Signed updates for the RemedyUI shell.
//!
//! The feed is Tauri `latest.json` (`Feed::Json` from RemedyUI). The minisign
//! public key is the one already in `desktop/src-tauri/tauri.conf.json`. Do not
//! rotate it here.
//!
//! Windows installs with `/S /NCRC /UPDATE /NOAUTOLAUNCH` and optional `/D=`
//! last. The app writes `%TEMP%\RemedyDesktop-UpdaterOwnsRelaunch.flag` and a
//! hidden waiter relaunches one copy. Linux replaces `$APPIMAGE`. macOS swaps
//! the running `.app`. `~/.remedy` is not part of any of those steps.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use remedy_ui::reactive::Signal;
use remedy_ui::update::{Feed, Release, UpdateConfig};
use remedy_ui::App;

/// First RemedyUI ship. Must stay greater than the last Tauri build, 0.70.5.
pub const APP_VERSION: &str = env!("CARGO_PKG_VERSION");

pub const BUNDLE_ID: &str = "com.remedy.desktop";
pub const PRODUCT_NAME: &str = "Remedy Desktop";

pub const FEED_URL: &str =
    "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json";

/// Minisign public key, the `RW` line. `Trust::Minisign` wants this, not the blob.
pub const MINISIGN_PUBKEY: &str = "RWTgvKzjXQwB1gQ5itS9iH5TjbPetoDAi4OLolIxjPrNnmRyd3qJJ4u6";

/// Same key, Tauri's base64 wrapping (`untrusted comment` + the RW line).
pub const MINISIGN_PUBKEY_B64: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQ2MDEwQzVERTNBQ0JDRTAKUldUZ3ZLempYUXdCMWdRNWl0UzlpSDVUamJQZXRvREFpNE9Mb2xJeGpQck5ubVJ5ZDNxSko0dTYK";

pub const RELEASE_BASE: &str = "https://github.com/AhmiDarrow/RemedyAI/releases/download";

/// Marker the NSIS POSTINSTALL hook consumes so it does not also start the app.
pub const OWNS_RELAUNCH_FLAG: &str = "RemedyDesktop-UpdaterOwnsRelaunch.flag";

const BUSY: AtomicBool = AtomicBool::new(false);
/// Bytes received. Written on the download thread, read on the UI thread.
static PROGRESS_GOT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// Total bytes, or `u64::MAX` when the server did not say.
static PROGRESS_TOTAL: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(u64::MAX);

thread_local! {
    static OFFER: std::cell::RefCell<Option<Signal<String>>> = const { std::cell::RefCell::new(None) };
    static NOTE: std::cell::RefCell<Option<Signal<String>>> = const { std::cell::RefCell::new(None) };
    static PENDING: std::cell::RefCell<Option<Release>> = const { std::cell::RefCell::new(None) };
    static APP: std::cell::RefCell<Option<App>> = const { std::cell::RefCell::new(None) };
}

fn signal(slot: &std::cell::RefCell<Option<Signal<String>>>) -> Signal<String> {
    let mut guard = slot.borrow_mut();
    if guard.is_none() {
        *guard = Some(Signal::new(String::new()));
    }
    guard.clone().unwrap()
}

pub(crate) fn offer_signal() -> Signal<String> {
    OFFER.with(signal)
}

pub(crate) fn note_signal() -> Signal<String> {
    NOTE.with(signal)
}

/// Tests and preview hosts set `REMEDY_DESKTOP_NO_SPAWN`. Either flag skips the network.
pub fn checks_disabled() -> bool {
    flag_on("REMEDY_DESKTOP_NO_UPDATE") || flag_on("REMEDY_DESKTOP_NO_SPAWN")
}

fn flag_on(name: &str) -> bool {
    matches!(
        std::env::var(name).unwrap_or_default().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

pub fn updater_config() -> UpdateConfig {
    UpdateConfig::new(APP_VERSION, Feed::Json(FEED_URL.to_string()), MINISIGN_PUBKEY)
}

pub fn windows_setup_name(version: &str) -> String {
    format!("Remedy.Desktop_{version}_x64-setup.exe")
}

pub fn linux_appimage_name(version: &str) -> String {
    format!("Remedy.Desktop_{version}_amd64.AppImage")
}

pub fn linux_deb_name(version: &str) -> String {
    format!("Remedy.Desktop_{version}_amd64.deb")
}

pub fn macos_dmg_name(version: &str, arch: &str) -> String {
    format!("Remedy.Desktop_{version}_{arch}.dmg")
}

pub fn macos_tar_name(version: &str, arch: &str) -> String {
    format!("Remedy.Desktop_{version}_{arch}.app.tar.gz")
}

pub fn windows_download_url(version: &str) -> String {
    format!("{RELEASE_BASE}/v{version}/{}", windows_setup_name(version))
}

pub fn linux_download_url(version: &str) -> String {
    format!("{RELEASE_BASE}/v{version}/{}", linux_appimage_name(version))
}

/// macOS updater bytes live on the `runtime-v{version}` companion, same as 0.69+.
pub fn darwin_download_url(version: &str, arch: &str) -> String {
    format!("{RELEASE_BASE}/runtime-v{version}/{}", macos_tar_name(version, arch))
}

/// `/D=` is last and unquoted. NSIS ignores it in any other position.
pub fn windows_silent_args(prior_dir: Option<&str>) -> Vec<String> {
    let mut args = vec![
        "/S".to_string(),
        "/NCRC".to_string(),
        "/UPDATE".to_string(),
        "/NOAUTOLAUNCH".to_string(),
    ];
    if let Some(dir) = prior_dir.map(str::trim).filter(|s| !s.is_empty()) {
        args.push(format!("/D={dir}"));
    }
    args
}

pub fn trusted_download(url: &str) -> bool {
    let url = url.trim();
    url.starts_with("https://github.com/AhmiDarrow/RemedyAI/releases/download/")
        || url.starts_with("https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/")
}

/// `1.0.0` is newer than `0.70.5`. A leading `v` is ignored. Pre-release tags compare equal
/// on the numeric triple only; the shipped check uses RemedyUI's semver parser.
pub fn release_is_newer(candidate: &str, current: &str) -> bool {
    match (parse_triple(candidate), parse_triple(current)) {
        (Some(a), Some(b)) => a > b,
        _ => false,
    }
}

fn parse_triple(v: &str) -> Option<(u64, u64, u64)> {
    let v = v.trim().trim_start_matches('v');
    let mut parts = v.split('.');
    let major = parts.next()?.split(['-', '+']).next()?.parse().ok()?;
    let minor = parts.next()?.split(['-', '+']).next()?.parse().ok()?;
    let patch = parts.next()?.split(['-', '+']).next()?.parse().ok()?;
    Some((major, minor, patch))
}

pub fn pubkey_line_from_blob(b64: &str) -> Option<String> {
    let raw = base64_decode(b64.trim())?;
    let text = String::from_utf8(raw).ok()?;
    text.lines()
        .map(str::trim)
        .find(|l| l.starts_with("RW"))
        .map(str::to_string)
}

fn base64_decode(input: &str) -> Option<Vec<u8>> {
    const TABLE: &[u8; 128] = &{
        let mut t = [0xffu8; 128];
        let mut i = 0u8;
        while i < 26 {
            t[(b'A' + i) as usize] = i;
            t[(b'a' + i) as usize] = i + 26;
            i += 1;
        }
        i = 0;
        while i < 10 {
            t[(b'0' + i) as usize] = i + 52;
            i += 1;
        }
        t[b'+' as usize] = 62;
        t[b'/' as usize] = 63;
        t
    };
    let bytes = input.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() * 3 / 4);
    let mut buf = 0u32;
    let mut bits = 0u32;
    for &b in bytes {
        if b == b'=' || b.is_ascii_whitespace() {
            continue;
        }
        if b >= 128 || TABLE[b as usize] == 0xff {
            return None;
        }
        buf = (buf << 6) | TABLE[b as usize] as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
            buf &= (1 << bits) - 1;
        }
    }
    Some(out)
}

pub fn gzip_magic(bytes: &[u8]) -> bool {
    bytes.len() >= 2 && bytes[0] == 0x1f && bytes[1] == 0x8b
}

pub fn elf_magic(bytes: &[u8]) -> bool {
    bytes.len() >= 4 && bytes[..4] == *b"\x7fELF"
}

pub fn pe_magic(bytes: &[u8]) -> bool {
    bytes.len() >= 2 && bytes[0] == b'M' && bytes[1] == b'Z'
}

/// Stage the new AppImage beside the running one so `rename` stays on that volume.
pub fn appimage_stage_path(dest: &Path, pid: u32) -> PathBuf {
    let parent = dest.parent().unwrap_or(Path::new("."));
    let name = dest.file_name().and_then(|s| s.to_str()).unwrap_or("AppImage");
    parent.join(format!(".{name}.{pid}.partial"))
}

/// A tar entry that stays under the directory it is extracted into.
pub fn archive_entry_is_safe(entry: &str) -> bool {
    let entry = entry.trim();
    if entry.is_empty() || entry.starts_with('/') || entry.starts_with('\\') {
        return false;
    }
    if entry.as_bytes().get(1) == Some(&b':') {
        return false;
    }
    !entry.split(['/', '\\']).any(|part| part == "..")
}

/// The archive's only top-level name, which must be one `.app`.
pub fn archive_top_level_app(entries: &[String]) -> Result<String, String> {
    if entries.is_empty() {
        return Err("archive is empty".into());
    }
    let mut top: Option<String> = None;
    for entry in entries {
        if !archive_entry_is_safe(entry) {
            return Err(format!("archive entry is not safe: {entry}"));
        }
        let Some(name) = entry.split(['/', '\\']).next().filter(|n| !n.is_empty()) else {
            continue;
        };
        match &top {
            None => top = Some(name.to_string()),
            Some(have) if have == name => {}
            Some(have) => {
                return Err(format!("archive has more than one top-level entry ({have}, {name})"));
            }
        }
    }
    let top = top.ok_or_else(|| "archive has no files".to_string())?;
    if !top.ends_with(".app") {
        return Err(format!("archive top-level is {top}, expected a .app bundle"));
    }
    Ok(top)
}

pub(crate) struct WindowsWaiter<'a> {
    pub parent_pid: u32,
    pub installer: &'a str,
    pub prior_dir: &'a str,
    pub flag_path: &'a str,
    pub log_path: &'a str,
}

fn ps_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "''"))
}

/// Hidden waiter. It does not delete the per-user data directory. `/D=` is last.
pub fn windows_waiter_script(w: &WindowsWaiter<'_>) -> String {
    format!(
        r#"$ErrorActionPreference = 'Continue'
$log = {log}
$ownsFlag = {flag}
$installer = {installer}
$priorDir = {prior}
$parentPid = {pid}
$lock = Join-Path $env:TEMP 'RemedyDesktop-Update.lock'
$code = 0
function Log($m) {{
  Add-Content -LiteralPath $log -Value (("{{0:u}} {{1}}" -f (Get-Date), $m)) -ErrorAction SilentlyContinue
}}
try {{
  Add-Content -LiteralPath $log -Value (("{{0:u}} BOOT pid={{1}}" -f (Get-Date), $PID)) -ErrorAction SilentlyContinue
}} catch {{}}
# Three hosts start this script. One proceeds. A lock whose PID is gone is stale.
function Take-Lock {{
  if (Test-Path -LiteralPath $lock) {{
    $old = ''
    try {{ $old = (Get-Content -LiteralPath $lock -TotalCount 1 -ErrorAction SilentlyContinue) }} catch {{}}
    $alive = $false
    if ($old -match '^\d+$') {{
      $alive = $null -ne (Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue)
    }}
    if ($alive) {{ return $false }}
    Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue
  }}
  try {{
    $fs = [System.IO.File]::Open($lock, 'CreateNew', 'Write', 'Read')
    $bytes = [System.Text.Encoding]::ASCII.GetBytes("$PID")
    $fs.Write($bytes, 0, $bytes.Length)
    $fs.Close()
    return $true
  }} catch {{
    return $false
  }}
}}
if (-not (Take-Lock)) {{ Log 'another waiter holds the lock'; exit 0 }}
try {{
  try {{ Set-Content -LiteralPath $ownsFlag -Value 'owned_by=update_script' -Encoding ASCII }} catch {{}}
  while (Get-Process -Id $parentPid -ErrorAction SilentlyContinue) {{
    Start-Sleep -Milliseconds 400
  }}
  Start-Sleep -Seconds 1
  Get-Process -ErrorAction SilentlyContinue | Where-Object {{
    $n = $_.ProcessName
    if ($n -match '^(powershell|pwsh|cmd|wscript|cscript|schtasks)$') {{ return $false }}
    if ($n -match '^(app|remedy-desktop|Remedy Desktop)$') {{ return $true }}
    if ($_.Path -and ($_.Path -like '*\Remedy Desktop.exe' -or $_.Path -like '*\app.exe')) {{ return $true }}
    return $false
  }} | ForEach-Object {{ Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }}
  if (-not (Test-Path -LiteralPath $installer)) {{
    Log 'ERROR: installer missing'
    $code = 2
  }} else {{
    # Silent update. /UPDATE keeps user data. /NOAUTOLAUNCH plus the flag file:
    # NSIS POSTINSTALL must not start the app. This script relaunches once.
    $nsisArgs = @('/S', '/NCRC', '/UPDATE', '/NOAUTOLAUNCH')
    if ($priorDir -and (Test-Path -LiteralPath $priorDir)) {{
      $nsisArgs += "/D=$priorDir"
      Log "Using /D=$priorDir"
    }}
    try {{ Set-Content -LiteralPath $ownsFlag -Value 'owned_by=update_script' -Encoding ASCII }} catch {{}}
    Log ("Starting NSIS: $installer " + ($nsisArgs -join ' '))
    $p = Start-Process -FilePath $installer -ArgumentList $nsisArgs -PassThru -WindowStyle Hidden
    if (-not $p) {{
      Log 'ERROR: Start-Process returned null'
      $code = 3
    }} else {{
      try {{ Wait-Process -Id $p.Id -Timeout 420 -ErrorAction Stop }} catch {{ Log $_.Exception.Message }}
      $candidates = @()
      if ($priorDir) {{
        $candidates += (Join-Path $priorDir 'Remedy Desktop.exe')
      }}
      $candidates += @(
        (Join-Path $env:LOCALAPPDATA 'Remedy Desktop\Remedy Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\Remedy Desktop.exe'),
        (Join-Path $env:LOCALAPPDATA 'Remedy Desktop\app.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Remedy Desktop\app.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\remedy-desktop\Remedy Desktop.exe')
      )
      $launch = $null
      foreach ($c in $candidates) {{
        if ($c -and (Test-Path -LiteralPath $c)) {{ $launch = $c; break }}
      }}
      if ($launch) {{
        Start-Process -FilePath $launch
        try {{ Remove-Item -LiteralPath $ownsFlag -Force -ErrorAction SilentlyContinue }} catch {{}}
        $code = 0
      }} else {{
        Log 'ERROR: Remedy Desktop.exe was not found after install'
        $code = 4
      }}
    }}
  }}
}} finally {{
  try {{ Remove-Item -LiteralPath $lock -Force -ErrorAction SilentlyContinue }} catch {{}}
}}
exit $code
"#,
        log = ps_quote(w.log_path),
        flag = ps_quote(w.flag_path),
        installer = ps_quote(w.installer),
        prior = ps_quote(w.prior_dir),
        pid = w.parent_pid,
    )
}

pub fn check_feed() -> Result<Option<Release>, String> {
    if checks_disabled() {
        return Ok(None);
    }
    remedy_ui::update::check(&updater_config()).map_err(|e| e.to_string())
}

pub(crate) fn bind_app(app: &App) {
    APP.with(|slot| *slot.borrow_mut() = Some(app.clone()));
}

/// Status-bar click. A no-op when checks are disabled, so previews stay quiet.
pub(crate) fn press_offer() {
    APP.with(|slot| {
        if let Some(app) = slot.borrow().clone() {
            on_update_menu(&app);
        }
    });
}

pub(crate) fn spawn_startup_check(app: &App) {
    bind_app(app);
    if checks_disabled() || BUSY.swap(true, Ordering::SeqCst) {
        return;
    }
    let app2 = app.clone();
    app.background(check_feed, move |result| finish_check(&app2, result));
}

pub(crate) fn on_update_menu(app: &App) {
    if checks_disabled() {
        return;
    }
    if offer_signal().get().is_empty() {
        if BUSY.swap(true, Ordering::SeqCst) {
            return;
        }
        note_signal().set(String::new());
        let app2 = app.clone();
        app.background(check_feed, move |result| finish_check(&app2, result));
    } else {
        spawn_install(app);
    }
}

fn finish_check(_app: &App, result: Result<Option<Release>, String>) {
    BUSY.store(false, Ordering::SeqCst);
    match result {
        Ok(Some(release)) => {
            offer_signal().set(release.version.clone());
            note_signal().set(String::new());
            PENDING.with(|p| *p.borrow_mut() = Some(release));
        }
        Ok(None) => {
            offer_signal().set(String::new());
            PENDING.with(|p| *p.borrow_mut() = None);
        }
        Err(e) => {
            note_signal().set(trim_note(&e));
        }
    }
}

fn trim_note(e: &str) -> String {
    let mut s: String = e.chars().take(80).collect();
    if e.chars().count() > 80 {
        s.push('…');
    }
    s
}

pub(crate) fn spawn_install(app: &App) {
    if checks_disabled() || BUSY.swap(true, Ordering::SeqCst) {
        return;
    }
    let Some(release) = PENDING.with(|p| p.borrow().clone()) else {
        BUSY.store(false, Ordering::SeqCst);
        return;
    };
    store_progress(0, None);
    note_signal().set(download_note(0, None));
    watch_download(app);
    let app2 = app.clone();
    app.background(
        move || download_and_stage(&release),
        move |result| match result {
            Ok(()) => app2.quit(),
            Err(e) => {
                BUSY.store(false, Ordering::SeqCst);
                note_signal().set(trim_note(&e));
            }
        },
    );
}

/// What the status line and the tray say while bytes are arriving.
/// `total` is `None` when the server did not send a length.
pub fn download_note(received: u64, total: Option<u64>) -> String {
    match total {
        Some(total) if total > 0 => {
            let pct = (received.saturating_mul(100) / total).min(100);
            format!("Downloading {pct}%")
        }
        _ if received >= 1024 * 1024 => {
            format!("Downloading {} MB", received / (1024 * 1024))
        }
        _ => "Downloading…".to_string(),
    }
}

fn store_progress(received: u64, total: Option<u64>) {
    use std::sync::atomic::Ordering::Relaxed;
    PROGRESS_GOT.store(received, Relaxed);
    PROGRESS_TOTAL.store(total.unwrap_or(u64::MAX), Relaxed);
}

fn load_progress() -> (u64, Option<u64>) {
    use std::sync::atomic::Ordering::Relaxed;
    let got = PROGRESS_GOT.load(Relaxed);
    let total = PROGRESS_TOTAL.load(Relaxed);
    let total = (total != u64::MAX).then_some(total);
    (got, total)
}

/// Copies the download counters onto the note. The counters move on the
/// download thread; a signal can only be written on the UI thread.
fn watch_download(app: &App) {
    let app2 = app.clone();
    app.after(std::time::Duration::from_millis(200), move || {
        if !BUSY.load(Ordering::SeqCst) {
            return;
        }
        let (got, total) = load_progress();
        note_signal().set(download_note(got, total));
        watch_download(&app2);
    });
}

pub fn download_and_stage(release: &Release) -> Result<(), String> {
    if !trusted_download(&release.url) {
        return Err("Download URL is not a trusted GitHub release host".into());
    }
    let dir = std::env::temp_dir().join(format!("RemedyDesktop-Update-{}", std::process::id()));
    let file = remedy_ui::update::download(&updater_config(), release, &dir, store_progress)
        .map_err(|e| e.to_string())?;
    apply_verified(&file)
}

fn apply_verified(file: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        return apply_windows(file);
    }
    #[cfg(target_os = "linux")]
    {
        return apply_linux(file);
    }
    #[cfg(target_os = "macos")]
    {
        return apply_macos(file);
    }
    #[cfg(not(any(windows, target_os = "linux", target_os = "macos")))]
    {
        let _ = file;
        Err("In-app updates are available on Windows, Linux AppImage, and macOS.".into())
    }
}

#[cfg(windows)]
fn apply_windows(installer: &Path) -> Result<(), String> {
    let meta = std::fs::metadata(installer).map_err(|e| format!("Cannot stat installer: {e}"))?;
    if meta.len() < 512 * 1024 {
        return Err(format!(
            "Downloaded installer is too small ({} bytes) to be a real NSIS package",
            meta.len()
        ));
    }
    let mut hdr = [0u8; 2];
    {
        use std::io::Read;
        let mut f = std::fs::File::open(installer).map_err(|e| format!("Cannot open installer: {e}"))?;
        f.read_exact(&mut hdr).map_err(|e| format!("Cannot read installer header: {e}"))?;
    }
    if !pe_magic(&hdr) {
        return Err(
            "Downloaded file is not a Windows executable (missing MZ header). \
             GitHub may have returned an HTML error page."
                .into(),
        );
    }
    write_owns_flag()?;
    let log = std::env::temp_dir().join("RemedyDesktop-Update.log");
    let flag = std::env::temp_dir().join(OWNS_RELAUNCH_FLAG);
    let prior = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_string_lossy().into_owned()))
        .unwrap_or_default();
    let script = windows_waiter_script(&WindowsWaiter {
        parent_pid: std::process::id(),
        installer: &installer.to_string_lossy(),
        prior_dir: &prior,
        flag_path: &flag.to_string_lossy(),
        log_path: &log.to_string_lossy(),
    });
    let ps1 = std::env::temp_dir().join(format!("RemedyDesktop-Update-Run-{}.ps1", std::process::id()));
    std::fs::write(&ps1, script).map_err(|e| format!("Cannot write update script: {e}"))?;
    schedule_windows_waiter(&ps1)?;
    if !wait_for_boot(&log) {
        let _ = schedule_windows_waiter(&ps1);
        if !wait_for_boot(&log) {
            return Err(
                "Could not start the install step after download. Install from GitHub Releases."
                    .into(),
            );
        }
    }
    Ok(())
}

#[cfg(windows)]
fn write_owns_flag() -> Result<(), String> {
    let path = std::env::temp_dir().join(OWNS_RELAUNCH_FLAG);
    std::fs::write(
        &path,
        format!("owned_by=remedy_desktop_updater\npid={}\n", std::process::id()),
    )
    .map_err(|e| format!("Cannot write relaunch flag: {e}"))
}

#[cfg(windows)]
fn schedule_windows_waiter(ps1: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};

    const DETACHED_PROCESS: u32 = 0x00000008;
    const CREATE_NEW_PROCESS_GROUP: u32 = 0x00000200;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
    let flags_breakaway =
        DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB;
    let flags_basic = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW;
    let ps1_s = ps1.to_string_lossy().to_string();
    let mut armed = 0u32;

    let try_ps = |flags: u32| {
        Command::new("powershell.exe")
            .args([
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                &ps1_s,
            ])
            .creation_flags(flags)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
    };
    if try_ps(flags_breakaway).or_else(|_| try_ps(flags_basic)).is_ok() {
        armed += 1;
    }

    let vbs = std::env::temp_dir().join(format!("RemedyDesktop-Update-Launch-{}.vbs", std::process::id()));
    let vbs_body = format!(
        "On Error Resume Next\r\nDim sh: Set sh = CreateObject(\"WScript.Shell\")\r\nsh.Run \"powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"\"{ps1}\"\"\", 0, False\r\n",
        ps1 = ps1_s.replace('"', "\"\"")
    );
    if std::fs::write(&vbs, vbs_body).is_ok() {
        let vbs_s = vbs.to_string_lossy().to_string();
        let spawn_vbs = |flags: u32| {
            Command::new("wscript.exe")
                .args(["//B", "//Nologo", &vbs_s])
                .creation_flags(flags)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
        };
        if spawn_vbs(flags_breakaway).or_else(|_| spawn_vbs(flags_basic)).is_ok() {
            armed += 1;
        }
    }

    let task = format!("RemedyDesktopUpdate_{}", std::process::id());
    let st = Command::new("powershell.exe")
        .args([
            "-NoProfile",
            "-WindowStyle",
            "Hidden",
            "-Command",
            "(Get-Date).AddSeconds(15).ToString('HH:mm')",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .filter(|s| s.len() >= 4)
        .unwrap_or_else(|| "23:59".into());
    let _ = Command::new("schtasks.exe")
        .args(["/Delete", "/TN", &task, "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let tr = format!(
        "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{ps1_s}\""
    );
    let created = Command::new("schtasks.exe")
        .args(["/Create", "/TN", &task, "/TR", &tr, "/SC", "ONCE", "/ST", &st, "/F", "/RL", "LIMITED"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if created {
        let _ = Command::new("schtasks.exe")
            .args(["/Run", "/TN", &task])
            .creation_flags(CREATE_NO_WINDOW)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        let cleanup = format!("Start-Sleep -Seconds 240; schtasks /Delete /TN \"{task}\" /F");
        let _ = Command::new("powershell.exe")
            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &cleanup])
            .creation_flags(flags_basic)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        armed += 1;
    }

    if armed == 0 {
        Err("Could not start the hidden update waiter".into())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
fn wait_for_boot(log: &Path) -> bool {
    for _ in 0..40 {
        std::thread::sleep(std::time::Duration::from_millis(150));
        if let Ok(txt) = std::fs::read_to_string(log) {
            if txt.contains("BOOT pid=") {
                return true;
            }
        }
    }
    false
}

#[cfg(target_os = "linux")]
fn apply_linux(verified: &Path) -> Result<(), String> {
    let appimage = std::env::var_os("APPIMAGE").map(PathBuf::from).filter(|p| p.is_file()).ok_or_else(|| {
        "This Linux install is not an AppImage, so Remedy cannot replace itself. \
         Download the new AppImage (or install the .deb with sudo) from:\n\
         https://github.com/AhmiDarrow/RemedyAI/releases/latest"
            .to_string()
    })?;
    let meta = std::fs::metadata(verified).map_err(|e| e.to_string())?;
    if meta.len() < 512 * 1024 {
        return Err("Downloaded file is too small to be an AppImage".into());
    }
    let mut hdr = [0u8; 4];
    {
        use std::io::Read;
        let mut f = std::fs::File::open(verified).map_err(|e| e.to_string())?;
        f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    }
    if !elf_magic(&hdr) {
        return Err("Downloaded file is not a Linux executable".into());
    }
    // Copy onto the AppImage's own volume, then rename over it. A rename from
    // the temp directory fails when that directory is a different filesystem.
    let staged = appimage_stage_path(&appimage, std::process::id());
    let _ = std::fs::remove_file(&staged);
    std::fs::copy(verified, &staged).map_err(|e| format!("Cannot stage AppImage: {e}"))?;
    {
        use std::os::unix::fs::PermissionsExt;
        if let Err(e) = std::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o755)) {
            let _ = std::fs::remove_file(&staged);
            return Err(e.to_string());
        }
    }
    if let Err(e) = std::fs::rename(&staged, &appimage) {
        let _ = std::fs::remove_file(&staged);
        return Err(format!("Cannot replace AppImage: {e}"));
    }
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    let mut cmd = Command::new(&appimage);
    cmd.stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0);
    cmd.spawn().map_err(|e| format!("Failed to relaunch AppImage: {e}"))?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn apply_macos(archive: &Path) -> Result<(), String> {
    use std::io::Read;
    use std::process::{Command, Stdio};

    let meta = std::fs::metadata(archive).map_err(|e| e.to_string())?;
    if meta.len() < 512 * 1024 {
        return Err("Update archive is too small to be a Remedy app bundle".into());
    }
    let mut hdr = [0u8; 2];
    {
        let mut f = std::fs::File::open(archive).map_err(|e| e.to_string())?;
        f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    }
    if !gzip_magic(&hdr) {
        return Err("Update archive is not a gzip-compressed tarball".into());
    }
    let dest = macos_app_bundle().ok_or("Could not locate Remedy Desktop.app")?;
    if dest.to_string_lossy().starts_with("/Volumes/") {
        return Err("This copy is on a disk image. Move Remedy Desktop to Applications, then update.".into());
    }
    let listing = Command::new("tar")
        .args(["-tzf", &archive.to_string_lossy()])
        .output()
        .map_err(|e| format!("Cannot list update archive: {e}"))?;
    if !listing.status.success() {
        return Err("tar could not list the update archive".into());
    }
    let text = String::from_utf8_lossy(&listing.stdout);
    let entries: Vec<String> = text.lines().map(str::trim).filter(|l| !l.is_empty()).map(str::to_string).collect();
    let top = archive_top_level_app(&entries)?;
    let parent = dest.parent().unwrap_or(Path::new(".")).to_path_buf();
    let staging = parent.join(format!("Remedy.Desktop.staging-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&staging);
    std::fs::create_dir_all(&staging).map_err(|e| e.to_string())?;
    let status = Command::new("tar")
        .args(["-xzf", &archive.to_string_lossy(), "-C", &staging.to_string_lossy()])
        .status()
        .map_err(|e| e.to_string())?;
    if !status.success() {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(format!("tar exited {status}"));
    }
    let new_app = staging.join(&top);
    if !new_app.is_dir() {
        let _ = std::fs::remove_dir_all(&staging);
        return Err("Update archive did not contain a .app bundle".into());
    }
    let plist = std::fs::read_to_string(new_app.join("Contents").join("Info.plist")).unwrap_or_default();
    if !plist.contains(BUNDLE_ID) {
        let _ = std::fs::remove_dir_all(&staging);
        return Err(format!("Update bundle identifier is not {BUNDLE_ID}"));
    }
    let macos_dir = new_app.join("Contents").join("MacOS");
    let has_bin = match std::fs::read_dir(&macos_dir) {
        Ok(dir) => dir.flatten().any(|e| e.path().is_file()),
        Err(e) => {
            let _ = std::fs::remove_dir_all(&staging);
            return Err(e.to_string());
        }
    };
    if !has_bin {
        let _ = std::fs::remove_dir_all(&staging);
        return Err("Update archive .app has an empty Contents/MacOS".into());
    }
    let backup = parent.join(format!(
        "{}.bak",
        dest.file_name().and_then(|s| s.to_str()).unwrap_or("Remedy Desktop.app")
    ));
    let _ = std::fs::remove_dir_all(&backup);
    std::fs::rename(&dest, &backup).map_err(|e| format!("Cannot move current app aside: {e}"))?;
    if let Err(e) = std::fs::rename(&new_app, &dest) {
        let _ = std::fs::rename(&backup, &dest);
        let _ = std::fs::remove_dir_all(&staging);
        return Err(format!("Cannot install updated app: {e}"));
    }
    let _ = std::fs::remove_dir_all(&staging);
    let _ = std::fs::remove_dir_all(&backup);
    Command::new("open")
        .arg(&dest)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to relaunch Remedy Desktop: {e}"))?;
    Ok(())
}

#[cfg(target_os = "macos")]
fn macos_app_bundle() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    let mut p = exe.as_path();
    for _ in 0..8 {
        if p.extension().and_then(|s| s.to_str()) == Some("app") {
            return Some(p.to_path_buf());
        }
        p = p.parent()?;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_oh_is_newer_than_last_tauri() {
        assert_eq!(APP_VERSION, "1.0.0");
        assert!(release_is_newer("1.0.0", "0.70.5"));
        assert!(release_is_newer("v1.0.0", "v0.70.5"));
        assert!(!release_is_newer("0.70.5", "1.0.0"));
        assert!(!release_is_newer("1.0.0", "1.0.0"));
    }

    #[test]
    fn asset_names_match_the_tauri_release() {
        assert_eq!(windows_setup_name("1.0.0"), "Remedy.Desktop_1.0.0_x64-setup.exe");
        assert_eq!(linux_appimage_name("1.0.0"), "Remedy.Desktop_1.0.0_amd64.AppImage");
        assert_eq!(linux_deb_name("1.0.0"), "Remedy.Desktop_1.0.0_amd64.deb");
        assert_eq!(macos_dmg_name("1.0.0", "aarch64"), "Remedy.Desktop_1.0.0_aarch64.dmg");
        assert_eq!(macos_dmg_name("1.0.0", "x64"), "Remedy.Desktop_1.0.0_x64.dmg");
        assert_eq!(macos_tar_name("1.0.0", "aarch64"), "Remedy.Desktop_1.0.0_aarch64.app.tar.gz");
        assert_eq!(macos_tar_name("1.0.0", "x64"), "Remedy.Desktop_1.0.0_x64.app.tar.gz");
        assert!(windows_download_url("1.0.0").ends_with("/v1.0.0/Remedy.Desktop_1.0.0_x64-setup.exe"));
        assert!(linux_download_url("1.0.0").contains("/v1.0.0/Remedy.Desktop_1.0.0_amd64.AppImage"));
        assert!(darwin_download_url("1.0.0", "aarch64").contains("/runtime-v1.0.0/Remedy.Desktop_1.0.0_aarch64.app.tar.gz"));
        assert!(darwin_download_url("1.0.0", "x64").contains("/runtime-v1.0.0/"));
        assert!(!windows_setup_name("1.0.0").contains("Remedy_Desktop_"));
    }

    #[test]
    fn silent_update_args_keep_d_last() {
        let args = windows_silent_args(Some(r"C:\Users\A\AppData\Local\Remedy Desktop"));
        assert_eq!(&args[..4], ["/S", "/NCRC", "/UPDATE", "/NOAUTOLAUNCH"]);
        assert_eq!(args[4], r"/D=C:\Users\A\AppData\Local\Remedy Desktop");
        assert_eq!(args.len(), 5);
        assert!(!args[4].contains('"'));
        assert_eq!(
            windows_silent_args(None),
            vec!["/S", "/NCRC", "/UPDATE", "/NOAUTOLAUNCH"]
        );
    }

    #[test]
    fn pubkey_is_the_existing_tauri_key() {
        let conf = include_str!("../../src-tauri/tauri.conf.json");
        let signing = include_str!("../../../docs/WINDOWS_SIGNING.md");
        assert!(conf.contains(MINISIGN_PUBKEY_B64));
        assert!(signing.contains(MINISIGN_PUBKEY_B64));
        assert_eq!(pubkey_line_from_blob(MINISIGN_PUBKEY_B64).as_deref(), Some(MINISIGN_PUBKEY));
        let cfg = updater_config();
        assert_eq!(cfg.current, "1.0.0");
        match cfg.trust {
            remedy_ui::update::Trust::Minisign(key) => assert_eq!(key, MINISIGN_PUBKEY),
            other => panic!("expected minisign trust, got {other:?}"),
        }
    }

    #[test]
    fn feed_template_uses_the_same_shape() {
        let raw = include_str!("../packaging/latest.json");
        let v: serde_json::Value = serde_json::from_str(raw).unwrap();
        assert_eq!(v["version"], "v1.0.0");
        for (key, url_end) in [
            ("windows-x86_64", "/v1.0.0/Remedy.Desktop_1.0.0_x64-setup.exe"),
            ("linux-x86_64", "/v1.0.0/Remedy.Desktop_1.0.0_amd64.AppImage"),
            ("darwin-aarch64", "/runtime-v1.0.0/Remedy.Desktop_1.0.0_aarch64.app.tar.gz"),
            ("darwin-x86_64", "/runtime-v1.0.0/Remedy.Desktop_1.0.0_x64.app.tar.gz"),
        ] {
            let url = v["platforms"][key]["url"].as_str().unwrap();
            assert!(url.ends_with(url_end), "{key} url {url}");
            assert!(v["platforms"][key].get("signature").is_some());
        }
        assert_eq!(FEED_URL, "https://github.com/AhmiDarrow/RemedyAI/releases/latest/download/latest.json");
        assert_eq!(BUNDLE_ID, "com.remedy.desktop");
        assert_eq!(PRODUCT_NAME, "Remedy Desktop");
    }

    #[test]
    fn nsis_keeps_silent_update_and_user_data() {
        let nsi = include_str!("../packaging/windows/installer.nsi");
        let hooks = include_str!("../../src-tauri/windows/hooks.nsh");
        assert!(nsi.contains("NSIS_HOOK_PREINSTALL"));
        assert!(nsi.contains("NSIS_HOOK_POSTINSTALL"));
        assert!(nsi.contains("NSIS_HOOK_PREUNINSTALL"));
        assert!(nsi.contains("NSIS_HOOK_POSTUNINSTALL"));
        assert!(nsi.contains("/UPDATE"));
        assert!(nsi.contains("Function UninstallPrevious"));
        assert!(nsi.contains("ExecWait '\"$R1\" /S /UPDATE _?=$R2'"));
        assert!(nsi.contains("Remedy.Desktop_${VERSION}_x64-setup.exe") || nsi.contains("Remedy.Desktop_"));
        assert!(nsi.contains("com.remedy.desktop"));
        assert!(nsi.contains(r"$LOCALAPPDATA\${PRODUCTNAME}"));
        assert!(nsi.contains("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${PRODUCTNAME}")
            || nsi.contains("Uninstall\\${PRODUCTNAME}"));
        assert!(nsi.contains("hooks.nsh"));
        // "com.remedy.desktop" contains the letters ".remedy". The data directory is
        // a different path, and this script must not name it.
        assert!(!nsi.contains("/.remedy") && !nsi.contains("\\.remedy"),
            "the installer script must not name the per-user data directory");
        assert!(hooks.contains("/NOAUTOLAUNCH"));
        assert!(hooks.contains("IfSilent"));
        assert!(hooks.contains("Update-mode uninstall: keeping user data"));
        assert!(hooks.contains("Silent uninstall: keeping user data"));
        assert!(hooks.contains("RemedyDesktop-UpdaterOwnsRelaunch.flag"));
    }

    #[test]
    fn waiter_passes_the_flags_and_leaves_remedy_home() {
        let script = windows_waiter_script(&WindowsWaiter {
            parent_pid: 42,
            installer: r"C:\Temp\Remedy.Desktop_1.0.0_x64-setup.exe",
            prior_dir: r"C:\Users\A\AppData\Local\Remedy Desktop",
            flag_path: r"C:\Temp\RemedyDesktop-UpdaterOwnsRelaunch.flag",
            log_path: r"C:\Temp\RemedyDesktop-Update.log",
        });
        assert!(script.contains("$nsisArgs = @('/S', '/NCRC', '/UPDATE', '/NOAUTOLAUNCH')"));
        assert!(script.contains("$nsisArgs += \"/D=$priorDir\""));
        assert!(script.contains("-WindowStyle Hidden"));
        assert!(script.contains("RemedyDesktop-UpdaterOwnsRelaunch.flag"));
        assert!(script.contains("Join-Path $priorDir 'Remedy Desktop.exe'"));
        assert!(script.contains("another waiter holds the lock"));
        assert!(script.contains("CreateNew"));
        assert!(!script.contains(".remedy"));
        assert!(script.contains("$parentPid = 42"));
        let appimage = Path::new("/home/a/Remedy.Desktop_1.0.0_amd64.AppImage");
        let stage = appimage_stage_path(appimage, 7);
        assert_eq!(stage.parent(), appimage.parent());
        assert_eq!(
            stage.file_name().and_then(|s| s.to_str()),
            Some(".Remedy.Desktop_1.0.0_amd64.AppImage.7.partial")
        );
        assert!(archive_entry_is_safe("Remedy Desktop.app/Contents/Info.plist"));
        assert!(!archive_entry_is_safe("../etc/passwd"));
        assert!(!archive_entry_is_safe("/tmp/x"));
        let entries = vec!["Remedy Desktop.app/Contents/Info.plist".to_string()];
        assert_eq!(archive_top_level_app(&entries).unwrap(), "Remedy Desktop.app");
        assert!(archive_top_level_app(&["../x".into()]).is_err());
        assert!(pe_magic(b"MZ"));
        assert!(!pe_magic(b"\x7fELF"));
    }

    #[test]
    fn download_urls_must_be_this_repos_releases() {
        assert!(trusted_download(&windows_download_url("1.0.0")));
        assert!(trusted_download(FEED_URL));
        assert!(!trusted_download("https://example.com/Remedy.Desktop_1.0.0_x64-setup.exe"));
        assert!(!trusted_download("http://github.com/AhmiDarrow/RemedyAI/releases/download/v1.0.0/x.exe"));
        assert!(gzip_magic(&[0x1f, 0x8b, 0x08]));
        assert!(elf_magic(b"\x7fELF"));
    }

    #[test]
    fn download_note_names_the_percent_the_screen_used_to_show() {
        assert_eq!(download_note(0, None), "Downloading…");
        assert_eq!(download_note(50, Some(100)), "Downloading 50%");
        assert_eq!(download_note(3 * 1024 * 1024, None), "Downloading 3 MB");
        assert_eq!(download_note(u64::MAX, Some(1)), "Downloading 100%");
    }

    #[cfg(windows)]
    #[test]
    fn nsis_script_compiles() {
        let makensis = which_makensis();
        let Some(makensis) = makensis else {
            eprintln!("makensis not on PATH; skipping compile proof");
            return;
        };
        let dir = std::env::temp_dir().join("remedy-nsis-proof");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let stub = dir.join("stub.exe");
        std::fs::write(&stub, b"MZ remedy packaging stub\n").unwrap();
        let outfile = dir.join("Remedy.Desktop_1.0.0_x64-setup.exe");
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let desktop = manifest.parent().expect("desktop dir");
        let nsi = manifest.join("packaging/windows/installer.nsi");
        // Windows makensis does not open forward-slash !include or File paths.
        let as_nsis = |p: &Path| p.display().to_string();
        // Compile from an unrelated directory. Absolute backslash /D paths.
        let status = std::process::Command::new(&makensis)
            .current_dir(&dir)
            .arg("/V2")
            .arg("/DVERSION=1.0.0")
            .arg(format!("/DOUTFILE={}", as_nsis(&outfile)))
            .arg(format!("/DMAINBINARYSRCPATH={}", as_nsis(&stub)))
            .arg(format!("/DHOOKS={}", as_nsis(&desktop.join("src-tauri/windows/hooks.nsh"))))
            .arg(format!(
                "/DUNINSTALL_OPTIONS={}",
                as_nsis(&desktop.join("src-tauri/windows/uninstall_options.ps1"))
            ))
            .arg(format!(
                "/DUNINSTALL_WIPE={}",
                as_nsis(&desktop.join("src-tauri/windows/uninstall_wipe.ps1"))
            ))
            .arg(format!("/DINSTALLERICON={}", as_nsis(&desktop.join("src-tauri/icons/icon.ico"))))
            .arg(&nsi)
            .status()
            .expect("spawn makensis");
        assert!(status.success(), "makensis failed: {status}");
        let meta = std::fs::metadata(&outfile).expect("installer written");
        assert!(meta.len() > 1024, "installer looks empty");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    fn which_makensis() -> Option<PathBuf> {
        if let Ok(p) = std::env::var("NSISDIR") {
            let c = PathBuf::from(p).join("makensis.exe");
            if c.is_file() {
                return Some(c);
            }
        }
        std::env::var_os("PATH").and_then(|path| {
            std::env::split_paths(&path).find_map(|dir| {
                let c = dir.join("makensis.exe");
                c.is_file().then_some(c)
            })
        })
    }
}
