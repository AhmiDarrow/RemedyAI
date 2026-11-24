//! Connections: phone, Connect, messengers, assistant, MCP.

use std::cell::RefCell;
use std::rc::Rc;

use remedy_ui::extra::{qr_code, QrOptions};
use remedy_ui::reactive::{Effect, Signal};
use remedy_ui::widgets as w;
use remedy_ui::{show, El, Ui};
use serde_json::{Value, json};

use super::bits;
use super::form;
use super::logic;
use super::state::{self, Settings};
use crate::runtime;

pub fn sections(ui: &Ui, s: Settings) -> El {
    form::stack(ui)
        .child(phone(ui, s))
        .child(connect(ui, s))
        .child(channels(ui, s))
        .child(assistant(ui, s))
        .child(mcp(ui, s))
}

fn phone(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "phone");
    form::block(ui, s, "phone", move |ui, s| {
        let status = w::text(ui, move || {
            let v = s.phone.get();
            if v.is_null() {
                "Phone status loads when this section is open.".into()
            } else {
                bits::str_of(&v, "message")
            }
        })
        .variant("small");
        let lines = bits::rebuild_on(
            ui,
            move || {
                let _ = s.phone.get().get("lines").map(|v| v.to_string());
            },
            move |ui| phone_lines(ui, s),
        );
        form::stack(ui)
            .child(status)
            .child(form::flow(ui, "Telephony terms", "Read the terms, then agree on this computer.", false, move |ui| {
                w::text(ui, logic::phone_terms()).variant("small")
            }))
            .child(form::action(ui, "Agree to the terms", "primary", move || {
                bits::ask(s, "POST", "telephony/terms", Some(json!({ "accept": true })), "Terms recorded on this computer.");
            }))
            .child(lines)
    })
}

fn phone_lines(ui: &Ui, s: Settings) -> El {
    let rows = s.phone.get().get("lines").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    if rows.is_empty() {
        return form::hint(ui, "No phone lines reported yet.");
    }
    let mut col = form::stack(ui);
    for line in rows {
        let name = bits::str_of(&line, "name");
        if name.is_empty() {
            continue;
        }
        let achievable = line.get("achievable").and_then(|v| v.as_bool()).unwrap_or(false);
        let missing = bits::str_of(&line, "missing");
        let label = name.clone();
        let hint = if missing.is_empty() { "Ready to choose.".into() } else { missing };
        col = col.child(form::stack(ui).child(form::hint(ui, format!("{name} — {hint}"))).child(form::action(
            ui,
            format!("Choose {label}"),
            "quiet",
            move || {
                if !achievable {
                    s.banner.set("This line is not achievable yet.".into());
                    return;
                }
                bits::ask(s, "POST", "telephony/choose", Some(json!({ "name": label })), "Phone line chosen.");
            },
        )));
    }
    col
}

