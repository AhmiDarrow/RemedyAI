//! Models: provider, catalog, local models, vision, memory harness.

use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use serde_json::{Value, json};

use super::bits;
use super::form;
use super::logic::{self, Knob};
use super::state::{self, Settings};

pub fn sections(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(provider(ui, s))
        .child(catalog(ui, s))
        .child(rmb(ui, s))
        .child(vision(ui, s))
        .child(harness(ui, s))
}

fn provider(ui: &Ui, s: Settings) -> El {
    Effect::new(move || {
        let id = s.provider.get();
        let catalog = s.catalog.get();
        if let Some(p) = catalog.iter().find(|p| p.id == id) {
            if s.base_url.get_untracked().unwrap_or_default().is_empty() && !p.base_url.is_empty() {
                s.base_url.set(p.base_url.clone());
            }
            if s.model.get_untracked().unwrap_or_default().is_empty() && !p.default_model.is_empty() {
                s.model.set(p.default_model.clone());
            }
        }
        s.discover_models();
    });
    form::block(ui, s, "provider", move |ui, s| {
        let providers = bits::rebuild_on(
            ui,
            move || {
                let _ = s.catalog.get().len();
            },
            move |ui| provider_select(ui, s),
        );
        let models = bits::rebuild_on(
            ui,
            move || {
                let _ = s.models.get().len();
            },
            move |ui| model_select(ui, s),
        );
        let custom = bits::field(ui, "Custom endpoint name", s.custom_name, "LM Studio");
        let custom = bits::visible_when(custom, move || s.provider.get() == logic::CUSTOM_PROVIDER);
        let xai = xai_block(ui, s);
        let xai = bits::visible_when(xai, move || s.provider.get() == "xai");
        let sleev_warn = form::notice(
            ui,
            "warn",
            "Gateway host is not on this computer. Save stays blocked until you allow a remote gateway or use a loopback address.",
        );
        let sleev_warn = bits::visible_when(sleev_warn, move || logic::sleev_remote_blocked(&s.sleev_url.get(), s.sleev_allow_remote.get()));
        form::stack(ui)
            .child(providers)
            .child(custom)
            .child(bits::field(ui, "Base URL", s.base_url, "https://api.example/v1"))
            .child(form::hint(ui, "Switching providers fills this only when it is empty."))
            .child(models)
            .child(bits::field(ui, "Model", s.model, "Model id"))
            .child(w::text(ui, move || s.discovery.get()).variant("small"))
            .child(form::label(ui, "API key"))
            .child(form::secret_line(ui, "API key"))
            .child(form::hint(ui, move || -> String {
                if s.api_key_set.get() {
                    "A key is saved. Leave the field blank to keep it.".into()
                } else {
                    "No key saved yet.".into()
                }
            }))
            .child(form::action(ui, "Test connection", "quiet", move || {
                bits::ask(
                    s,
                    "POST",
                    "providers/probe",
                    Some(json!({ "provider": s.provider.get(), "base_url": s.base_url.get() })),
                    "Connection looks good.",
                );
            }))
            .child(form::action(ui, "Check Ollama", "quiet", move || {
                bits::ask(s, "GET", "providers/ollama/detect", None, "Ollama check finished.");
            }))
            .child(form::action(ui, "Save custom endpoint", "quiet", move || {
                bits::ask(
                    s,
                    "POST",
                    "providers/custom",
                    Some(json!({ "name": s.custom_name.get(), "base_url": s.base_url.get() })),
                    "Custom endpoint saved. The API key still goes with Save.",
                );
            }))
            .child(xai)
            .child(form::flow(
                ui,
                "Token savings",
                "Optional local compression gateway",
                false,
                move |ui| {
                    form::stack(ui)
                        .child(form::toggle(ui, "Use Sleev", "Route chat through a local Sleev gateway.", s.sleev_enabled))
                        .child(bits::field(ui, "Sleev gateway URL", s.sleev_url, "http://127.0.0.1:17321"))
                        .child(form::toggle(
                            ui,
                            "Allow a remote gateway",
                            "Required when the URL is not loopback. Save is refused until this is on.",
                            s.sleev_allow_remote,
                        ))
                        .child(sleev_warn.clone())
                        .child(form::action(ui, "Sleev docs", "quiet", || bits::open_http("https://sleev.ai/docs/quickstart")))
                },
            ))
    })
}

