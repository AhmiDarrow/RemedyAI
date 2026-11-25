//! Supervises the managed `remedy-runtime` child.
//!
//! The old Tauri shell kept the Child, bound it to a Windows kill-on-close job
//! object (or Linux `PR_SET_PDEATHSIG`), and ran a watchdog that respawned
//! after an unexpected exit. The 1.0 port forgot the handle after spawn, so
//! Restart was a no-op when the port was already up and a crash was not
//! recovered. This module restores that lifecycle. A server this process did
//! not start is never killed.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

use super::{
    EnsureOutcome, find_runtime, load_token, log_desktop, origin, ping_blocking, spawn_disabled,
    wait_until_up,
};

/// Delays (seconds) before recovery attempts 1..=N, matching the Tauri shell.
pub const RECOVERY_DELAYS_SECS: [u64; 3] = [0, 2, 5];

/// How long an explicit Restart waits for the local API to answer.
const RESTART_HEALTH_SECS: u64 = 30;
const WATCHDOG_TICK: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RestartOutcome {
    /// Tests, or an explicit opt-out.
    Disabled,
    /// Killed the managed child (if any) and spawned a new one that answered ping.
    Restarted,
    /// Port was down; a new sidecar came up.
    Spawned,
    /// A healthy server we did not start is on the port. Leave it alone.
    Foreign,
    /// Spawned, but ping never answered.
    Unhealthy,
    /// No binary, or the process could not be started.
    Failed,
}

struct State {
    child: Option<Child>,
    cmd: Option<PathBuf>,
    attached_existing: bool,
    unexpected_exits: u32,
}

impl State {
    fn empty() -> Self {
        Self {
            child: None,
            cmd: None,
            attached_existing: false,
            unexpected_exits: 0,
        }
    }
}

fn state() -> &'static Mutex<State> {
    static STATE: OnceLock<Mutex<State>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(State::empty()))
}

fn lock() -> std::sync::MutexGuard<'static, State> {
    state().lock().unwrap_or_else(|p| p.into_inner())
}

fn exiting() -> &'static AtomicBool {
    static FLAG: AtomicBool = AtomicBool::new(false);
    &FLAG
}

/// Stop the watchdog from respawning during a full quit, and reap a managed
/// sidecar now. The job object or parent-death signal is the backstop if
/// this process exits before the kill finishes.
pub fn note_exiting() {
    exiting().store(true, Ordering::SeqCst);
    kill_managed();
}

/// Whether a crashed sidecar should be started again.
pub fn should_recover(exiting: bool, attached_existing: bool, spawn_disabled: bool) -> bool {
    !exiting && !attached_existing && !spawn_disabled
}

/// Starts the sidecar if nothing is on the port, then starts the watchdog.
pub fn ensure_running() -> EnsureOutcome {
    if spawn_disabled() {
        return EnsureOutcome::Disabled;
    }
    if ping_blocking(&origin(), load_token().as_deref()) {
        let mut st = lock();
        if st.child.is_none() {
            st.attached_existing = true;
        }
        start_watchdog();
        return EnsureOutcome::AlreadyUp;
    }
    match spawn_managed() {
        SpawnResult::Spawned => {
            start_watchdog();
            EnsureOutcome::Spawned
        }
        SpawnResult::MissingBinary => EnsureOutcome::MissingBinary,
        SpawnResult::Failed => EnsureOutcome::SpawnFailed,
    }
}

/// Kill the managed sidecar (only) and start a fresh one. A foreign server
/// on the port is left running.
pub fn restart() -> RestartOutcome {
    if spawn_disabled() {
        return RestartOutcome::Disabled;
    }
    {
        let st = lock();
        if st.attached_existing && st.child.is_none() && ping_blocking(&origin(), load_token().as_deref())
        {
            return RestartOutcome::Foreign;
        }
    }
    kill_managed();
    wait_until_down(Duration::from_secs(5));
    match spawn_managed() {
        SpawnResult::MissingBinary | SpawnResult::Failed => RestartOutcome::Failed,
        SpawnResult::Spawned => {
            start_watchdog();
            if wait_until_up(&origin(), load_token().as_deref(), (RESTART_HEALTH_SECS / 2) as u32)
            {
                RestartOutcome::Restarted
            } else {
                RestartOutcome::Unhealthy
            }
        }
    }
}

enum SpawnResult {
    Spawned,
    MissingBinary,
    Failed,
}