fn connect(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "connect");
    form::block(ui, s, "connect", move |ui, s| {
        let ui = ui.clone();
        let enabled = Signal::new(s.connect.get().get("enabled").and_then(|v| v.as_bool()).unwrap_or(false));
        let paused = Signal::new(s.connect.get().get("paused").and_then(|v| v.as_bool()).unwrap_or(false));
        let bind = Signal::new(bits::str_of(&s.connect.get(), "bind_host"));
        sync_connect_flag(s, enabled, "enabled");
        sync_connect_flag(s, paused, "paused");
        Effect::new(move || {
            bind.set_if_changed(bits::str_of(&s.connect.get(), "bind_host"));
        });
        let addresses = Signal::new(Vec::<String>::new());
        Effect::new(move || {
            let raw = address_list(&s.connect.get());
            addresses.set_if_changed(logic::filter_connect_addresses(&raw));
        });
        let host = form::stack(&ui);
        let ui_pick = ui.clone();
        let _ = w::for_each(&host, addresses, |ip| ip.clone(), move |ip| {
            let label = ip.clone();
            let pick = ip.clone();
            form::action(&ui_pick, label, "quiet", move || {
                bind.set(pick.clone());
                put_connect_bind(s, &pick);
            })
        });
        let loop_warn = form::notice(&ui, "warn", logic::CONNECT_LOOPBACK_WARNING);
        let loop_warn = bits::visible_when(loop_warn, move || logic::is_connect_loopback(&bind.get()));
        let panes = pane_list(&ui, s);
        let qr = show(
            &ui,
            {
                let s = s.clone();
                move || s.pair_qr.get().trim().is_empty()
            },
            {
                let ui = ui.clone();
                move || w::text(&ui, "Pairing code appears here.").variant("muted")
            },
        )
        .fallback({
            let ui = ui.clone();
            let s = s.clone();
            move || {
                qr_code(
                    &ui,
                    "Pairing code",
                    s.pair_qr,
                    QrOptions::default().description("Connect pairing QR code".into()),
                )
            }
        })
        .el();
        form::stack(&ui)
            .child(form::toggle(&ui, "Connect is on", "Phones on this network can reach this PC.", enabled))
            .child(form::action(&ui, "Apply on/off", "primary", move || put_connect_flag(s, "enabled", enabled.get())))
            .child(form::toggle(&ui, "Paused", "Keep the pairing and stop accepting phones for now.", paused))
            .child(form::action(&ui, if false { "" } else { "Pause or resume" }, "quiet", move || {
                if paused.get() {
                    bits::ask(s, "POST", "connect/pause", Some(json!({})), "Connect paused.");
                } else {
                    bits::ask(s, "POST", "connect/resume", Some(json!({})), "Connect resumed.");
                }
            }))
            .child(form::label(&ui, "Bind address"))
            .child(bits::field(&ui, "Bind host", bind, "192.168.1.20"))
            .child(host)
            .child(loop_warn)
            .child(form::action(&ui, "Use this bind address", "quiet", move || put_connect_bind(s, &bind.get())))
            .child(panes)
            .child(form::action(&ui, "Show pairing code", "primary", move || {
                if s.preview {
                    s.banner.set("Not available in this preview.".into());
                    return;
                }
                super::http::request(s, "POST", "connect/pair/start".into(), Some(json!({})), move |out| match out {
                    Ok(v) => {
                        s.pair_qr.set(state::jstr(&v, "qr", ""));
                        s.banner.set("Pairing code ready.".into());
                    }
                    Err(e) => s.banner.set(e),
                });
            }))
            .child(qr)
            .child(device_list(&ui, s))
            .child(form::action(&ui, "Tailscale status", "quiet", move || bits::ask(s, "GET", "connect/tailscale/status", None, "Tailscale status updated.")))
            .child(form::action(&ui, "Install Tailscale", "quiet", move || bits::ask(s, "POST", "connect/tailscale/install", Some(json!({})), "Tailscale install started.")))
            .child(form::action(&ui, "Tailscale login", "quiet", move || bits::ask(s, "POST", "connect/tailscale/login", Some(json!({})), "Tailscale login started.")))
            .child(mobile_reach(&ui, s))
    })
}

fn address_list(v: &Value) -> Vec<String> {
    v.get("addresses")
        .and_then(|a| a.as_array())
        .map(|rows| rows.iter().filter_map(|x| x.as_str().map(|s| s.to_string())).collect())
        .unwrap_or_default()
}

fn sync_connect_flag(s: Settings, local: Signal<bool>, key: &'static str) {
    Effect::new(move || {
        let on = s.connect.get().get(key).and_then(|v| v.as_bool()).unwrap_or(false);
        local.set_if_changed(on);
    });
}

fn put_connect_flag(s: Settings, key: &str, on: bool) {
    let mut body = connect_body(s);
    body[key] = json!(on);
    bits::ask(s, "PUT", "connect", Some(body), "Connect updated.");
}

fn put_connect_bind(s: Settings, host: &str) {
    let mut body = connect_body(s);
    body["bind_host"] = json!(host);
    bits::ask(s, "PUT", "connect", Some(body), "Bind address updated.");
}

fn connect_body(s: Settings) -> Value {
    let cur = s.connect.get();
    let mut panes = logic::merge_connect_panes(cur.get("panes").unwrap_or(&Value::Null));
    panes.insert("approvals".into(), Value::Bool(true));
    json!({
        "enabled": cur.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false),
        "paused": cur.get("paused").and_then(|v| v.as_bool()).unwrap_or(false),
        "bind_host": bits::str_of(&cur, "bind_host"),
        "bind_port": cur.get("bind_port").and_then(|v| v.as_i64()).unwrap_or(7400),
        "panes": panes,
        "relay_url": bits::str_of(&cur, "relay_url"),
    })
}

