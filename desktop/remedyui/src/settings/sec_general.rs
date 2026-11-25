//! General: you and the agent, voice, workspace, appearance.

use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use serde_json::json;

use super::bits;
use super::form;
use super::logic;
use super::state::Settings;

pub fn sections(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(you_agent(ui, s))
        .child(voice(ui, s))
        .child(workspace(ui, s))
        .child(theme(ui, s))
}

fn you_agent(ui: &Ui, s: Settings) -> El {
    let mut armed = false;
    Effect::new(move || {
        let lang = s.ui_language.get();
        if !armed {
            armed = true;
            return;
        }
        if s.preview {
            return;
        }
        let lang = if lang.trim().is_empty() { "auto".into() } else { lang };
        super::http::put_settings(s, json!({ "ui_language": lang }), move |out| {
            s.report(out, "Language saved.");
        });
    });
    let help = form::hint(ui, "Help articles stay in English.");
    let help = bits::visible_when(help, move || s.mode.get() == "advanced" || logic::searching(&s.search.get()));
    let persona_hint = w::text(ui, move || persona_hint(&s.persona.get()).to_string()).variant("small");
    form::block(ui, s, "you-agent", move |ui, s| {
        let langs = s.ui_languages.get();
        form::stack(ui)
            .child(bits::picker(ui, "Language", &langs, s.ui_language))
            .child(form::hint(ui, "Menus follow this language. Auto uses the system language."))
            .child(help.clone())
            .child(bits::field(ui, "Your name", s.user_name, "Ada"))
            .child(bits::field(ui, "Partner name", s.agent_name, "Remedy"))
            .child(bits::choice(
                ui,
                "Partner",
                &[("female", "Female"), ("male", "Male"), ("neutral", "Neutral")],
                s.agent_gender,
            ))
            .child(bits::picker(
                ui,
                "Communication style",
                &[
                    ("balanced".into(), "Balanced".into()),
                    ("efficient".into(), "Efficient".into()),
                    ("detailed".into(), "Detailed".into()),
                    ("playful".into(), "Playful".into()),
                ],
                s.persona,
            ))
            .child(persona_hint.clone())
    })
}

fn persona_hint(id: &str) -> &'static str {
    match id {
        "efficient" => "Concise, code-first, minimal explanation.",
        "detailed" => "Thorough explanations with context.",
        "playful" => "Casual tone with light humor.",
        _ => "Helpful and adaptable to the task.",
    }
}

fn voice(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "voice");
    form::block(ui, s, "voice", move |ui, s| {
        let speak = Signal::new(voice_bool(&s.voice.get(), "settings", "speak_replies"));
        let stt_on = Signal::new(voice_bool(&s.voice.get(), "stt", "enabled"));
        Effect::new(move || {
            let v = s.voice.get();
            speak.set_if_changed(voice_bool(&v, "settings", "speak_replies"));
            stt_on.set_if_changed(voice_bool(&v, "stt", "enabled"));
        });
        Effect::new(move || {
            let on = speak.get();
            if s.preview {
                return;
            }
            let server = voice_bool(&s.voice.get_untracked().unwrap_or(serde_json::Value::Null), "settings", "speak_replies");
            if on == server {
                return;
            }
            crate::depth::note_speak_replies(on);
            bits::ask(s, "POST", "voice/settings", Some(json!({ "speak_replies": on })), "Voice updated.");
        });
        Effect::new(move || {
            let on = stt_on.get();
            if s.preview {
                return;
            }
            let server = voice_bool(&s.voice.get_untracked().unwrap_or(serde_json::Value::Null), "stt", "enabled");
            if on == server {
                return;
            }
            bits::ask(s, "POST", "voice/settings", Some(json!({ "stt_enabled": on })), "Hearing updated.");
        });
        let status = w::text(ui, move || voice_status_line(&s.voice.get())).variant("small");
        let advanced = form::stack(ui)
            .child(form::toggle(ui, "Voice input", "Turn speech into text on this computer.", stt_on))
            .child(form::action(ui, "Download hearing", "quiet", move || {
                bits::ask(s, "POST", "voice/install", Some(json!({ "component": "stt" })), "Hearing download started.");
            }))
            .child(form::hint(
                ui,
                "Turn-taking keeps Remedy from talking over you. About 9 MB. Until it arrives, calls use energy detection.",
            ))
            .child(form::action(ui, "Download turn-taking", "quiet", move || {
                bits::ask(s, "POST", "voice/install", Some(json!({ "component": "smart-turn" })), "Turn-taking download started.");
            }));
        let advanced = bits::visible_when(advanced, move || s.mode.get() == "advanced" || logic::searching(&s.search.get()));
        form::stack(ui)
            .child(status)
            .child(form::toggle(
                ui,
                "Speak replies aloud",
                "Speech is generated on this computer once Remedy’s voice is ready.",
                speak,
            ))
            .child(form::action(ui, "Download Remedy’s voice", "primary", move || {
                bits::ask(s, "POST", "voice/install", Some(json!({ "component": "all" })), "Voice download started.");
            }))
            .child(advanced)
    })
}

fn voice_bool(v: &serde_json::Value, group: &str, key: &str) -> bool {
    v.get(group).and_then(|g| g.get(key)).and_then(|x| x.as_bool()).unwrap_or(false)
}

