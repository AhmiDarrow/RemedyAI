//! Small helpers shared by the settings sections.

use remedy_ui::layout::Length;
use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use serde_json::{Value, json};

use super::http;
use super::state::Settings;
use super::{form, logic, prefs};

pub fn field(ui: &Ui, name: &str, value: Signal<String>, placeholder: &str) -> El {
    form::stack(ui)
        .child(form::label(ui, name))
        .child(form::bound_line(ui, name, value, placeholder))
}

pub fn choice(ui: &Ui, name: &str, pairs: &[(&str, &str)], value: Signal<String>) -> El {
    form::stack(ui)
        .child(form::label(ui, name))
        .child(form::segments(ui, name, pairs, value))
}

pub fn picker(ui: &Ui, name: &str, pairs: &[(String, String)], value: Signal<String>) -> El {
    form::stack(ui)
        .child(form::label(ui, name))
        .child(form::pick(ui, name, pairs, value))
}

/// Hides `el` (it stays mounted) whenever `when` is false.
pub fn visible_when(el: El, when: impl Fn() -> bool + 'static) -> El {
    let flag = el.clone();
    el.bind(move || {
        flag.clone().hidden(!when());
    })
}

/// Rebuilds `build` whenever `watch` reads a new value. `watch` must not write
/// a signal that it also reads.
pub fn rebuild_on(ui: &Ui, watch: impl Fn() + 'static, build: impl Fn(&Ui) -> El + 'static) -> El {
    let epoch = Signal::new(vec![0u64]);
    Effect::new(move || {
        watch();
        epoch.update(|v| {
            let n = v.first().copied().unwrap_or(0).wrapping_add(1);
            *v = vec![n];
        });
    });
    let host = ui.el("rebuild").column().fill_width();
    let ui_b = ui.clone();
    let _ = w::for_each(&host, epoch, |n| *n, move |_| build(&ui_b));
    host
}

/// Refetches a lazy section on the transition from closed to open.
pub fn when_open(s: Settings, id: &'static str) {
    let was = Signal::new(false);
    Effect::new(move || {
        let on = s.is_open(id);
        let prev = was.get_untracked().unwrap_or(false);
        if on != prev {
            was.set(on);
        }
        if on && !prev {
            s.reload_section(id);
        }
    });
}

pub fn open_http(url: &str) {
    let url = url.trim();
    if !(url.starts_with("https://") || url.starts_with("http://") || url.starts_with("mailto:")) {
        return;
    }
    let Some(app) = prefs::host() else { return };
    app.open_url(url.to_string(), |_| {});
}

/// Open a bundled Help chapter in the in-app manual, the same path as F1.
pub fn open_help_topic(id: &str) {
    crate::chrome::open_help_topic(id);
}

/// OAuth and provider verification. The in-app browser keeps Settings mounted.
/// Anything the host cannot take still opens in the system browser.
pub fn open_in_browser_rail(url: &str) {
    let url = url.trim();
    if url.is_empty() {
        return;
    }
    if crate::browser::open_in_rail(url, true) {
        return;
    }
    open_http(url);
}

pub fn browse_folder(s: Settings) {
    let Some(app) = prefs::host() else {
        s.banner.set("Folder picker is not available in this preview.".into());
        return;
    };
    let mut dialog = remedy_ui::host::FileDialog::default();
    dialog.title = "Choose a project folder".into();
    dialog.folders = true;
    app.file_dialog(None, dialog, move |paths| {
        if let Some(path) = paths.into_iter().next() {
            s.project_path.set(path.display().to_string());
            s.banner.set("Folder selected — click Save to load project and reload Remedy".into());
        }
    });
}

pub fn browse_default_workspace(s: Settings) {
    let Some(app) = prefs::host() else {
        s.banner.set("Folder picker is not available in this preview.".into());
        return;
    };
    let mut dialog = remedy_ui::host::FileDialog::default();
    dialog.title = "Choose the default workspace folder".into();
    dialog.folders = true;
    app.file_dialog(None, dialog, move |paths| {
        if let Some(path) = paths.into_iter().next() {
            s.default_workspace.set(path.display().to_string());
            s.banner.set("Default workspace selected — click Save to keep this folder.".into());
        }
    });
}

pub fn use_default_workspace(s: Settings) {
    let path = logic::default_workspace_display(&s.default_workspace.get());
    if let Err(err) = std::fs::create_dir_all(&path) {
        s.banner.set(format!("Could not create workspace folder: {err}"));
        return;
    }
    s.default_workspace.set(path);
    s.banner.set("Workspace folder ready — click Save to keep this path.".into());
}

pub fn ask(s: Settings, method: &str, path: &str, body: Option<Value>, ok: &str) {
    if s.preview {
        s.banner.set("Not available in this preview.".into());
        return;
    }
    let path = path.to_string();
    let ok = ok.to_string();
    http::request(s, method, path, body, move |out| s.report(out, &ok));
}

pub fn set_obj(root: &mut Value, key: &str, val: Value) {
    if !root.is_object() {
        *root = json!({});
    }
    if let Some(map) = root.as_object_mut() {
        map.insert(key.to_string(), val);
    }
}

pub fn str_of(v: &Value, key: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or("").to_string()
}

pub fn fill(el: El) -> El {
    el.layout(|l| {
        l.width = Length::Percent(1.0);
        l.height = Length::Percent(1.0);
    })
}