fn pane_list(ui: &Ui, s: Settings) -> El {
    let mut col = form::stack(ui).child(form::label(ui, "What the phone can do"));
    let panes = logic::merge_connect_panes(s.connect.get().get("panes").unwrap_or(&Value::Null));
    for key in logic::PANE_KEYS {
        let on = Signal::new(panes.get(*key).and_then(|v| v.as_bool()).unwrap_or(false));
        let locked = *key == "approvals";
        if locked {
            Effect::new(move || {
                if !on.get() {
                    on.set(true);
                }
            });
        }
        let toggle = form::toggle(ui, logic::pane_label(key), if locked { "Approvals stay on." } else { "" }, on);
        let toggle = if locked { toggle.disabled(true) } else { toggle };
        let key_owned = (*key).to_string();
        col = col.child(toggle).child(form::action(ui, format!("Apply {}", logic::pane_label(key)), "quiet", move || {
            let mut body = connect_body(s);
            if let Some(map) = body.get_mut("panes").and_then(|v| v.as_object_mut()) {
                map.insert(key_owned.clone(), json!(on.get() || key_owned == "approvals"));
            }
            bits::ask(s, "PUT", "connect", Some(body), "Phone panes updated.");
        }));
    }
    col
}

fn device_list(ui: &Ui, s: Settings) -> El {
    let rows = s.connect.get().get("devices").and_then(|v| v.as_array()).cloned().unwrap_or_default();
    if rows.is_empty() {
        return form::hint(ui, "No paired phones yet.");
    }
    let mut col = form::stack(ui).child(form::label(ui, "Paired phones"));
    for device in rows {
        let id = bits::str_of(&device, "id");
        if id.is_empty() {
            continue;
        }
        let name = {
            let n = bits::str_of(&device, "name");
            if n.is_empty() { id.clone() } else { n }
        };
        let revoke_id = id.clone();
        col = col.child(form::action(ui, format!("Revoke {name}"), "danger", move || {
            let path = format!("connect/devices/{revoke_id}/revoke");
            bits::ask(s, "POST", &path, Some(json!({})), "Phone revoked.");
        }));
    }
    col
}

fn mobile_reach(ui: &Ui, s: Settings) -> El {
    let mobile = s.connect.get().get("mobile").cloned().unwrap_or(Value::Null);
    let enabled = Signal::new(mobile.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false));
    let punch = Signal::new(mobile.get("punch").and_then(|v| v.as_bool()).unwrap_or(false));
    let port = Signal::new(mobile.get("port").map(|n| n.to_string()).unwrap_or_else(|| "4433".into()));
    let relay = Signal::new({
        let mode = bits::str_of(&mobile, "relay_mode");
        if mode.is_empty() { "off".into() } else { mode }
    });
    let relay_url = Signal::new(bits::str_of(&mobile, "relay_url"));
    let message = mobile.get("status").map(|st| bits::str_of(st, "message")).unwrap_or_default();
    form::stack(ui)
        .child(form::label(ui, "Reach this PC from mobile data"))
        .child(form::toggle(
            ui,
            "Let my paired phone reach this PC from anywhere",
            "No account and no VPN app. This PC opens one UDP port. Chats stay end-to-end encrypted.",
            enabled,
        ))
        .child(form::action(ui, "Apply mobile reach", "primary", move || {
            bits::ask(s, "PUT", "connect", Some(json!({ "mobile_reach": enabled.get() })), "Mobile reach updated.");
        }))
        .child(form::hint(ui, if message.is_empty() { "Off. A phone on this Wi-Fi can still connect.".into() } else { message }))
        .child(form::toggle(ui, "Get through carrier-grade NAT", "Try a direct connection when the provider shares one address. Re-pair after changing this.", punch))
        .child(form::action(ui, "Apply NAT traversal", "quiet", move || {
            bits::ask(s, "PUT", "connect", Some(json!({ "mobile_punch": punch.get() })), "NAT traversal updated.");
        }))
        .child(bits::field(ui, "UDP port", port, "4433"))
        .child(form::action(ui, "Apply port", "quiet", move || {
            let n = port.get().trim().parse::<u16>().unwrap_or(4433);
            bits::ask(s, "PUT", "connect", Some(json!({ "mobile_port": n })), "UDP port updated.");
        }))
        .child(bits::choice(ui, "Relay", &[("off", "Off"), ("custom", "Custom"), ("n0", "n0")], relay))
        .child(bits::field(ui, "Relay URL", relay_url, "https://"))
        .child(form::action(ui, "Apply relay", "quiet", move || {
            bits::ask(
                s,
                "PUT",
                "connect",
                Some(json!({ "mobile_relay_mode": relay.get(), "mobile_relay_url": relay_url.get() })),
                "Relay updated.",
            );
        }))
        .child(form::action(ui, "Test again", "quiet", move || {
            bits::ask(s, "POST", "connect/mobile/test", Some(json!({})), "Mobile reach test finished.");
        }))
}

