//! Status-bar Server menu: health, logs, data folder, restart, live output.

use std::time::{SystemTime, UNIX_EPOCH};

use remedy_ui::core::Insets;
use remedy_ui::layout::Length;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};

use crate::chrome::Chrome;
use crate::messages;
use crate::prefs;
use crate::runtime::{self, origin};
use crate::studio::{Conn, Studio};

pub fn format_uptime(started_at_ms: Option<i64>, now_ms: i64) -> String {
    let Some(started) = started_at_ms else {
        return "—".into();
    };
    if started > now_ms {
        return "—".into();
    }
    let s = ((now_ms - started) / 1000).max(0);
    if s < 60 {
        return format!("{s}s");
    }
    let m = s / 60;
    if m < 60 {
        return format!("{m}m {}s", s % 60);
    }
    let h = m / 60;
    if h < 48 {
        return format!("{h}h {}m", m % 60);
    }
    format!("{}d {}h", h / 24, h % 24)
}

pub fn format_ago(at_ms: Option<i64>, now_ms: i64) -> String {
    let Some(at) = at_ms else {
        return String::new();
    };
    let s = ((now_ms - at) / 1000).max(0);
    if s < 5 {
        return "just now".into();
    }
    if s < 60 {
        return format!("{s}s ago");
    }
    let m = s / 60;
    if m < 60 {
        return format!("{m} min ago");
    }
    let h = m / 60;
    if h < 48 {
        return format!("{h} h ago");
    }
    format!("{} d ago", h / 24)
}

/// Turn an OS exit status into something a person can act on.
pub fn describe_exit(status: &str) -> String {
    let raw = status.trim();
    if raw.is_empty() {
        return String::new();
    }
    if let Some(n) = exit_code(raw) {
        let unsigned = if n < 0 { (n as i64) + 0x1_0000_0000 } else { n };
        let hint = match unsigned {
            0 => Some("exited cleanly (something asked it to stop)"),
            1 => Some("exited with an error (see the last output below)"),
            2 => Some("refused to start: bad arguments or another instance holds the lock"),
            3 => Some("API server failed to start (port busy or startup error)"),
            0xC000_0005 => Some("access violation — a native crash inside the server"),
            0xC000_0409 => Some("stack buffer overrun / fast-fail — a native crash"),
            0xC000_013A => Some("closed by a console Ctrl+C / Ctrl+Break event"),
            0xC000_0374 => Some("heap corruption — a native crash"),
            0xE043_4352 => Some(".NET/CLR exception in a native component"),
            0xC000_00FD => Some("stack overflow — a native crash"),
            _ => None,
        };
        let hex = if unsigned > 255 {
            format!(" (0x{unsigned:X})")
        } else {
            String::new()
        };
        return match hint {
            Some(h) => format!("{raw}{hex} — {h}"),
            None => format!("{raw}{hex}"),
        };
    }
    let lower = raw.to_ascii_lowercase();
    if let Some(rest) = lower.split("signal:").nth(1) {
        let token = rest.split_whitespace().next().unwrap_or("");
        let name = token.trim_matches(|c: char| !c.is_ascii_alphanumeric());
        if name == "sigkill" || name == "9" {
            return format!("{raw} — killed by the OS or another process");
        }
        if name == "sigsegv" || name == "11" {
            return format!("{raw} — segmentation fault, a native crash");
        }
        if name == "sigterm" || name == "15" {
            return format!("{raw} — asked to terminate");
        }
    }
    raw.to_string()
}

fn exit_code(raw: &str) -> Option<i64> {
    let lower = raw.to_ascii_lowercase();
    let digits = if let Some(rest) = lower.split("exit code:").nth(1) {
        rest.trim().split_whitespace().next().unwrap_or("")
    } else if raw.chars().all(|c| c == '-' || c.is_ascii_digit()) {
        raw
    } else {
        return None;
    };
    digits.parse().ok()
}

