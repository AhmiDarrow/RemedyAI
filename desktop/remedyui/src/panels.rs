//! Overlay bodies: About, Diagnostics, Time Travel, Settings, quit.

use remedy_ui::a11y::{Role, Semantics};
use remedy_ui::core::Insets;
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::reactive::Signal;
use remedy_ui::style::StylePatch;
use remedy_ui::widgets as w;
use remedy_ui::{CaughtError, El, Retry, Ui};

use crate::chrome::Chrome;
use crate::messages;
use crate::prefs;
use crate::runtime;
use crate::studio::Studio;
use crate::titlebar;
use crate::updates::VERSION;

const SOURCE: &str = "https://github.com/AhmiDarrow/RemedyAI";

const LANGS: &[(&str, &str)] = &[
    ("auto", "Auto (match you)"),
    ("en-US", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("it", "Italiano"),
    ("ja", "日本語"),
    ("ko", "한국어"),
    ("pl", "Polski"),
    ("pt", "Português"),
    ("ru", "Русский"),
    ("zh-Hans", "简体中文"),
];

pub fn crash(ui: &Ui, err: &CaughtError, retry: Retry, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let detail = format!("{err}\n{}\n{}", err.element_path, err.phase.describe());
    let copy_text = detail.clone();
    let u = ui.clone();
    w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 4.0))
        .style(crate::surface_of(ui))
        .child(
            w::text(ui, messages().text(ui, "error-badge"))
                .style(StylePatch::new().foreground(colors.danger).background(colors.danger.with_alpha(0.12)).radius(ui.theme().radii.small).padding(Insets::all(unit))),
        )
        .child(w::heading(ui, 2, messages().text(ui, "error-title")))
        .child(w::text(ui, messages().text(ui, "error-body")).variant("muted"))
        .child(w::text(ui, detail).variant("muted"))
        .child(
            w::row(ui)
                .gap(unit * 2.0)
                .child(w::button(ui, move || messages().text(&u, "error-copy"), move || {
                    if let Some(app) = chrome.app() {
                        app.copy(copy_text.clone(), false);
                    }
                }))
                .child(w::button(ui, {
                    let u = ui.clone();
                    move || messages().text(&u, "error-continue")
                }, move || retry.retry()).variant("primary")),
        )
}

pub fn about_body(ui: &Ui, studio: &Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let user = studio.user_name.get();
    let blurb = messages().format(ui, "about-blurb", &[("home", "~/.remedy".into())]);
    let icon = about_icon(ui);
    w::column(ui)
        .gap(unit * 2.0)
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit * 2.0)
                .child(icon)
                .child(titlebar::wordmark(ui)),
        )
        .child(w::text(ui, messages().text(ui, "menu-about")).style(StylePatch::new().foreground(colors.text)))
        .child(w::text(ui, blurb).variant("muted"))
        .child(w::text(ui, messages().text(ui, "about-fromCreator")).variant("muted"))
        .child(w::text(ui, messages().text(ui, "about-creator")))
        .child(w::text(ui, format!("{} {VERSION}", messages().text(ui, "about-version"))))
        .child(w::text(ui, messages().text(ui, "about-platform")).variant("muted"))
        .child(w::text(ui, messages().format(ui, "about-signedIn", &[("userName", user.into())])))
        .child(
            w::row(ui)
                .gap(unit * 2.0)
                .child(quiet(ui, "about-whatsNew", move || {
                    chrome.help_id.set("13-whats-new".into());
                    chrome.help_query.set(String::new());
                    chrome.overlay.set(crate::chrome::Overlay::Help);
                }))
                .child(quiet(ui, "about-source", move || chrome.open_url(SOURCE))),
        )
        .child(w::text(ui, messages().text(ui, "about-signature")).variant("muted"))
}

fn about_icon(ui: &Ui) -> El {
    let decoded = remedy_ui::image::decode(include_bytes!("../assets/icon.png"), Some((88, 88))).ok();
    if let Some(decoded) = decoded {
        let image = remedy_ui::paint::Image::new(decoded.image.width, decoded.image.height, decoded.image.pixels);
        w::image(ui, image, "Remedy").layout(|l| {
            l.width = Length::Px(44.0);
            l.height = Length::Px(44.0);
        })
    } else {
        w::text(ui, "R")
    }
}

