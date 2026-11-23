//! In-app update screen. The download is checked with the Tauri minisign key
//! before anything is launched. Packaging owns the process-killing install script;
//! this screen only starts a verified Windows installer with `/S /UPDATE`.

use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use minisign_verify::{PublicKey, Signature};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::net::{Cancel, Http, Request};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};

use super::chrome;
use super::model::{
    self, ReleaseInfo, UpdatePhase, UPDATE_GITHUB, UPDATE_HANG_TIGHT, UPDATE_LATER, UPDATE_MANUAL,
    UPDATE_NO_URL, UPDATE_PLEASE_WAIT, UPDATE_RELAUNCH, UPDATE_RETRY, UPDATE_STARTING, UPDATE_TITLE,
    UPDATE_VERSION, UPDATE_WRONG,
};
use crate::logo;

/// Same pubkey as `desktop/src-tauri` (`plugins.updater.pubkey`).
const UPDATER_MINISIGN_PUBKEY_B64: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEQ2MDEwQzVERTNBQ0JDRTAKUldUZ3ZLempYUXdCMWdRNWl0UzlpSDVUamJQZXRvREFpNE9Mb2xJeGpQck5ubVJ5ZDNxSko0dTYK";

const INSTALLER_MIN_BYTES: u64 = 512 * 1024;
const INSTALLER_MAX_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Clone)]
pub struct Updater {
    pub phase: Signal<UpdatePhase>,
    pub percent: Signal<Option<f32>>,
    pub message: Signal<String>,
    pub error: Signal<String>,
    pub from: Signal<String>,
    pub to: Signal<String>,
    pub notes: Signal<String>,
    pub url: Signal<String>,
    pub signature: Signal<String>,
    pub auto_start: Signal<bool>,
    pub close: Signal<bool>,
    /// Set after a verified Windows installer has been spawned. The gate quits.
    pub quit: Signal<bool>,
    started: Signal<bool>,
}

impl Updater {
    pub fn new() -> Self {
        Self {
            phase: Signal::new(UpdatePhase::Ready),
            percent: Signal::new(None),
            message: Signal::new(String::new()),
            error: Signal::new(String::new()),
            from: Signal::new(model::shell_version().into()),
            to: Signal::new(String::new()),
            notes: Signal::new(String::new()),
            url: Signal::new(String::new()),
            signature: Signal::new(String::new()),
            auto_start: Signal::new(false),
            close: Signal::new(false),
            quit: Signal::new(false),
            started: Signal::new(false),
        }
    }

    /// Ready state: notes, Later, Update & Relaunch. Nothing downloads.
    pub fn preview() -> Self {
        let u = Self::new();
        u.to.set("1.1.0".into());
        u.notes.set(
            "A new built-in mind, smarter token saving, and house rules you can go over again after an update."
                .into(),
        );
        u.url.set(
            "https://github.com/AhmiDarrow/RemedyAI/releases/download/v1.1.0/Remedy.Desktop_1.1.0_x64-setup.exe"
                .into(),
        );
        u.signature.set("preview".into());
        u
    }

    pub fn apply_release(&self, info: ReleaseInfo) {
        self.from.set(model::shell_version().into());
        self.to.set(info.version);
        self.notes.set(model::notes_slice(&info.notes));
        self.url.set(info.download_url);
        self.signature.set(info.signature);
        self.phase.set(UpdatePhase::Ready);
        self.started.set(false);
        self.error.set(String::new());
        self.message.set(String::new());
        self.percent.set(None);
        self.auto_start.set(false);
    }

    pub fn request_close(&self) {
        self.close.set(true);
    }

    /// The title-bar chip opens the screen and starts the download.
    pub fn prepare_auto(&self) {
        self.phase.set(UpdatePhase::Ready);
        self.started.set(false);
        self.error.set(String::new());
        self.message.set(String::new());
        self.auto_start.set(true);
    }