fn provider_select(ui: &Ui, s: Settings) -> El {
    let catalog = s.catalog.get();
    if catalog.is_empty() {
        return form::hint(ui, "Loading providers…");
    }
    let pairs: Vec<(String, String)> = catalog.into_iter().map(|p| (p.id, p.name)).collect();
    bits::picker(ui, "Provider", &pairs, s.provider)
}

fn model_select(ui: &Ui, s: Settings) -> El {
    let models = s.models.get();
    if models.is_empty() {
        return form::hint(ui, "No discovered models yet. Type an id below.");
    }
    let pairs: Vec<(String, String)> = models.into_iter().map(|m| (m.clone(), m)).collect();
    bits::picker(ui, "Discovered models", &pairs, s.model)
}

fn xai_block(ui: &Ui, s: Settings) -> El {
    let code = Signal::new(String::new());
    let url = Signal::new(String::new());
    let session = Signal::new(String::new());
    let line = w::text(ui, move || {
        let x = s.xai.get();
        let connected = x.get("connected").and_then(|v| v.as_bool()).unwrap_or(false);
        let method = bits::str_of(&x, "auth_method");
        if connected {
            format!("xAI connected ({method}).")
        } else if method.is_empty() {
            "xAI is not connected.".into()
        } else {
            format!("xAI status: {method}.")
        }
    })
    .variant("small");
    let shown = w::text(ui, move || {
        let c = code.get();
        if c.is_empty() { String::new() } else { format!("Code {c}") }
    })
    .variant("strong");
    form::stack(ui)
        .child(form::label(ui, "xAI sign-in"))
        .child(line)
        .child(shown)
        .child(form::action(ui, "Sign in with xAI", "primary", move || {
            if s.preview {
                s.banner.set("Not available in this preview.".into());
                return;
            }
            super::http::request(s, "POST", "auth/xai/login".into(), Some(json!({})), move |out| match out {
                Ok(v) => {
                    code.set(state::jstr(&v, "user_code", ""));
                    let link = state::jstr(&v, "verification_uri_complete", "");
                    let link = if link.is_empty() { state::jstr(&v, "verification_uri", "") } else { link };
                    url.set(link.clone());
                    session.set(state::jstr(&v, "session_id", ""));
                    s.xai.set(v);
                    if !link.is_empty() {
                        bits::open_http(&link);
                    }
                    s.banner.set("Enter the code in the browser, then check status.".into());
                }
                Err(e) => s.banner.set(e),
            });
        }))
        .child(form::action(ui, "Check sign-in", "quiet", move || {
            let id = session.get();
            if id.is_empty() {
                bits::ask(s, "GET", "auth/xai", None, "xAI status refreshed.");
                return;
            }
            let path = format!("auth/xai/login/status?session_id={id}");
            if s.preview {
                s.banner.set("Not available in this preview.".into());
                return;
            }
            super::http::request(s, "GET", path, None, move |out| match out {
                Ok(v) => {
                    if let Some(c) = v.get("credentials") {
                        s.xai.set(c.clone());
                    }
                    let status = v.get("session").map(|sess| bits::str_of(sess, "status")).unwrap_or_default();
                    s.banner.set(if status.is_empty() { "Sign-in status updated.".into() } else { format!("Sign-in {status}.") });
                }
                Err(e) => s.banner.set(e),
            });
        }))
        .child(form::action(ui, "Open verification page", "quiet", move || {
            let link = url.get();
            if link.is_empty() {
                s.banner.set("Start sign-in first.".into());
            } else {
                bits::open_http(&link);
            }
        }))
        .child(form::action(ui, "Save API key to xAI", "quiet", move || {
            let key = super::state::take_api_key();
            if key.is_empty() {
                s.banner.set("Enter an API key first. If this failed after a previous try, type it again.".into());
                return;
            }
            bits::ask(s, "POST", "auth/xai/apikey", Some(json!({ "api_key": key })), "xAI API key saved.");
        }))
        .child(form::action(ui, "Sign out of xAI", "danger", move || {
            bits::ask(s, "DELETE", "auth/xai", None, "Signed out of xAI.");
        }))
}