pub fn diagnostics_body(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let host = ui.el("diag-body").column().gap(unit * 2.0);
    let slot = host.clone();
    let u = ui.clone();
    let pending = Signal::new(String::new());
    let host = host.bind(move || {
        let v = chrome.diag.get();
        let hive = chrome.hive.get();
        let pending_id = pending.get();
        slot.clear();
        let overall = v.get("overall").and_then(|s| s.as_str()).unwrap_or("");
        let label = match overall {
            "ok" => messages().text(&u, "diag-healthy"),
            "degraded" => messages().text(&u, "diag-degraded"),
            "error" => messages().text(&u, "diag-issues"),
            _ if v.is_null() => messages().text(&u, "diag-loading"),
            _ => messages().text(&u, "diag-unknown"),
        };
        let colors = u.theme().colors.clone();
        let mark = match overall {
            "ok" => colors.ok,
            "degraded" => colors.warn,
            "error" => colors.danger,
            _ => colors.muted,
        };
        let _ = slot.clone().child(
            w::text(&u, label.clone())
                .style(StylePatch::new().foreground(mark))
                .semantics(Semantics::new(Role::Label).label(label)),
        );
        if let Some(err) = v.get("error").and_then(|e| e.as_str()) {
            let _ = slot.clone().child(w::text(&u, err).variant("muted"));
        }
        if v.is_null() && studio.client().is_none() {
            let _ = slot.clone().child(w::text(&u, messages().text(&u, "diag-offline")).variant("muted"));
        }
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-remedy"), &json_line(&v, &[("/remedy/version", "version"), ("/remedy/active_model", "model"), ("/remedy/home_dir", "home")])));
        let listening = v.pointer("/remedy/api/listening").and_then(|n| n.as_bool());
        let api = match listening {
            Some(true) => "127.0.0.1:7400 listening".to_string(),
            Some(false) => "not listening".to_string(),
            None => String::new(),
        };
        if !api.is_empty() {
            let _ = slot.clone().child(w::text(&u, api).variant("muted"));
        }
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-rmb"), &flag_line(&v, "/rmb/ready", "ready")));
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-hardware"), &json_line(&v, &[("/hardware/platform", "platform"), ("/hardware/system", "system")])));
        let providers = v.pointer("/providers/connected_count").and_then(|n| n.as_i64()).unwrap_or(0);
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-providers"), &format!("{providers} connected")));
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-computer"), &flag_line(&v, "/computer/ok", "ok")));
        let _ = slot.clone().child(kv(&u, &messages().text(&u, "diag-vision"), &flag_line(&v, "/vision/ready", "ready")));
        let _ = slot.clone().child(hive_block(&u, &hive, pending, pending_id, studio.clone(), chrome));
        let s = studio.clone();
        let _ = slot.clone().child(quiet(&u, "diag-refresh", move || chrome.load_diag(&s)));
    });
    host
}

fn hive_block(ui: &Ui, hive: &serde_json::Value, pending: Signal<String>, pending_id: String, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let mut col = w::column(ui).gap(unit).child(w::heading(ui, 3, messages().text(ui, "diag-hive")));
    let live = hive.get("daughters").and_then(|d| d.as_array()).map(|rows| rows.iter().filter(|r| {
        let status = r.get("status").and_then(|s| s.as_str()).unwrap_or("");
        status != "retired" && status != "cancelled"
    }).count()).unwrap_or(0);
    col = col.child(w::text(ui, format!("Daughters Remedy hired. They report to them, not you. · {live} live")).variant("muted"));
    let Some(rows) = hive.get("daughters").and_then(|d| d.as_array()) else {
        return col;
    };
    for row in rows {
        let id = row.get("id").and_then(|s| s.as_str()).unwrap_or("").to_string();
        let cadence = row.get("cadence").and_then(|s| s.as_str()).unwrap_or("");
        let status = row.get("status").and_then(|s| s.as_str()).unwrap_or("");
        let goal = row.get("goal").and_then(|s| s.as_str()).unwrap_or("untitled job");
        let label = if pending_id == id { "Confirm stop" } else { "Stop" };
        let hid = id.clone();
        let s = studio.clone();
        let p = pending;
        col = col.child(
            w::row(ui)
                .align(Align::Center)
                .justify(Justify::SpaceBetween)
                .gap(unit * 2.0)
                .child(w::text(ui, format!("{cadence} · {status} · {goal}")))
                .child(w::button(ui, label, move || {
                    if p.get() == hid {
                        chrome.retire_hive(&s, &hid);
                        p.set(String::new());
                    } else {
                        p.set(hid.clone());
                    }
                }).variant("quiet")),
        );
    }
    col
}