    /// Download, verify, then on Windows spawn `/S /UPDATE`. Safe to call twice.
    pub fn begin(&self) {
        if self.started.get() {
            return;
        }
        let url = self.url.get();
        if url.is_empty() {
            self.fail(UPDATE_NO_URL);
            return;
        }
        if !model::is_trusted_download_url(&url) {
            self.fail("Download URL is not a trusted GitHub release host.");
            return;
        }
        let signature = self.signature.get();
        if signature.is_empty() {
            self.fail(
                "Release is unsigned (empty signature in latest.json). Install manually from GitHub Releases if you trust the asset.",
            );
            return;
        }
        let to = self.to.get();
        if !to.is_empty() && !model::is_newer(&to, model::shell_version()) {
            self.fail(format!(
                "Latest release is {to}; this Desktop is {}. Nothing newer to install.",
                model::shell_version()
            ));
            return;
        }
        let Ok(waker) = remedy_ui::reactive::waker() else {
            self.fail(UPDATE_WRONG);
            return;
        };
        self.started.set(true);
        self.phase.set(UpdatePhase::Downloading);
        self.percent.set(None);
        self.message.set(UPDATE_STARTING.into());
        self.error.set(String::new());
        let phase = self.phase.clone();
        let percent = self.percent.clone();
        let message = self.message.clone();
        let error = self.error.clone();
        let started = self.started.clone();
        let quit = self.quit.clone();
        if std::thread::Builder::new()
            .name("remedy-update".into())
            .spawn(move || {
                let dest = installer_path();
                let phase_p = phase.clone();
                let percent_p = percent.clone();
                let message_p = message.clone();
                let waker_p = waker.clone();
                let downloaded = download_installer(&url, &dest, move |written, total| {
                    let pct = total
                        .filter(|t| *t > 0)
                        .map(|t| (written as f32 / t as f32).clamp(0.0, 1.0));
                    let mb = written as f64 / (1024.0 * 1024.0);
                    let msg = format!("Downloading update… {mb:.1} MB");
                    let phase_p = phase_p.clone();
                    let percent_p = percent_p.clone();
                    let message_p = message_p.clone();
                    let _ = waker_p.post(move || {
                        phase_p.set(UpdatePhase::Downloading);
                        percent_p.set(pct);
                        message_p.set(msg);
                    });
                });
                if let Err(e) = downloaded {
                    let _ = std::fs::remove_file(&dest);
                    report_fail(&waker, phase, error, started, message, with_manual(&e, &url));
                    return;
                }
                if let Err(e) = inspect_installer(&dest).and_then(|_| verify_installer(&dest, &signature)) {
                    let _ = std::fs::remove_file(&dest);
                    report_fail(&waker, phase, error, started, message, with_manual(&e, &url));
                    return;
                }
                let phase_f = phase.clone();
                let message_f = message.clone();
                let _ = waker.post(move || {
                    phase_f.set(UpdatePhase::Finishing);
                    percent.set(Some(1.0));
                    message_f.set("Download complete. Restarting to finish install…".into());
                });
                match handoff(&dest, &url) {
                    Ok(()) => {
                        let _ = waker.post(move || {
                            phase.set(UpdatePhase::StartingApp);
                            message.set(model::update_subtitle(UpdatePhase::StartingApp).into());
                            quit.set(true);
                        });
                    }
                    Err(e) => report_fail(&waker, phase, error, started, message, e),
                }
            })
            .is_err()
        {
            self.fail("Could not start the download.");
        }
    }

    fn fail(&self, msg: impl Into<String>) {
        self.phase.set(UpdatePhase::Failed);
        self.error.set(msg.into());
        self.started.set(false);
        self.message.set(String::new());
    }
}

fn report_fail(
    waker: &remedy_ui::reactive::Waker,
    phase: Signal<UpdatePhase>,
    error: Signal<String>,
    started: Signal<bool>,
    message: Signal<String>,
    text: String,
) {
    let _ = waker.post(move || {
        phase.set(UpdatePhase::Failed);
        error.set(text);
        started.set(false);
        message.set(String::new());
    });
}

fn with_manual(msg: &str, url: &str) -> String {
    if url.is_empty() {
        format!("{msg}\n\n{UPDATE_GITHUB}")
    } else {
        format!("{msg}\n\n{UPDATE_MANUAL}\n{url}")
    }
}

fn installer_path() -> PathBuf {
    let ext = if cfg!(windows) { "exe" } else { "bin" };
    std::env::temp_dir().join(format!("RemedyDesktop-Update-{}.{ext}", std::process::id()))
}

struct Dl {
    file: File,
    written: u64,
    total: Option<u64>,
    rejected: Option<String>,
    saw: bool,
    last_report: u64,
}