fn catalog(ui: &Ui, s: Settings) -> El {
    let query = Signal::new(String::new());
    form::block(ui, s, "provider-catalog", move |ui, s| {
        let list = bits::rebuild_on(
            ui,
            move || {
                let _ = s.catalog.get().len();
                let _ = query.get();
            },
            move |ui| catalog_rows(ui, s, &query.get()),
        );
        form::stack(ui)
            .child(form::hint(
                ui,
                "Demo stays available. Disable a provider to hide it without deleting its key. An empty model list means every model is allowed.",
            ))
            .child(form::bound_line(ui, "Search providers…", query, "Search providers…"))
            .child(list)
            .child(form::label(ui, "Skills in the hot catalog"))
            .child(w::slider(ui, "Skills active budget", s.skills_budget, 10.0, 500.0, 1.0))
            .child(w::text(ui, move || format!("{:.0} skills", s.skills_budget.get())).variant("small"))
    })
}

fn catalog_rows(ui: &Ui, s: Settings, query: &str) -> El {
    let q = query.trim().to_lowercase();
    let mut col = form::stack(ui);
    for p in s.catalog.get() {
        if !q.is_empty() && !p.id.to_lowercase().contains(&q) && !p.name.to_lowercase().contains(&q) {
            continue;
        }
        let id = p.id.clone();
        let on = Signal::new(provider_enabled(s, &id));
        let id_on = id.clone();
        let demo = id == "demo";
        Effect::new(move || {
            let want = on.get();
            if demo {
                if !want {
                    on.set(true);
                }
                return;
            }
            if s.preview {
                return;
            }
            let cur = provider_enabled(s, &id_on);
            if want == cur {
                return;
            }
            set_provider_enabled(s, &id_on, want);
        });
        let allow = Signal::new(model_allow_text(s, &id));
        let id_allow = id.clone();
        Effect::new(move || {
            let text = allow.get();
            if s.preview {
                return;
            }
            if text == model_allow_text(s, &id_allow) {
                return;
            }
            set_model_allow(s, &id_allow, &text);
        });
        let row = form::stack(ui)
            .child(form::toggle(ui, &p.name, if id == "demo" { "Demo is always available." } else { "Blank model list allows every model." }, on))
            .child(form::bound_line(ui, format!("Models for {id}"), allow, "Blank = all models"));
        col = col.child(row);
    }
    col
}

fn provider_enabled(s: Settings, id: &str) -> bool {
    if id == "demo" {
        return true;
    }
    let list = s.enabled_providers.get();
    list.is_empty() || list.iter().any(|x| x == id)
}

fn set_provider_enabled(s: Settings, id: &str, on: bool) {
    if id == "demo" {
        return;
    }
    let mut list = s.enabled_providers.get();
    if list.is_empty() {
        list = s.catalog.get().into_iter().map(|p| p.id).collect();
    }
    if on {
        if !list.iter().any(|x| x == id) {
            list.push(id.to_string());
        }
    } else {
        list.retain(|x| x != id);
    }
    if !list.iter().any(|x| x == "demo") {
        list.insert(0, "demo".into());
    }
    s.enabled_providers.set(list);
    s.providers_touched.set(true);
}

fn model_allow_text(s: Settings, id: &str) -> String {
    s.enabled_models
        .get()
        .get(id)
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|x| x.as_str()).collect::<Vec<_>>().join(", "))
        .unwrap_or_default()
}

fn set_model_allow(s: Settings, id: &str, text: &str) {
    let mut models = s.enabled_models.get();
    if !models.is_object() {
        models = json!({});
    }
    let ids: Vec<String> = text.split(',').map(|p| p.trim().to_string()).filter(|p| !p.is_empty()).collect();
    if let Some(map) = models.as_object_mut() {
        if ids.is_empty() {
            map.remove(id);
        } else {
            map.insert(id.to_string(), json!(ids));
        }
    }
    s.enabled_models.set(models);
    s.providers_touched.set(true);
}