pub fn timeline_body(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let host = ui.el("time-body").column().gap(unit * 2.0);
    let slot = host.clone();
    let u = ui.clone();
    let host = host.bind(move || {
        slot.clear();
        if studio.active.get().is_none() {
            let _ = slot.clone().child(w::text(&u, messages().text(&u, "time-none")).variant("muted"));
            return;
        }
        let v = chrome.timeline.get();
        if v.is_null() {
            let _ = slot.clone().child(w::text(&u, messages().text(&u, "time-loading")).variant("muted"));
            return;
        }
        let steps = v.get("steps").and_then(|a| a.as_array()).cloned().unwrap_or_default();
        let user_steps: Vec<_> = steps.into_iter().filter(|s| s.get("kind").and_then(|k| k.as_str()) == Some("user")).collect();
        if user_steps.is_empty() {
            let _ = slot.clone().child(w::text(&u, messages().text(&u, "time-empty")).variant("muted"));
            return;
        }
        for step in user_steps {
            let label = step.get("label").and_then(|s| s.as_str()).unwrap_or("step").to_string();
            let preview = step.get("preview").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let message = step.get("message_id").and_then(|s| s.as_str()).unwrap_or("").to_string();
            let session = studio.active.get().unwrap_or_default();
            let can = step.get("can_restore").and_then(|b| b.as_bool()).unwrap_or(true);
            let _ = slot.clone().child(w::text(&u, label));
            if !preview.is_empty() {
                let _ = slot.clone().child(w::text(&u, preview).variant("muted"));
            }
            if can && !message.is_empty() {
                let m = message.clone();
                let sess = session.clone();
                let _ = slot.clone().child(w::button(&u, messages().text(&u, "time-restore"), move || {
                    chrome.confirm.set(crate::chrome::Confirm::TimeTravel { session: sess.clone(), message: m.clone() });
                }).variant("quiet"));
            }
        }
    });
    host
}

pub fn confirm_card(ui: &Ui, studio: Studio, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let (title, go) = match chrome.confirm.get() {
        crate::chrome::Confirm::TimeTravel { session, message } => {
            let s = studio.clone();
            let m = message.clone();
            (
                messages().text(ui, "time-confirm"),
                Box::new(move || {
                    chrome.restore_step(&s, &m);
                    chrome.confirm.set(crate::chrome::Confirm::None);
                    let _ = session;
                }) as Box<dyn Fn()>,
            )
        }
        crate::chrome::Confirm::None => (messages().text(ui, "confirm-body"), Box::new(|| {}) as Box<dyn Fn()>),
    };
    w::column(ui)
        .gap(unit * 2.0)
        .padding(Insets::all(unit * 3.0))
        .style(crate::surface_of(ui))
        .layout(|l| l.width = Length::Px(380.0))
        .semantics(Semantics::new(Role::Dialog).label(messages().text(ui, "confirm-title")).modal(true))
        .child(w::heading(ui, 3, messages().text(ui, "confirm-title")))
        .child(w::text(ui, title).variant("muted"))
        .child(
            w::row(ui)
                .justify(Justify::End)
                .gap(unit * 2.0)
                .child(w::button(ui, messages().text(ui, "confirm-cancel"), move || chrome.confirm.set(crate::chrome::Confirm::None)).variant("quiet"))
                .child(w::button(ui, messages().text(ui, "confirm-ok"), go).style(StylePatch::new().background(colors.danger).foreground(colors.on_accent))),
        )
}