#[derive(Clone, Debug)]
pub struct ServerInfo {
    pub mode: String,
    pub running: bool,
    pub healthy: bool,
    pub pid: Option<u32>,
    pub started_at_ms: Option<i64>,
    pub api_origin: String,
    pub desktop_version: String,
    pub launch_cmd: String,
    pub unexpected_exits: u32,
    pub last_exit_status: String,
    pub last_exit_at_ms: Option<i64>,
    pub last_error: String,
    pub logs_dir: String,
    pub output: Vec<String>,
    pub recovery_attempts: u32,
    pub recovered_at_ms: Option<i64>,
}

pub fn build_diagnostics(info: &ServerInfo, status: &str, version: &str, now_ms: i64) -> String {
    let mut lines = Vec::new();
    let stamp = chrono_stamp(now_ms);
    lines.push(format!("Remedy server diagnostics — {stamp}"));
    lines.push(format!("indicator: {status}  api version: {}", if version.is_empty() { "?" } else { version }));
    lines.push(format!(
        "mode: {}  running: {}  healthy: {}  pid: {}  uptime: {}",
        info.mode,
        info.running,
        info.healthy,
        info.pid.map(|p| p.to_string()).unwrap_or_else(|| "-".into()),
        format_uptime(info.started_at_ms, now_ms),
    ));
    lines.push(format!("origin: {}  desktop: {}", info.api_origin, info.desktop_version));
    lines.push(format!("launch: {}", if info.launch_cmd.is_empty() { "-".into() } else { info.launch_cmd.clone() }));
    lines.push(format!("unexpected exits: {}", info.unexpected_exits));
    if !info.last_exit_status.is_empty() {
        let recovered = info
            .recovered_at_ms
            .map(|t| format_ago(Some(t), now_ms))
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "no".into());
        lines.push(format!(
            "last exit: {} {} — recovery attempts {}, recovered: {recovered}",
            describe_exit(&info.last_exit_status),
            format_ago(info.last_exit_at_ms, now_ms),
            info.recovery_attempts,
        ));
    }
    if !info.last_error.is_empty() {
        lines.push(format!("last error: {}", info.last_error));
    }
    lines.push(format!("logs: {}", info.logs_dir));
    if !info.output.is_empty() {
        lines.push(String::new());
        lines.push("--- last server output ---".into());
        let start = info.output.len().saturating_sub(60);
        lines.extend(info.output[start..].iter().cloned());
    }
    lines.join("\n")
}

fn chrono_stamp(now_ms: i64) -> String {
    iso8601(now_ms)
}

/// Howard Hinnant's civil-from-days, so the diagnostics header is an ISO stamp.
fn iso8601(ms: i64) -> String {
    let secs = ms.div_euclid(1000);
    let days = secs.div_euclid(86_400);
    let tod = secs.rem_euclid(86_400);
    let hour = tod / 3600;
    let min = (tod % 3600) / 60;
    let sec = tod % 60;
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { y + 1 } else { y };
    format!("{year:04}-{month:02}-{d:02}T{hour:02}:{min:02}:{sec:02}Z")
}

pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

pub fn info_from(studio: &Studio, chrome: &Chrome) -> ServerInfo {
    let conn = studio.conn.get();
    let (running, healthy, mode) = match &conn {
        Conn::Ready => (true, true, "attached"),
        Conn::Connecting => (true, false, "starting"),
        Conn::Error(_) => (false, false, "error"),
        Conn::Disconnected => (false, false, "down"),
    };
    ServerInfo {
        mode: mode.into(),
        running,
        healthy,
        pid: None,
        started_at_ms: None,
        api_origin: origin(),
        desktop_version: crate::updates::VERSION.into(),
        launch_cmd: String::new(),
        unexpected_exits: 0,
        last_exit_status: String::new(),
        last_exit_at_ms: None,
        last_error: match conn {
            Conn::Error(e) => e,
            _ => String::new(),
        },
        logs_dir: prefs::logs_dir().display().to_string(),
        output: chrome.server_output.get(),
        recovery_attempts: 0,
        recovered_at_ms: None,
    }
}

pub fn indicator(studio: &Studio) -> &'static str {
    match studio.conn.get() {
        Conn::Ready => "connected",
        Conn::Connecting => "connecting",
        Conn::Error(_) => "error",
        Conn::Disconnected => "disconnected",
    }
}