fn rmb(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "rmb");
    form::block(ui, s, "rmb", move |ui, s| {
        let status = w::text(ui, move || rmb_status(&s.rmb.get())).variant("small");
        let files = bits::rebuild_on(
            ui,
            move || {
                let _ = s.rmb.get().get("discovered_ggufs").map(|v| v.to_string());
            },
            move |ui| gguf_select(ui, s),
        );
        let profile_now = bits::str_of(&s.rmb.get(), "profile");
        let profile = Signal::new(if profile_now.is_empty() { "autofit".into() } else { profile_now });
        let thinking = Signal::new(rmb_thinking(&s.rmb.get()));
        Effect::new(move || {
            thinking.set_if_changed(rmb_thinking(&s.rmb.get()));
        });
        let knobs = knob_list(ui, s);
        let hits = w::text(ui, move || hf_line(&s.hf_hits.get())).variant("small");
        form::stack(ui)
            .child(status)
            .child(form::hint(
                ui,
                "Selecting a GGUF restarts the host and switches chat to that model automatically. Stop only if you want the host off.",
            ))
            .child(bits::choice(ui, "Thinking", &[("on", "On"), ("off", "Off")], thinking))
            .child(form::action(ui, "Apply thinking", "quiet", move || {
                patch_rmb(s, json!({ "thinking": thinking.get() }));
            }))
            .child(files)
            .child(bits::field(ui, "Or paste a GGUF path", s.rmb_path, "C:\\models\\remedy.gguf"))
            .child(form::action(ui, "Use pasted path", "primary", move || {
                let path = s.rmb_path.get();
                patch_rmb(s, json!({ "model_path": path, "use_as_chat_provider": !path.trim().is_empty() }));
            }))
            .child(form::action(ui, "Start host", "quiet", move || bits::ask(s, "POST", "rmb/start", Some(json!({})), "Local host starting.")))
            .child(form::action(ui, "Stop host", "danger", move || bits::ask(s, "POST", "rmb/stop", Some(json!({})), "Local host stopping.")))
            .child(bits::choice(
                ui,
                "Profile",
                &[("autofit", "Autofit"), ("agent", "Agent"), ("turbo", "Turbo"), ("quality", "Quality")],
                profile,
            ))
            .child(form::hint(ui, "Profile, context, and GPU layers apply with the buttons below."))
            .child(form::action(ui, "Apply profile", "quiet", move || patch_rmb(s, json!({ "profile": profile.get() }))))
            .child(knobs)
            .child(bits::field(ui, "Hugging Face search", s.hf_query, "repo or model name"))
            .child(form::action(ui, "Search", "quiet", move || {
                let query = s.hf_query.get();
                if s.preview {
                    s.banner.set("Not available in this preview.".into());
                    return;
                }
                super::http::request(s, "POST", "rmb/hf/search".into(), Some(json!({ "query": query })), move |out| match out {
                    Ok(v) => {
                        s.hf_hits.set(v.get("repos").cloned().unwrap_or(Value::Null));
                        s.report(Ok(v), "Search finished.");
                    }
                    Err(e) => s.banner.set(e),
                });
            }))
            .child(hits)
            .child(form::action(ui, "Pull first result", "primary", move || {
                let repo = s.hf_hits.get().as_array().and_then(|a| a.first()).map(|row| bits::str_of(row, "id")).unwrap_or_default();
                if repo.is_empty() {
                    s.banner.set("Search first.".into());
                    return;
                }
                bits::ask(s, "POST", "rmb/hf/pull", Some(json!({ "repo": repo, "load": true })), "Download started.");
            }))
            .child(form::action(ui, "Download progress", "quiet", move || {
                bits::ask(s, "GET", "rmb/hf/progress", None, "Progress updated.");
            }))
            .child(form::action(ui, "Cancel download", "danger", move || {
                bits::ask(s, "POST", "rmb/hf/cancel", Some(json!({})), "Download cancelled.");
            }))
    })
}

fn rmb_status(v: &Value) -> String {
    if v.is_null() {
        return "Local model status loads when this section is open.".into();
    }
    let running = v.get("running").and_then(|x| x.as_bool()).unwrap_or(false);
    let model = bits::str_of(v, "model");
    let profile = bits::str_of(v, "profile");
    let ctx = v.get("ctx_size").and_then(|n| n.as_i64()).unwrap_or(0);
    format!(
        "{} · {} · {} tok · {}",
        if running { "Host running" } else { "Host stopped" },
        if model.is_empty() { "no model" } else { model.as_str() },
        ctx,
        if profile.is_empty() { "autofit" } else { profile.as_str() }
    )
}

fn rmb_thinking(v: &Value) -> String {
    let raw = v.get("engine").and_then(|e| e.get("thinking")).and_then(|t| t.as_str()).unwrap_or("on");
    if raw == "off" { "off".into() } else { "on".into() }
}