pub fn quit_body(ui: &Ui, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let skip = Signal::new(false);
    let host = runtime::origin().trim_start_matches("https://").trim_start_matches("http://").to_string();
    let hint = if cfg!(target_os = "linux") {
        "quit-hintLinux"
    } else if cfg!(target_os = "macos") {
        "quit-hintMac"
    } else {
        "quit-hintOther"
    };
    let extra = if cfg!(target_os = "linux") {
        "quit-extraLinux"
    } else if cfg!(target_os = "macos") {
        "quit-extraMac"
    } else {
        "quit-extraOther"
    };
    w::column(ui)
        .gap(unit * 2.0)
        .child(w::text(ui, format!("{} {host}", messages().text(ui, "quit-body1"))))
        .child(w::text(ui, format!("{} {}", messages().text(ui, "quit-body2"), messages().text(ui, extra))).variant("muted"))
        .child(w::text(ui, messages().text(ui, hint)).variant("muted"))
        .child(w::check(ui, messages().text(ui, "quit-dontWarn"), skip))
        .child(
            w::row(ui)
                .justify(Justify::End)
                .gap(unit * 2.0)
                .child(quiet(ui, "quit-cancel", move || chrome.close_overlay()))
                .child(quiet(ui, "quit-webui", {
                    let u = ui.clone();
                    move || {
                        chrome.open_web(&u);
                        chrome.close_overlay();
                    }
                }))
                .child(
                    w::button(ui, messages().text(ui, "quit-confirm"), move || chrome.confirm_quit(skip.get()))
                        .style(StylePatch::new().background(colors.danger).foreground(colors.on_accent)),
                ),
        )
}

pub fn settings_body(ui: &Ui, chrome: Chrome) -> El {
    let unit = ui.theme().spacing.unit;
    let lang_idx = Signal::new(lang_index(&chrome.locale.get()));
    let font_idx = Signal::new(chrome.font_idx.get() as usize);
    let langs: Vec<String> = LANGS.iter().map(|(_, name)| (*name).to_string()).collect();
    let fonts = vec![
        messages().text(ui, "font-sm"),
        messages().text(ui, "font-md"),
        messages().text(ui, "font-lg"),
        messages().text(ui, "font-xl"),
    ];
    let font_name = fonts.get(chrome.font_idx.get() as usize).cloned().unwrap_or_else(|| "Medium".into());
    let u_lang = ui.clone();
    let c_lang = chrome;
    let lang_watch = ui.el("lang-watch").bind(move || {
        let i = lang_idx.get().min(LANGS.len() - 1);
        let id = LANGS[i].0;
        if c_lang.locale.get() != id {
            c_lang.set_locale(&u_lang, id);
        }
    });
    let u_font = ui.clone();
    let c_font = chrome;
    let font_watch = ui.el("font-watch").bind(move || {
        let idx = font_idx.get().min(3) as u8;
        if c_font.font_idx.get() != idx {
            c_font.set_font(&u_font, idx);
        }
    });
    w::column(ui)
        .gap(unit * 3.0)
        .child(w::text(ui, messages().text(ui, "settings-language")))
        .child(w::text(ui, messages().text(ui, "settings-languageHintShort")).variant("muted"))
        .child(w::select(ui, messages().text(ui, "settings-language"), langs, lang_idx))
        .child(lang_watch)
        .child(w::text(ui, messages().text(ui, "settings-uiMode")))
        .child(w::check(ui, messages().text(ui, "bar-advanced"), chrome.advanced))
        .child(flip(ui, chrome.advanced, move |on| {
            chrome.advanced.set(on);
            chrome.save_prefs();
        }))
        .child(w::check(ui, messages().text(ui, "settings-launch"), chrome.launch_at_login))
        .child(flip(ui, chrome.launch_at_login, move |on| {
            prefs::set_launch_at_login(on);
            chrome.save_prefs();
        }))
        .child(w::check(ui, messages().text(ui, "settings-startInTray"), chrome.start_in_tray))
        .child(flip(ui, chrome.start_in_tray, move |_| chrome.save_prefs()))
        .child(w::check(ui, messages().text(ui, "settings-skipQuit"), chrome.skip_quit))
        .child(flip(ui, chrome.skip_quit, move |_| chrome.save_prefs()))
        .child(w::text(ui, messages().text(ui, "settings-font")))
        .child(w::text(ui, font_name))
        .child(w::select(ui, messages().text(ui, "settings-font"), fonts, font_idx))
        .child(font_watch)
        .child(w::check(ui, messages().text(ui, "settings-phone"), chrome.connect_compact))
}