fn menu_button(ui: &Ui, id: &'static str, on: impl Fn() + 'static) -> El {
    let u = ui.clone();
    w::button(ui, move || messages().text(&u, id), on).variant("quiet")
}

pub fn panel(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let info = info_from(&studio, &chrome);
    let status = indicator(&studio);
    let text = build_diagnostics(&info, status, crate::updates::VERSION, now_ms());
    let c = chrome;
    let u = ui.clone();
    let copied = text.clone();
    let c_r = chrome;
    let s_r = studio.clone();
    let u_r = ui.clone();
    let c_l = chrome;
    let u_l = ui.clone();
    let c_d = chrome;
    let u_d = ui.clone();
    let show_out = chrome.server_output_open;
    let u_o = ui.clone();
    let out_ui = ui.clone();
    let out_host = ui.el("server-output");
    let out_h = out_host.clone();
    let c_out = chrome;
    let out_host = out_host.bind(move || {
        out_h.clear();
        if !c_out.server_output_open.get() {
            return;
        }
        let lines = c_out.server_output.get();
        let body = if lines.is_empty() { "—".into() } else { lines.join("\n") };
        let _ = out_h.clone().child(w::text(&out_ui, body).variant("muted"));
    });
    w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 3.0))
        .style(crate::surface_of(ui))
        .layout(|l| {
            l.width = Length::Px(300.0);
            l.max_height = Length::Px(360.0);
        })
        .child(w::text(ui, format!("{status} · {}", origin())).variant("muted"))
        .child(w::text(ui, format!("uptime {}", format_uptime(info.started_at_ms, now_ms()))))
        .child(menu_button(ui, "server-copy", move || {
            if let Some(app) = c.app() {
                app.copy(copied.clone(), false);
            }
            c.toast(&u, messages().text(&u, "toast-copied"));
        }))
        .child(menu_button(ui, "server-restart", move || restart(&s_r, c_r, &u_r)))
        .child(menu_button(ui, "server-logs", move || reveal(prefs::logs_dir(), c_l, &u_l)))
        .child(menu_button(ui, "server-data", move || reveal(prefs::data_dir(), c_d, &u_d)))
        .child(menu_button(ui, "server-output", move || {
            let _ = &u_o;
            show_out.update(|v| *v = !*v);
        }))
        .child(out_host)
}

fn reveal(path: std::path::PathBuf, chrome: Chrome, ui: &Ui) {
    if !prefs::reveal(&path) {
        chrome.toast(
            ui,
            messages().format(
                ui,
                "toast-folder",
                &[("path", path.display().to_string().into())],
            ),
        );
    }
}

fn restart(studio: &Studio, chrome: Chrome, ui: &Ui) {
    if runtime::spawn_disabled() {
        chrome.toast(ui, messages().text(ui, "toast-restartOff"));
        chrome.push_output("restart skipped (REMEDY_DESKTOP_NO_SPAWN)");
        return;
    }
    runtime::ensure_running();
    chrome.toast(ui, messages().text(ui, "toast-restartAsk"));
    chrome.push_output("restart requested");
    if let Some(client) = studio.client() {
        let c = chrome;
        let waker = remedy_ui::reactive::waker().ok();
        runtime::get_json(&client, "ping", move |res| {
            let line = match res {
                Ok(_) => "server answered ping".to_string(),
                Err(e) => format!("ping failed: {e}"),
            };
            let apply = move || c.push_output(line);
            if let Some(w) = waker {
                let _ = w.post(apply);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uptime_and_exit_match_the_current_app() {
        assert_eq!(format_uptime(None, 10_000), "—");
        assert_eq!(format_uptime(Some(0), 5_000), "5s");
        assert_eq!(format_uptime(Some(0), 125_000), "2m 5s");
        let crash = describe_exit("exit code: 1");
        assert!(crash.contains("exited with an error"), "{crash}");
        let kill = describe_exit("signal: 9 (SIGKILL)");
        assert!(kill.contains("killed by the OS"), "{kill}");
        assert_eq!(format_ago(Some(0), 2_000), "just now");
    }
}