fn gguf_select(ui: &Ui, s: Settings) -> El {
    let rows = s.rmb.get().get("discovered_ggufs").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    if rows.is_empty() {
        return form::hint(ui, "No GGUF files found in Downloads or the Remedy models folder.");
    }
    let mut col = form::stack(ui).child(form::label(ui, "Model file"));
    for row in rows {
        let path = if let Some(text) = row.as_str() {
            text.to_string()
        } else {
            bits::str_of(&row, "path")
        };
        if path.is_empty() {
            continue;
        }
        let label = row.as_str().map(|p| file_name(p).to_string()).unwrap_or_else(|| {
            let name = bits::str_of(&row, "name");
            if name.is_empty() { file_name(&path).to_string() } else { name }
        });
        let path_btn = path.clone();
        col = col.child(form::action(ui, label, "quiet", move || {
            patch_rmb(s, json!({ "model_path": path_btn, "enabled": true, "use_as_chat_provider": true }));
        }));
    }
    col
}

fn file_name(path: &str) -> &str {
    path.rsplit(['/', '\\']).next().unwrap_or(path)
}

fn one_key(key: &str, val: Value) -> Value {
    let mut v = json!({});
    v[key] = val;
    v
}

fn patch_rmb(s: Settings, extra: Value) {
    let mut body = json!({ "enabled": true });
    if let (Some(dst), Some(src)) = (body.as_object_mut(), extra.as_object()) {
        for (k, v) in src {
            dst.insert(k.clone(), v.clone());
        }
    }
    bits::ask(s, "POST", "rmb/settings", Some(body), "Local model settings sent.");
}

fn knob_list(ui: &Ui, s: Settings) -> El {
    let mut col = form::stack(ui).child(form::label(ui, "Engine"));
    for knob in logic::RMB_KNOBS {
        col = col.child(knob_row(ui, s, *knob));
    }
    col
}

fn knob_row(ui: &Ui, s: Settings, knob: Knob) -> El {
    let key = knob.key;
    if knob.kind == "bool" {
        let on = Signal::new(engine_bool(&s.rmb.get(), key));
        return form::stack(ui).child(form::toggle(ui, knob.label, "", on)).child(form::action(ui, "Apply", "quiet", move || {
            patch_rmb(s, one_key(key, json!(on.get())));
        }));
    }
    if knob.kind == "text" {
        let text = Signal::new(engine_text(&s.rmb.get(), key));
        return form::stack(ui)
            .child(bits::field(ui, knob.label, text, ""))
            .child(form::action(ui, "Apply", "quiet", move || {
                patch_rmb(s, one_key(key, json!(text.get())));
            }));
    }
    let value = Signal::new(engine_num(&s.rmb.get(), key, knob.min));
    form::stack(ui)
        .child(form::label(ui, knob.label))
        .child(w::slider(ui, knob.label, value, knob.min, knob.max, knob.step))
        .child(w::text(ui, move || format!("{:.2}", value.get())).variant("small"))
        .child(form::action(ui, "Apply", "quiet", move || {
            let n = logic::clamp_knob(&knob, value.get());
            let stored = if knob.kind == "int" { json!(n as i64) } else { json!(n) };
            patch_rmb(s, one_key(key, stored));
        }))
}

fn engine_bool(v: &Value, key: &str) -> bool {
    v.get("engine").and_then(|e| e.get(key)).and_then(|x| x.as_bool()).unwrap_or(false)
}

fn engine_text(v: &Value, key: &str) -> String {
    v.get("engine").and_then(|e| e.get(key)).map(|x| x.as_str().map(|s| s.to_string()).unwrap_or_else(|| x.to_string())).unwrap_or_default()
}

fn engine_num(v: &Value, key: &str, default: f64) -> f64 {
    v.get("engine").and_then(|e| e.get(key)).and_then(|x| x.as_f64()).unwrap_or(default)
}

fn hf_line(v: &Value) -> String {
    let Some(rows) = v.as_array() else {
        return "No Hugging Face results yet.".into();
    };
    if rows.is_empty() {
        return "No matching repos.".into();
    }
    rows.iter().take(5).map(|row| bits::str_of(row, "id")).filter(|id| !id.is_empty()).collect::<Vec<_>>().join("\n")
}