pub fn memory_body(ui: &Ui, chrome: Chrome) -> El {
    let studio = chrome
        .studio
        .get()
        .unwrap_or_else(Studio::preview);
    crate::depth::panels::memory_sheet(ui, studio)
}

pub fn skills_body(ui: &Ui, chrome: Chrome) -> El {
    let studio = chrome
        .studio
        .get()
        .unwrap_or_else(Studio::preview);
    if studio.runtime().is_none() {
        seed_skills_preview(&studio);
    }
    crate::depth::panels::skills_sheet(ui, studio)
}


fn seed_skills_preview(studio: &crate::studio::Studio) {
    use crate::depth::logic::{SkillBudget, SkillLearning};
    use crate::depth::SkillRow;
    studio.depth.skills.set(vec![
        SkillRow {
            name: "summarize".into(),
            description: "Shorten a long page or thread.".into(),
            status: "installed".into(),
            quarantine: false,
            auto_generated: false,
        },
        SkillRow {
            name: "receipts".into(),
            description: "Read a receipt photo and total it.".into(),
            status: "installed".into(),
            quarantine: false,
            auto_generated: false,
        },
        SkillRow {
            name: "recipe-notes".into(),
            description: "Learned from the Recipes thread. Still on probation.".into(),
            status: "discovered".into(),
            quarantine: false,
            auto_generated: true,
        },
        SkillRow {
            name: "house-style".into(),
            description: "Keep replies in Ahmi's voice and spelling.".into(),
            status: "active".into(),
            quarantine: false,
            auto_generated: true,
        },
        SkillRow {
            name: "imported-pack-alpha".into(),
            description: "Imported from a ZIP. Trust before it can run.".into(),
            status: "installed".into(),
            quarantine: true,
            auto_generated: false,
        },
        SkillRow {
            name: "old-scraper".into(),
            description: "Unused for 4 months.".into(),
            status: "archived".into(),
            quarantine: false,
            auto_generated: false,
        },
    ]);
    studio.depth.skill_budget.set(SkillBudget {
        active_count: 72,
        active_budget: 80,
        banner: "72 / 80 in active set — archive or pack to stay sharp".into(),
    });
    studio.depth.skill_learning.set(SkillLearning {
        learned_count: 6,
        probation_count: 2,
        total_activations: 41,
        multi_session: 3,
    });
}

fn lang_index(id: &str) -> usize {
    LANGS.iter().position(|(k, _)| *k == id).unwrap_or(0)
}

fn flip(ui: &Ui, sig: Signal<bool>, f: impl Fn(bool) + 'static) -> El {
    let prev = Signal::new(sig.get());
    ui.el("flip").layout(|l| {
        l.height = Length::Px(0.0);
        l.width = Length::Px(0.0);
    }).bind(move || {
        let now = sig.get();
        if prev.get() != now {
            prev.set_if_changed(now);
            f(now);
        }
    })
}

fn quiet(ui: &Ui, id: &'static str, on: impl Fn() + 'static) -> El {
    let u = ui.clone();
    w::button(ui, move || messages().text(&u, id), on).variant("quiet")
}

fn kv(ui: &Ui, k: &str, v: &str) -> El {
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(ui.theme().spacing.unit * 2.0)
        .child(w::text(ui, k))
        .child(w::text(ui, v).variant("muted"))
}

fn json_line(v: &serde_json::Value, fields: &[(&str, &str)]) -> String {
    let mut parts = Vec::new();
    for (ptr, name) in fields {
        if let Some(s) = v.pointer(ptr).and_then(|n| n.as_str()) {
            if !s.is_empty() {
                parts.push(format!("{name} {s}"));
            }
        }
    }
    if parts.is_empty() { "—".into() } else { parts.join(" · ") }
}

fn flag_line(v: &serde_json::Value, ptr: &str, word: &str) -> String {
    match v.pointer(ptr).and_then(|n| n.as_bool()) {
        Some(true) => word.into(),
        Some(false) => format!("not {word}"),
        None => "—".into(),
    }
}