fn voice_status_line(v: &serde_json::Value) -> String {
    if v.is_null() {
        return "Voice status loads when this section is open.".into();
    }
    let tts = v.get("tts").and_then(|t| t.get("installed")).and_then(|x| x.as_bool()).unwrap_or(false);
    let stt = v.get("stt").and_then(|t| t.get("installed")).and_then(|x| x.as_bool()).unwrap_or(false);
    if tts {
        "Remedy’s voice is ready on this computer.".into()
    } else if stt {
        "Hearing is installed. Remedy’s speaking voice is not on this computer yet.".into()
    } else {
        "Remedy’s voice is not on this computer yet.".into()
    }
}

fn workspace(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "workspace", move |ui, s| {
        let path = w::row(ui)
            .align(remedy_ui::layout::Align::Center)
            .gap(form::gap(ui))
            .fill_width()
            .child(form::bound_line(ui, "Project folder", s.project_path, "C:\\Users\\You\\Projects\\MyApp").grow(1.0))
            .child(form::action(ui, "Browse", "quiet", move || bits::browse_folder(s)));
        form::stack(ui)
            .child(form::label(ui, "Default project"))
            .child(path)
            .child(form::hint(ui, "Tools and files use this folder. Click Save to load the project. An empty folder uses the default workspace in Access & permissions."))
            .child(bits::field(ui, "Browser homepage", s.browser_home, logic::DEFAULT_BROWSER_HOME))
            .child(form::hint(ui, "In-app Browser home. Blank becomes the Remedy GitHub repo. Use http(s) only."))
            .child(form::toggle(
                ui,
                "Browser Privacy Shield",
                "Saves the preference on this computer. Filter-list download and blocking are not in this shell.",
                s.privacy_shield,
            ))
            .child(form::action(ui, "Update lists", "quiet", move || {
                s.banner.set("Filter lists are not downloaded by this shell.".into());
            }))
    })
}

fn theme(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "theme", move |ui, s| {
        let mut rows = form::stack(ui);
        for item in logic::theme_menu() {
            let id = item.id.clone();
            let id_mark = id.clone();
            let bg = form::parse_hex(&item.bg).unwrap_or(remedy_ui::core::Color::BLACK);
            let accent = form::parse_hex(&item.accent).unwrap_or(remedy_ui::core::Color::WHITE);
            let split = item.split_bg.as_deref().and_then(form::parse_hex);
            let mark = w::text(ui, move || if s.theme_id.get() == id_mark { "✓".into() } else { String::new() });
            let row = w::row(ui)
                .align(remedy_ui::layout::Align::Center)
                .gap(form::gap(ui))
                .fill_width()
                .child(form::swatch(ui, bg, accent, split))
                .child(w::text(ui, item.name).grow(1.0))
                .child(mark)
                .on_click(move || s.theme_id.set(id.clone()));
            rows = rows.child(row);
        }
        let mut fonts = form::stack(ui);
        for (role, title, hint, _) in logic::FONT_ROLES {
            let pairs: Vec<(String, String)> = logic::faces_for_role(role)
                .into_iter()
                .map(|id| (id.to_string(), logic::face_label(id)))
                .collect();
            let value = match *role {
                "heading" => s.font_heading,
                "reading" => s.font_reading,
                "accent" => s.font_accent,
                "code" => s.font_code,
                _ => s.font_ui,
            };
            fonts = fonts
                .child(bits::picker(ui, title, &pairs, value))
                .child(form::hint(ui, (*hint).to_string()));
        }
        form::stack(ui)
            .child(form::label(ui, "Theme"))
            .child(form::hint(ui, "Forest is the Remedy default. Colorblind palettes keep their own accents."))
            .child(rows)
            .child(bits::choice(ui, "Density", &[("cozy", "Cozy"), ("compact", "Compact")], s.density))
            .child(bits::choice(
                ui,
                "Text size",
                &[("sm", "Small"), ("md", "Medium"), ("lg", "Large"), ("xl", "Extra large")],
                s.font_scale,
            ))
            .child(form::hint(ui, "Ctrl+= and Ctrl+- change text size from anywhere."))
            .child(fonts)
            .child(bits::field(ui, "Custom accent", s.custom_accent, "#3D8F6E"))
            .child(form::hint(ui, "Hex color. Blank uses the theme accent. Light accents get dark text."))
            .child(form::toggle(ui, "Reduce motion", "Keep movement short and still.", s.reduce_motion))
            .child(form::toggle(
                ui,
                "Remedy's presence",
                "A small geometric mark that shows whether she is idle, listening, thinking, working, done, or in error. Off leaves a quieter shell.",
                s.show_presence,
            ))
            .child(form::toggle(ui, "High contrast", "Stronger text and borders, including when the system asks for it.", s.high_contrast))
            .child(form::action(ui, "Reset fonts", "quiet", move || super::prefs::reset_fonts(s)))
            .child(theme_pack_editor(ui))
    })
}

fn theme_pack_editor(ui: &Ui) -> El {
    let Some(custom) = crate::theme_custom::current() else {
        return form::stack(ui);
    };
    form::stack(ui)
        .child(form::label(ui, "Theme pack"))
        .child(form::hint(
            ui,
            "Your own tokens, on top of the selected theme. Import a pack or edit the one saved on this PC.",
        ))
        .child(remedy_ui::customize::theme_editor(ui, &custom))
}