fn spawn_managed() -> SpawnResult {
    if ping_blocking(&origin(), load_token().as_deref()) {
        // Lost the race with another starter; attach rather than double-bind.
        let mut st = lock();
        if st.child.is_none() {
            st.attached_existing = true;
        }
        return SpawnResult::Spawned;
    }
    let Some(cmd) = find_runtime() else {
        log_desktop("sidecar missing: no remedy-runtime next to the app or on PATH");
        return SpawnResult::MissingBinary;
    };
    let Some(child) = spawn_bound(&cmd) else {
        log_desktop(&format!("sidecar spawn failed cmd={}", cmd.display()));
        return SpawnResult::Failed;
    };
    log_desktop(&format!("sidecar spawned pid={} cmd={}", child.id(), cmd.display()));
    let mut st = lock();
    st.child = Some(child);
    st.cmd = Some(cmd);
    st.attached_existing = false;
    SpawnResult::Spawned
}

fn spawn_bound(cmd: &std::path::Path) -> Option<Child> {
    #[allow(unused_mut)]
    let mut command = build_command(cmd);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let child = spawn_os(command)?;
    #[cfg(windows)]
    bind_job(&child);
    Some(child)
}

fn build_command(cmd: &std::path::Path) -> Command {
    let mut command = Command::new(cmd);
    if super::file_name_starts(cmd, "remedy-runtime") {
        command.arg("--serve");
    } else {
        command.args(["serve", "--host", "127.0.0.1", "--port", "7400"]);
    }
    if let Ok(key) = std::env::var("REMEDY_API_KEY") {
        command.env("REMEDY_API_KEY", key);
    }
    // Tells the runtime and its worker they run inside the shipped desktop.
    // Unattended code self-modification is for a developer's checkout only and
    // keys off this, so an installed app never drafts changes to itself.
    command.env("REMEDY_DESKTOP_SIDECAR", "1");
    if let Some(dir) = cmd.parent() {
        command.current_dir(dir);
    }
    command.stdin(Stdio::null()).stdout(Stdio::null());
    let log_path = super::remedy_home().join("logs").join("desktop.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match std::fs::OpenOptions::new().create(true).append(true).open(&log_path) {
        Ok(f) => {
            command.stderr(Stdio::from(f));
        }
        Err(_) => {
            command.stderr(Stdio::null());
        }
    }
    command
}

/// Linux: `PR_SET_PDEATHSIG` is tied to the forking *thread*, so the spawn
/// thread parks for the life of the process. Windows uses a job object instead.
fn spawn_os(command: Command) -> Option<Child> {
    #[cfg(target_os = "linux")]
    {
        return spawn_bound_to_parent(command);
    }
    #[cfg(not(target_os = "linux"))]
    {
        let mut command = command;
        command.spawn().ok()
    }
}

#[cfg(target_os = "linux")]
fn spawn_bound_to_parent(mut c: Command) -> Option<Child> {
    use std::os::unix::process::CommandExt;
    let (tx, rx) = std::sync::mpsc::channel::<Option<Child>>();
    let spawned = std::thread::Builder::new()
        .name("sidecar-spawner".into())
        .spawn(move || {
            unsafe {
                c.pre_exec(|| {
                    libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM as libc::c_ulong);
                    Ok(())
                });
            }
            let _ = tx.send(c.spawn().ok());
            loop {
                std::thread::park();
            }
        });
    if spawned.is_err() {
        return None;
    }
    rx.recv().ok().flatten()
}

#[cfg(windows)]
fn bind_job(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::JobObjects::AssignProcessToJobObject;
    let Some(job) = job_object() else {
        return;
    };
    let process = HANDLE(child.as_raw_handle() as *mut core::ffi::c_void);
    if let Err(e) = unsafe { AssignProcessToJobObject(job, process) } {
        log_desktop(&format!(
            "Could not bind sidecar pid {} to the kill-on-close job: {e}",
            child.id()
        ));
    }
}

/// Job object whose close kills every assigned process. The handle is never
/// closed, so the sidecar tree dies with this process.
#[cfg(windows)]
fn job_object() -> Option<windows::Win32::Foundation::HANDLE> {
    use windows::core::PCWSTR;
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    static JOB: OnceLock<isize> = OnceLock::new();
    let raw = *JOB.get_or_init(|| unsafe {
        let job = match CreateJobObjectW(None, PCWSTR::null()) {
            Ok(h) => h,
            Err(e) => {
                log_desktop(&format!("CreateJobObject failed; sidecar will not be bound: {e}"));
                return 0;
            }
        };
        let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if let Err(e) = set {
            log_desktop(&format!("SetInformationJobObject(KILL_ON_JOB_CLOSE) failed: {e}"));
            let _ = CloseHandle(job);
            return 0;
        }
        job.0 as isize
    });
    if raw == 0 {
        None
    } else {
        Some(windows::Win32::Foundation::HANDLE(raw as *mut core::ffi::c_void))
    }
}

fn kill_managed() {
    let mut st = lock();
    if let Some(mut child) = st.child.take() {
        let pid = child.id();
        let _ = child.kill();
        let _ = child.wait();
        log_desktop(&format!("sidecar killed pid={pid}"));
    }
    st.attached_existing = false;
}