fn channels(ui: &Ui, s: Settings) -> El {
    bits::when_open(s, "channels");
    form::block(ui, s, "channels", move |ui, s| {
        let ui = ui.clone();
        let ids = Signal::new(Vec::<String>::new());
        Effect::new(move || {
            ids.set_if_changed(messenger_ids(&s.messengers.get()));
        });
        let list = form::stack(&ui);
        let _ = w::for_each(&list, ids, |id| id.clone(), {
            let ui = ui.clone();
            move |id| messenger_card(&ui, s, id)
        });
        let tunnel = w::text(&ui, move || tunnel_line(&s.tunnel.get())).variant("small");
        let denied = w::text(&ui, move || denied_line(&s.denied.get())).variant("small");
        form::stack(&ui)
            .child(form::hint(&ui, "A webhook tunnel gives messengers an https address. Quick mode needs no account."))
            .child(tunnel)
            .child(form::action(&ui, "Start quick tunnel", "primary", move || {
                bits::ask(s, "POST", "messengers/tunnel/start", Some(json!({ "mode": "quick" })), "Tunnel starting.");
            }))
            .child(form::action(&ui, "Start named tunnel", "quiet", move || {
                bits::ask(s, "POST", "messengers/tunnel/start", Some(json!({ "mode": "named" })), "Named tunnel starting.");
            }))
            .child(form::action(&ui, "Stop tunnel", "danger", move || {
                bits::ask(s, "POST", "messengers/tunnel/stop", Some(json!({})), "Tunnel stopping.");
            }))
            .child(form::action(&ui, "Prepare Signal CLI", "quiet", move || {
                bits::ask(s, "POST", "messengers/signal/ensure", Some(json!({})), "Signal CLI check finished.");
            }))
            .child(form::hint(&ui, "Signal’s CLI needs Java on this computer. Temurin 21 is the build Remedy expects."))
            .child(form::action(&ui, "Java download", "quiet", || {
                bits::open_http("https://adoptium.net/temurin/releases/?version=21");
            }))
            .child(denied)
            .child(list)
    })
}

fn messenger_ids(v: &Value) -> Vec<String> {
    v.as_array().map(|rows| rows.iter().filter_map(|m| m.get("id").and_then(|id| id.as_str()).map(|s| s.to_string())).collect()).unwrap_or_default()
}