fn vision(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "vision");
    let mut arm_enabled = false;
    Effect::new(move || {
        let on = s.vision_enabled.get();
        if !arm_enabled {
            arm_enabled = true;
            return;
        }
        if s.preview {
            return;
        }
        s.put_patch(json!({ "vision_enabled": on }));
    });
    let mut arm_force = false;
    Effect::new(move || {
        let on = s.vision_force.get();
        if !arm_force {
            arm_force = true;
            return;
        }
        if s.preview {
            return;
        }
        s.put_patch(json!({ "vision_force_decode": on }));
    });
    form::block(ui, s, "vision", move |ui, s| {
        let status = w::text(ui, move || vision_status(&s.vision.get())).variant("small");
        let force = form::toggle(
            ui,
            "Prefer local decoder even if chat model has vision",
            "Uses SmolVLM2 for images. Off while vision is disabled.",
            s.vision_force,
        );
        let force = force.disabled(move || !s.vision_enabled.get());
        form::stack(ui)
            .child(form::hint(ui, "SmolVLM2 2.2B, Apache 2.0, about 1.6 GB. It is part of Remedy, not an optional add-on."))
            .child(status)
            .child(form::action(ui, "Install", "primary", move || {
                bits::ask(s, "POST", "vision/install", Some(json!({})), "Vision install started.");
            }))
            .child(form::action(ui, "Activate bundled files", "quiet", move || {
                bits::ask(s, "POST", "vision/activate", Some(json!({})), "Vision bundle activated.");
            }))
            .child(form::action(ui, "Start", "quiet", move || bits::ask(s, "POST", "vision/start", Some(json!({})), "Vision starting.")))
            .child(form::action(ui, "Stop", "quiet", move || bits::ask(s, "POST", "vision/stop", Some(json!({})), "Vision stopping.")))
            .child(form::action(ui, "Cancel install", "danger", move || {
                bits::ask(s, "POST", "vision/install/cancel", Some(json!({})), "Install cancelled.");
            }))
            .child(form::action(ui, "Reinstall runtime", "quiet", move || {
                bits::ask(s, "POST", "vision/reinstall-runtime", Some(json!({ "prefer_cuda": true })), "Runtime reinstall started.");
            }))
            .child(form::action(ui, "Uninstall", "danger", move || {
                bits::ask(s, "POST", "vision/uninstall", Some(json!({ "keep_models": false })), "Vision uninstalled.");
            }))
            .child(form::toggle(ui, "Enable for text-only chat models", "Lets a text model ask the local decoder to read images.", s.vision_enabled))
            .child(force)
    })
}

fn vision_status(v: &Value) -> String {
    if v.is_null() {
        return "Vision status loads when this section is open.".into();
    }
    let name = bits::str_of(v, "model_name");
    let device = bits::str_of(v, "device");
    let runtime = bits::str_of(v, "runtime_version");
    let installed = v.get("installed").and_then(|x| x.as_bool()).unwrap_or(false);
    let running = v.get("running").and_then(|x| x.as_bool()).unwrap_or(false);
    format!(
        "{} · {} · {} · {}",
        if name.is_empty() { "SmolVLM2 2.2B" } else { name.as_str() },
        if installed { "installed" } else { "not installed" },
        if running { "running" } else { "stopped" },
        [device.as_str(), runtime.as_str()].into_iter().filter(|p| !p.is_empty()).collect::<Vec<_>>().join(" · ")
    )
}

fn harness(ui: &Ui, s: Settings) -> El {
    Effect::new(move || {
        let min = s.harness_min.get();
        let max = s.harness_max.get_untracked().unwrap_or(0.92);
        if let Some(next) = logic::couple_harness_min(min, max) {
            s.harness_max.set_if_changed(next);
        }
    });
    Effect::new(move || {
        let max = s.harness_max.get();
        let min = s.harness_min.get_untracked().unwrap_or(0.75);
        if let Some(next) = logic::couple_harness_max(min, max) {
            s.harness_min.set_if_changed(next);
        }
    });
    form::block(ui, s, "memory-harness", move |ui, s| {
        form::stack(ui)
            .child(bits::choice(ui, "Mode", &[("auto", "Auto"), ("manual", "Manual"), ("off", "Off")], s.harness_mode))
            .child(form::label(ui, "Keep at least"))
            .child(w::slider(ui, "Harness minimum", s.harness_min, 0.05, 0.90, 0.01))
            .child(w::text(ui, move || format!("{:.0}% of the window", s.harness_min.get() * 100.0)).variant("small"))
            .child(form::label(ui, "Compact before"))
            .child(w::slider(ui, "Harness maximum", s.harness_max, 0.10, 0.99, 0.01))
            .child(w::text(ui, move || format!("{:.0}% of the window", s.harness_max.get() * 100.0)).variant("small"))
            .child(form::hint(ui, "If the floor meets the ceiling, the other side moves so a gap remains."))
    })
}