fn download_installer(
    url: &str,
    dest: &Path,
    progress: impl Fn(u64, Option<u64>) + Send + 'static,
) -> Result<(), String> {
    if !model::is_trusted_download_url(url) {
        return Err("Download URL is not a trusted GitHub release host".into());
    }
    let http = Http::new("https://github.com")
        .timeout(Some(Duration::from_secs(600)))
        .max_body(Some(INSTALLER_MAX_BYTES));
    let req = Request::get(url)
        .header("User-Agent", "RemedyDesktop-Updater/1.0")
        .header("Accept", "application/octet-stream")
        .timeout(Some(Duration::from_secs(600)))
        .max_body(Some(INSTALLER_MAX_BYTES));
    let file = File::create(dest).map_err(|e| format!("Cannot write installer: {e}"))?;
    let state = Arc::new(Mutex::new(Dl {
        file,
        written: 0,
        total: None,
        rejected: None,
        saw: false,
        last_report: 0,
    }));
    let cancel = Cancel::new();
    let (tx, rx) = mpsc::channel();
    let state_c = Arc::clone(&state);
    let cancel_c = cancel.clone();
    http.stream(
        req,
        &cancel,
        move |resp, chunk| {
            let mut st = state_c.lock().unwrap_or_else(|e| e.into_inner());
            if st.rejected.is_some() {
                return;
            }
            if !st.saw {
                st.saw = true;
                if resp
                    .header("content-type")
                    .is_some_and(|ct| ct.to_ascii_lowercase().contains("text/html"))
                {
                    st.rejected = Some("GitHub returned an HTML error page.".into());
                    cancel_c.cancel();
                    return;
                }
                if let Some(len) = resp.header("content-length").and_then(|s| s.trim().parse().ok()) {
                    st.total = Some(len);
                }
            }
            if st.written == 0 && looks_like_html(chunk) {
                st.rejected = Some("GitHub returned an HTML error page.".into());
                cancel_c.cancel();
                return;
            }
            if st.file.write_all(chunk).is_err() {
                st.rejected = Some("Could not write the installer.".into());
                cancel_c.cancel();
                return;
            }
            st.written += chunk.len() as u64;
            let written = st.written;
            let total = st.total;
            if written.saturating_sub(st.last_report) >= 64 * 1024 || total.is_some_and(|t| written >= t) {
                st.last_report = written;
                drop(st);
                progress(written, total);
            }
        },
        move |done| {
            let _ = tx.send(done);
        },
    );
    let rejected = {
        let result = rx.recv_timeout(Duration::from_secs(620));
        let rejected = state.lock().unwrap_or_else(|e| e.into_inner()).rejected.clone();
        match result {
            Ok(Ok(_)) => rejected,
            Ok(Err(e)) => {
                let _ = std::fs::remove_file(dest);
                return Err(rejected.unwrap_or_else(|| e.to_string()));
            }
            Err(_) => {
                cancel.cancel();
                let _ = std::fs::remove_file(dest);
                return Err(rejected.unwrap_or_else(|| "Download timed out.".into()));
            }
        }
    };
    drop(state);
    if let Some(err) = rejected {
        let _ = std::fs::remove_file(dest);
        return Err(err);
    }
    Ok(())
}

fn looks_like_html(chunk: &[u8]) -> bool {
    let n = chunk.len().min(64);
    let text = String::from_utf8_lossy(&chunk[..n]).trim_start().to_ascii_lowercase();
    text.starts_with("<!doctype") || text.starts_with("<html") || text.starts_with("<head")
}

fn inspect_installer(path: &Path) -> Result<(), String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("Cannot stat installer: {e}"))?;
    if meta.len() < INSTALLER_MIN_BYTES {
        return Err(format!(
            "Downloaded installer is too small ({} bytes) - likely not a real NSIS package",
            meta.len()
        ));
    }
    let mut file = File::open(path).map_err(|e| format!("Cannot open installer: {e}"))?;
    let mut magic = [0u8; 4];
    file.read_exact(&mut magic)
        .map_err(|e| format!("Cannot read installer header: {e}"))?;
    #[cfg(windows)]
    if &magic[..2] != b"MZ" {
        return Err(
            "Downloaded file is not a Windows executable (missing MZ header). \
             GitHub may have returned an HTML error page."
                .into(),
        );
    }
    #[cfg(not(windows))]
    if looks_like_html(&magic) {
        return Err("GitHub returned an HTML error page.".into());
    }
    Ok(())
}

fn decode_updater_signature(sig: &str) -> Result<Signature, String> {
    let sig = sig.trim();
    if let Ok(parsed) = Signature::decode(sig) {
        return Ok(parsed);
    }
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(sig)
        .map_err(|_| "updater signature parse: Invalid encoding in minisign data".to_string())?;
    let text = String::from_utf8(decoded)
        .map_err(|_| "updater signature parse: Invalid encoding in minisign data".to_string())?;
    Signature::decode(text.trim()).map_err(|e| format!("updater signature parse: {e}"))
}