fn messenger_card(ui: &Ui, s: Settings, id: &str) -> El {
    let Some(row) = s.messengers.get().as_array().and_then(|rows| {
        rows.iter().find(|m| m.get("id").and_then(|v| v.as_str()) == Some(id)).cloned()
    }) else {
        return ui.el("messenger-missing");
    };
    let id = id.to_string();
    let name = {
        let n = bits::str_of(&row, "name");
        if n.is_empty() { logic::messenger_badge(&id) } else { n }
    };
    let enabled = Signal::new(draft_bool(s, &id, "enabled"));
    let id_en = id.clone();
    let mut armed = false;
    Effect::new(move || {
        let on = enabled.get();
        if !armed {
            armed = true;
            return;
        }
        write_draft(s, &id_en, "enabled", json!(on));
    });
    let open = Signal::new(false);
    let mut fields = form::stack(ui);
    if let Some(schema) = row.get("field_schema").and_then(|v| v.as_array()) {
        for spec in schema {
            let key = bits::str_of(spec, "key");
            if key.is_empty() {
                continue;
            }
            let label = {
                let l = bits::str_of(spec, "label");
                if l.is_empty() { key.clone() } else { l }
            };
            let kind = bits::str_of(spec, "kind");
            if kind == "bool" {
                let on = Signal::new(draft_bool(s, &id, &key));
                let id_b = id.clone();
                let key_b = key.clone();
                let mut armed_b = false;
                Effect::new(move || {
                    let v = on.get();
                    if !armed_b {
                        armed_b = true;
                        return;
                    }
                    write_draft(s, &id_b, &key_b, json!(v));
                });
                fields = fields.child(form::toggle(ui, &label, "", on));
            } else {
                let text = Signal::new(draft_string(s, &id, &key));
                let id_t = id.clone();
                let key_t = key.clone();
                let mut armed_t = false;
                Effect::new(move || {
                    let v = text.get();
                    if !armed_t {
                        armed_t = true;
                        return;
                    }
                    if v == draft_string(s, &id_t, &key_t) {
                        return;
                    }
                    write_draft(s, &id_t, &key_t, json!(v));
                });
                let placeholder = if kind == "secret" { "Leave blank to keep the saved secret" } else { "" };
                fields = fields.child(bits::field(ui, &label, text, placeholder));
            }
        }
    }
    let fields = bits::visible_when(fields, move || open.get());
    let id_mark = id.clone();
    form::stack(ui)
        .child(w::row(ui).align(remedy_ui::layout::Align::Center).gap(form::gap(ui)).fill_width().child(w::badge(ui, logic::messenger_badge(&id))).child(w::text(ui, name).grow(1.0)).child(form::action(ui, "Details", "quiet", move || open.update(|v| *v = !*v))))
        .child(form::hint(ui, bits::str_of(&row, "description")))
        .child(form::toggle(ui, "Enabled", "Save sends this connector with the main Save button.", enabled))
        .child(w::text(ui, move || format!("Status {}", bits::str_of(&messenger_row(s, &id_mark), "status"))).variant("small"))
        .child(fields)
}

fn messenger_row(s: Settings, id: &str) -> Value {
    s.messengers.get().as_array().and_then(|rows| rows.iter().find(|m| m.get("id").and_then(|v| v.as_str()) == Some(id)).cloned()).unwrap_or(Value::Null)
}

fn draft_string(s: Settings, id: &str, key: &str) -> String {
    s.messenger_drafts.get().get(id).and_then(|d| d.get(key)).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

fn draft_bool(s: Settings, id: &str, key: &str) -> bool {
    s.messenger_drafts.get().get(id).and_then(|d| d.get(key)).and_then(|v| v.as_bool()).unwrap_or(false)
}

fn write_draft(s: Settings, id: &str, key: &str, val: Value) {
    let mut drafts = s.messenger_drafts.get();
    if !drafts.is_object() {
        drafts = json!({});
    }
    let entry = drafts.as_object_mut().and_then(|map| {
        let slot = map.entry(id.to_string()).or_insert_with(|| json!({}));
        slot.as_object_mut().map(|obj| {
            obj.insert(key.to_string(), val);
        })
    });
    let _ = entry;
    s.messenger_drafts.set(drafts);
    s.messengers_dirty.set(true);
}

fn tunnel_line(v: &Value) -> String {
    if v.is_null() {
        return "Tunnel status loads with Connect.".into();
    }
    let running = v.get("running").and_then(|x| x.as_bool()).unwrap_or(false);
    let url = bits::str_of(v, "public_url");
    if running && !url.is_empty() {
        format!("Tunnel running · {url}")
    } else if running {
        "Tunnel running.".into()
    } else {
        "Tunnel is stopped.".into()
    }
}

fn denied_line(v: &Value) -> String {
    let Some(rows) = v.as_array() else {
        return "No blocked inbound messages.".into();
    };
    if rows.is_empty() {
        return "No blocked inbound messages.".into();
    }
    format!("{} blocked inbound message(s).", rows.len())
}

fn assistant(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "assistant", move |ui, s| {
        let enabled = Signal::new(s.assistant.get().get("enabled").and_then(|v| v.as_bool()).unwrap_or(false));
        let brief = Signal::new(s.assistant.get().get("brief").and_then(|b| b.get("enabled")).and_then(|v| v.as_bool()).unwrap_or(false));
        let hour = Signal::new(
            s.assistant
                .get()
                .get("brief")
                .and_then(|b| b.get("hour_local"))
                .and_then(|n| n.as_i64())
                .map(|n| n.to_string())
                .unwrap_or_else(|| "7".into()),
        );
        draft_on_change(s, enabled, "enabled");
        let planned = w::text(ui, move || planned_line(&s.assistant.get())).variant("small");
        form::stack(ui)
            .child(form::hint(ui, "Calendar, mail, and budget stay on this computer. Connect asks before any account is linked."))
            .child(form::toggle(ui, "Personal assistant", "Include the assistant in the next Save.", enabled))
            .child(form::toggle(ui, "Morning brief", "A short brief at the hour below. Saved with Settings.", brief))
            .child(bits::field(ui, "Brief hour (0–23)", hour, "7"))
            .child(form::action(ui, "Keep brief in the draft", "quiet", move || {
                let n = hour.get().trim().parse::<i64>().unwrap_or(7).clamp(0, 23);
                let mut draft = s.assistant_draft.get();
                let mut brief_obj = draft.get("brief").cloned().unwrap_or_else(|| json!({}));
                bits::set_obj(&mut brief_obj, "enabled", json!(brief.get()));
                bits::set_obj(&mut brief_obj, "hour_local", json!(n));
                bits::set_obj(&mut draft, "brief", brief_obj);
                s.assistant_draft.set(draft);
                s.banner.set("Brief is included in the next Save.".into());
            }))
            .child(planned)
            .child(form::action(ui, "Connect Google", "primary", {
                let ui = ui.clone();
                move || connect_google(&ui, s)
            }))
            .child(form::action(ui, "Disconnect Google", "danger", move || {
                bits::ask(s, "DELETE", "assistant/google", None, "Google disconnected.");
            }))
    })
}