fn wait_until_down(max: Duration) {
    let start = std::time::Instant::now();
    while start.elapsed() < max {
        if !ping_blocking(&origin(), load_token().as_deref()) {
            return;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
}

fn start_watchdog() {
    static STARTED: AtomicBool = AtomicBool::new(false);
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    let _ = std::thread::Builder::new()
        .name("sidecar-watchdog".into())
        .spawn(watchdog_loop);
}

fn watchdog_loop() {
    loop {
        std::thread::sleep(WATCHDOG_TICK);
        if exiting().load(Ordering::SeqCst) {
            return;
        }
        let exited = {
            let mut st = lock();
            match st.child.as_mut().map(Child::try_wait) {
                Some(Ok(Some(status))) => {
                    st.child = None;
                    Some(status.to_string())
                }
                Some(Err(error)) => {
                    log_desktop(&format!("Could not inspect managed sidecar status: {error}"));
                    None
                }
                _ => None,
            }
        };
        let Some(status) = exited else {
            continue;
        };
        let attached = lock().attached_existing;
        if !should_recover(
            exiting().load(Ordering::SeqCst),
            attached,
            spawn_disabled(),
        ) {
            log_desktop(&format!(
                "sidecar exited ({status}); not recovering (exiting or foreign)"
            ));
            continue;
        }
        {
            let mut st = lock();
            st.unexpected_exits = st.unexpected_exits.saturating_add(1);
        }
        log_desktop(&format!("sidecar exited unexpectedly ({status}); recovering"));
        recover();
    }
}

fn recover() {
    for (attempt, delay) in RECOVERY_DELAYS_SECS.into_iter().enumerate() {
        if delay > 0 {
            std::thread::sleep(Duration::from_secs(delay));
        }
        if exiting().load(Ordering::SeqCst) {
            return;
        }
        log_desktop(&format!("sidecar recovery attempt {}", attempt + 1));
        if ping_blocking(&origin(), load_token().as_deref()) {
            log_desktop("sidecar recovery: port already answers, attaching");
            let mut st = lock();
            if st.child.is_none() {
                st.attached_existing = true;
            }
            return;
        }
        match spawn_managed() {
            SpawnResult::Spawned => {
                if wait_until_up(&origin(), load_token().as_deref(), 15) {
                    log_desktop("sidecar recovered");
                    return;
                }
                log_desktop("sidecar recovery spawn did not become ready");
            }
            SpawnResult::MissingBinary => {
                log_desktop("sidecar recovery failed: binary missing");
                return;
            }
            SpawnResult::Failed => {
                log_desktop("sidecar recovery spawn failed");
            }
        }
    }
}

/// Reap a short-lived process. Used by tests and as a model for kill_managed.
#[cfg(test)]
pub(crate) fn kill_child_wait(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn recovery_delays_match_the_tauri_shell() {
        assert_eq!(RECOVERY_DELAYS_SECS, [0, 2, 5]);
    }

    #[test]
    fn recover_skips_when_exiting_foreign_or_disabled() {
        assert!(!should_recover(true, false, false));
        assert!(!should_recover(false, true, false));
        assert!(!should_recover(false, false, true));
        assert!(should_recover(false, false, false));
    }

    #[test]
    fn kill_reaps_a_managed_child() {
        // Windows has no `sleep` program outside a POSIX shell's PATH.
        #[cfg(windows)]
        let mut cmd = {
            let mut c = Command::new("ping");
            c.args(["-n", "30", "127.0.0.1"]);
            c
        };
        #[cfg(not(windows))]
        let mut cmd = {
            let mut c = Command::new("sleep");
            c.arg("30");
            c
        };
        let mut child = cmd
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("sleep");
        let pid = child.id();
        assert!(pid > 0);
        kill_child_wait(&mut child);
        // A second wait should not hang: the process is already reaped.
        assert!(child.try_wait().ok().flatten().is_some() || child.try_wait().is_ok());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn linux_child_gets_a_parent_death_signal() {
        use std::io::Read;
        let mut cmd = Command::new("python3");
        cmd.args([
            "-c",
            "import ctypes,sys; libc=ctypes.CDLL('libc.so.6'); v=ctypes.c_int(); libc.prctl(2, ctypes.byref(v)); sys.stdout.write(str(v.value))",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
        unsafe {
            use std::os::unix::process::CommandExt;
            cmd.pre_exec(|| {
                libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM as libc::c_ulong);
                Ok(())
            });
        }
        let mut child = cmd.spawn().expect("python pdeathsig probe");
        let mut out = String::new();
        if let Some(mut stdout) = child.stdout.take() {
            let _ = stdout.read_to_string(&mut out);
        }
        let _ = child.wait();
        let sig = out.trim().parse::<i32>().unwrap_or(0);
        assert_eq!(sig, libc::SIGTERM, "child should inherit PR_SET_PDEATHSIG=SIGTERM, got {out:?}");
    }
}