fn verify_installer(path: &Path, sig: &str) -> Result<(), String> {
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(UPDATER_MINISIGN_PUBKEY_B64)
        .map_err(|e| format!("updater pubkey decode: {e}"))?;
    let pk_text = String::from_utf8_lossy(&decoded);
    let pk_line = pk_text
        .lines()
        .map(str::trim)
        .find(|l| l.starts_with("RW"))
        .ok_or_else(|| "updater pubkey missing RW line".to_string())?;
    let pk = PublicKey::from_base64(pk_line).map_err(|e| format!("updater pubkey parse: {e}"))?;
    let signature = decode_updater_signature(sig)?;
    let data = std::fs::read(path).map_err(|e| format!("read installer for verify: {e}"))?;
    pk.verify(&data, &signature, false)
        .map_err(|e| format!("installer signature mismatch: {e}"))?;
    Ok(())
}

fn handoff(path: &Path, url: &str) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _ = url;
        launch_windows(path)
    }
    #[cfg(not(windows))]
    {
        let _ = std::fs::remove_file(path);
        Err(format!(
            "{}\n\n{UPDATE_MANUAL}\n{url}",
            model::UPDATE_PLATFORM
        ))
    }
}

#[cfg(windows)]
fn launch_windows(path: &Path) -> Result<(), String> {
    use std::os::windows::process::CommandExt;
    const CREATE_BREAKAWAY_FROM_JOB: u32 = 0x01000000;
    const DETACHED_PROCESS: u32 = 0x00000008;
    const CREATE_NO_WINDOW: u32 = 0x08000000;
    let spawn = |flags: u32| {
        Command::new(path)
            .args(["/S", "/UPDATE"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(flags)
            .spawn()
    };
    let broken = CREATE_BREAKAWAY_FROM_JOB | DETACHED_PROCESS | CREATE_NO_WINDOW;
    match spawn(broken) {
        Ok(_) => Ok(()),
        Err(_) => spawn(DETACHED_PROCESS | CREATE_NO_WINDOW)
            .map(|_| ())
            .map_err(|e| format!("Could not start installer: {e}")),
    }
}

pub fn update_screen(ui: &Ui, updater: Updater) -> El {
    let auto = updater.auto_start.clone();
    let phase_watch = updater.phase.clone();
    let started = updater.started.clone();
    let kick = updater.clone();
    let screen = chrome::center(ui, body(ui, updater), 420.0);
    let screen2 = screen.clone();
    screen.bind(move || {
        if auto.get() && !started.get() && phase_watch.get() == UpdatePhase::Ready {
            kick.begin();
        }
        let _ = &screen2;
    });
    screen
}

fn body(ui: &Ui, updater: Updater) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let phase = updater.phase.clone();
    let subtitle = updater.phase.clone();
    w::column(ui)
        .gap(unit * 2.0)
        .align(Align::Center)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(logo::wordmark(ui, 40.0))
        .child(
            w::text(ui, UPDATE_TITLE)
                .variant("heading")
                .style(StylePatch::new().foreground(colors.accent)),
        )
        .child(
            w::text(ui, "")
                .variant("muted")
                .text(move || model::update_subtitle(subtitle.get()).to_string()),
        )
        .child(version_row(ui, updater.clone()))
        .child(notes_block(ui, updater.clone()))
        .child(progress_block(ui, updater.clone()))
        .child(error_block(ui, updater.clone()))
        .child(actions(ui, updater, phase))
}

fn version_row(ui: &Ui, updater: Updater) -> El {
    let colors = ui.theme().colors.clone();
    let unit = ui.theme().spacing.unit;
    let from = updater.from.clone();
    let to = updater.to.clone();
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .padding(remedy_ui::core::Insets::symmetric(unit * 1.5, unit * 2.0))
        .style(
            StylePatch::new()
                .background(colors.panel)
                .radius(ui.theme().radii.medium)
                .border(colors.line.unwrap_or(colors.muted))
                .border_width(1.0),
        )
        .layout(|l| l.width = Length::Percent(1.0))
        .child(w::text(ui, UPDATE_VERSION).variant("muted"))
        .child(w::text(ui, "").text(move || format!("v{} → v{}", from.get(), to.get())))
}

fn notes_block(ui: &Ui, updater: Updater) -> El {
    let phase = updater.phase.clone();
    let notes = updater.notes.clone();
    let phase2 = phase.clone();
    let notes2 = notes.clone();
    let ui_notes = ui.clone();
    show(
        ui,
        move || phase2.get() == UpdatePhase::Ready && !notes2.get().trim().is_empty(),
        move || {
            let colors = ui_notes.theme().colors.clone();
            let unit = ui_notes.theme().spacing.unit;
            let radius = ui_notes.theme().radii.medium;
            let notes = notes.clone();
            w::scroll(&ui_notes)
                .padding(remedy_ui::core::Insets::all(unit * 1.5))
                .style(
                    StylePatch::new()
                        .background(colors.panel)
                        .radius(radius)
                        .border(colors.line.unwrap_or(colors.muted))
                        .border_width(1.0),
                )
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.max_height = Length::Px(112.0);
                    l.grow = 0.0;
                })
                .child(w::text(&ui_notes, "").variant("muted").text(move || model::notes_slice(&notes.get())))
        },
    )
    .el()
}