fn draft_on_change(s: Settings, local: Signal<bool>, key: &'static str) {
    let mut armed = false;
    Effect::new(move || {
        let v = local.get();
        if !armed {
            armed = true;
            return;
        }
        let mut draft = s.assistant_draft.get();
        bits::set_obj(&mut draft, key, json!(v));
        s.assistant_draft.set(draft);
    });
}

fn planned_line(v: &Value) -> String {
    let Some(rows) = v.get("providers_planned").and_then(|p| p.as_array()) else {
        return "Google is the account Remedy can connect. Outlook and Yahoo are planned.".into();
    };
    if rows.is_empty() {
        return "Google is the account Remedy can connect.".into();
    }
    rows.iter()
        .map(|row| format!("{} ({})", bits::str_of(row, "name"), bits::str_of(row, "status")))
        .collect::<Vec<_>>()
        .join(" · ")
}

fn sign_in_ready(v: &Value) -> bool {
    if v.get("sign_in_ready").and_then(|x| x.as_bool()).unwrap_or(false) {
        return true;
    }
    let google = v.get("google").cloned().unwrap_or(Value::Null);
    google.get("sign_in_ready").and_then(|x| x.as_bool()).unwrap_or(false)
        || google.get("client_id_set").and_then(|x| x.as_bool()).unwrap_or(false)
        || google.get("app").and_then(|a| a.get("client_id_set")).and_then(|x| x.as_bool()).unwrap_or(false)
}

fn connect_google(ui: &Ui, s: Settings) {
    let privacy = Signal::new(false);
    let account = Signal::new(false);
    let client_id = Signal::new(String::new());
    let client_secret = Signal::new(String::new());
    let err = Signal::new(String::new());
    let ready = sign_in_ready(&s.assistant.get());
    let notices = s.assistant.get().get("privacy").cloned().unwrap_or(Value::Null);
    let short = {
        let t = bits::str_of(&notices, "privacy_ai_short");
        if t.is_empty() { "Calendar and mail text can be sent to your model when you ask.".into() } else { t }
    };
    let privacy_label = {
        let t = bits::str_of(&notices, "privacy_ai_checkbox");
        if t.is_empty() { "I understand Remedy may send account text to my model.".into() } else { t }
    };
    let account_label = {
        let t = bits::str_of(&notices, "account_connect_checkbox");
        if t.is_empty() { "I want Remedy to connect this account on this computer.".into() } else { t }
    };
    let holder: Rc<RefCell<Option<w::Modal>>> = Rc::new(RefCell::new(None));
    let dismiss = holder.clone();
    // The HTTP callback has to be Send. Rc is not, so the callback flips this
    // and the effect, which stays on the UI thread, closes the dialog.
    let close_when = Signal::new(false);
    let watched = holder.clone();
    Effect::new(move || {
        if close_when.get() {
            if let Some(modal) = watched.borrow().as_ref() {
                modal.close();
            }
        }
    });
    let err_line = w::text(ui, move || err.get()).variant("error");
    let mut body = form::stack(ui)
        .child(form::hint(ui, short))
        .child(w::check(ui, privacy_label, privacy))
        .child(w::check(ui, account_label, account));
    if !ready {
        body = body
            .child(bits::field(ui, "OAuth client ID", client_id, "Client ID"))
            .child(bits::field(ui, "OAuth client secret", client_secret, "Optional"));
    }
    body = body
        .child(err_line)
        .child(form::action(ui, "Continue", "primary", move || {
            if !privacy.get() || !account.get() {
                err.set("Accept both notices to continue.".into());
                return;
            }
            if s.preview {
                err.set("Not available in this preview.".into());
                return;
            }
            let id = client_id.get();
            let secret = client_secret.get();
            let ready = ready;
            super::http::put_settings(
                s,
                json!({ "assistant": { "privacy_ai_accepted": true, "account_access_accepted": true } }),
                move |out| {
                    if let Err(e) = out {
                        err.set(e);
                        return;
                    }
                    if !ready {
                        if id.trim().is_empty() {
                            err.set("Client ID required for this install.".into());
                            return;
                        }
                        let origin = runtime::origin();
                        let mut app = json!({
                            "client_id": id.trim(),
                            "redirect_uri": format!("{}/api/assistant/google/callback", origin.trim_end_matches('/')),
                        });
                        if !secret.trim().is_empty() {
                            bits::set_obj(&mut app, "client_secret", json!(secret.trim()));
                        }
                        super::http::request(s, "PUT", "assistant/google/app".into(), Some(app), move |out| {
                            if let Err(e) = out {
                                err.set(e);
                                return;
                            }
                            start_google(s, err, close_when);
                        });
                        return;
                    }
                    start_google(s, err, close_when);
                },
            );
        }))
        .child(form::action(ui, "Close", "quiet", move || {
            if let Some(modal) = dismiss.borrow().as_ref() {
                modal.close();
            }
        }));
    let modal = w::modal(ui, "Connect Google", body, true);
    *holder.borrow_mut() = Some(modal);
}

fn start_google(s: Settings, err: Signal<String>, close_when: Signal<bool>) {
    let origin = runtime::origin();
    let redirect = format!("{}/api/assistant/google/callback", origin.trim_end_matches('/'));
    super::http::request(s, "POST", "assistant/google/oauth/start".into(), Some(json!({ "redirect_uri": redirect })), move |out| {
        match out {
            Ok(v) => {
                let url = state::jstr(&v, "auth_url", "");
                if !url.is_empty() {
                    bits::open_in_browser_rail(&url);
                }
                s.banner.set("Continue in the browser, then return here.".into());
                close_when.set(true);
            }
            Err(e) => err.set(e),
        }
    });
}

fn mcp(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "mcp", move |ui, _s| {
        form::stack(ui)
            .child(form::hint(
                ui,
                "Run Remedy as an MCP server so other tools on this machine can use skills and plans. Same owner, local only.",
            ))
            .child(w::text(ui, "remedy mcp serve\n# or: remedy-mcp").variant("code"))
            .child(form::label(ui, "MCP client JSON"))
            .child(w::text(
                ui,
                "{\n  \"mcpServers\": {\n    \"remedy\": {\n      \"command\": \"remedy-mcp\",\n      \"args\": []\n    }\n  }\n}",
            ).variant("code"))
            .child(form::hint(ui, "The command and JSON are shown here. This shell has no separate clipboard control."))
            .child(form::hint(ui, "Tools: skill list, search, and get; plan list and show. Script run stays opt-in."))
            .child(form::hint(ui, "Quarantined skills stay blocked until you trust them in Skills."))
            .child(form::hint(ui, "Script execution: set REMEDY_MCP_ALLOW_RUN=1 (owner choice). Logs go to stderr."))
            .child(w::link(ui, "Open CLI & API help", || {
                bits::open_http("https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/10-cli-and-api.md");
            }))
    })
}