fn progress_block(ui: &Ui, updater: Updater) -> El {
    let phase = updater.phase.clone();
    let phase2 = phase.clone();
    let ui_bar = ui.clone();
    let message = updater.message.clone();
    let percent = updater.percent.clone();
    show(
        ui,
        move || {
            matches!(
                phase2.get(),
                UpdatePhase::Downloading | UpdatePhase::Finishing | UpdatePhase::StartingApp
            )
        },
        move || {
            let unit = ui_bar.theme().spacing.unit;
            let message = message.clone();
            let waiting = phase.clone();
            let percent = percent.clone();
            w::column(&ui_bar)
                .gap(unit)
                .layout(|l| l.width = Length::Percent(1.0))
                .child(
                    w::progress(&ui_bar, "Update download", percent).layout(|l| {
                        l.width = Length::Percent(1.0);
                        l.height = Length::Px(8.0);
                    }),
                )
                .child(
                    w::text(&ui_bar, "")
                        .variant("muted")
                        .text(move || {
                            let msg = message.get();
                            if msg.is_empty() {
                                model::update_subtitle(waiting.get()).to_string()
                            } else {
                                msg
                            }
                        }),
                )
        },
    )
    .el()
}

fn error_block(ui: &Ui, updater: Updater) -> El {
    let phase = updater.phase.clone();
    let error = updater.error.clone();
    let ui_err = ui.clone();
    show(
        ui,
        move || phase.get() == UpdatePhase::Failed,
        move || {
            let colors = ui_err.theme().colors.clone();
            let error = error.clone();
            w::text(&ui_err, "")
                .variant("muted")
                .text(move || {
                    let msg = error.get();
                    if msg.trim().is_empty() { UPDATE_WRONG.into() } else { msg }
                })
                .style(StylePatch::new().foreground(colors.danger))
        },
    )
    .el()
}

fn actions(ui: &Ui, updater: Updater, phase: Signal<UpdatePhase>) -> El {
    let ready = phase.clone();
    let failed = phase.clone();
    let busy = phase.clone();
    let ui_ready = ui.clone();
    let ui_failed = ui.clone();
    let ui_busy = ui.clone();
    w::column(ui)
        .layout(|l| l.width = Length::Percent(1.0))
        .child(show(ui, move || ready.get() == UpdatePhase::Ready, {
            let updater = updater.clone();
            move || {
                let later = chrome::quiet(&ui_ready, UPDATE_LATER, {
                    let updater = updater.clone();
                    move || updater.request_close()
                });
                let go = chrome::primary(&ui_ready, UPDATE_RELAUNCH, {
                    let updater = updater.clone();
                    move || updater.begin()
                });
                let go2 = go.clone();
                let url = updater.url.clone();
                let started = updater.started.clone();
                go.bind(move || {
                    let _ = go2.disabled(url.get().is_empty() || started.get());
                });
                chrome::actions(&ui_ready, vec![later, go])
            }
        }).el())
        .child(show(ui, move || failed.get() == UpdatePhase::Failed, {
            let updater = updater.clone();
            move || {
                chrome::actions(
                    &ui_failed,
                    vec![
                        chrome::quiet(&ui_failed, model::UPDATE_CLOSE, {
                            let updater = updater.clone();
                            move || updater.request_close()
                        }),
                        chrome::primary(&ui_failed, UPDATE_RETRY, {
                            let updater = updater.clone();
                            move || updater.begin()
                        }),
                    ],
                )
            }
        }).el())
        .child(show(
            ui,
            move || {
                matches!(
                    busy.get(),
                    UpdatePhase::Downloading | UpdatePhase::Finishing | UpdatePhase::StartingApp
                )
            },
            move || {
                let phase = phase.clone();
                w::text(&ui_busy, "")
                    .variant("muted")
                    .text(move || {
                        if phase.get() == UpdatePhase::Downloading {
                            UPDATE_PLEASE_WAIT.into()
                        } else {
                            UPDATE_HANG_TIGHT.into()
                        }
                    })
            },
        ).el())
}


