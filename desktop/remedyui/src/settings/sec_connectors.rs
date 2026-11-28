//! Connectors: the MCP servers the owner lets Remedy use (GitHub and the like).
//!
//! The runtime owns them (`/api/mcp/servers`). This section lists them with
//! their state and tools, adds one from a command or a URL, and removes or
//! reconnects one. A token is typed here and sent once; it is never shown back.

use remedy_ui::reactive::Signal;
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui};
use serde_json::{Value, json};

use super::bits;
use super::form;
use super::http;
use super::state::Settings;

/// What a typed target is: a URL to connect to, or a command and its arguments.
pub(crate) fn split_target(target: &str) -> Result<Value, String> {
    let target = target.trim();
    if target.is_empty() {
        return Err("Give a URL to connect to, or the command that starts the connector.".into());
    }
    if target.starts_with("http://") || target.starts_with("https://") {
        return Ok(json!({ "url": target }));
    }
    let mut parts = target.split_whitespace();
    let command = parts.next().unwrap_or_default();
    let args: Vec<&str> = parts.collect();
    Ok(json!({ "command": command, "args": args }))
}

/// One line saying how a connector is doing.
pub(crate) fn state_line(server: &Value) -> String {
    let tools = server.get("tools").and_then(Value::as_array).map(Vec::len).unwrap_or(0);
    let state = bits::str_of(server, "state");
    let mut line = match state.as_str() {
        "connected" if tools == 1 => "Connected · 1 tool".to_string(),
        "connected" => format!("Connected · {tools} tools"),
        "connecting" => "Connecting…".to_string(),
        "off" => "Off".to_string(),
        _ => {
            let why = bits::str_of(server, "error");
            if why.is_empty() { "Not connected".to_string() } else { format!("Not connected: {why}") }
        }
    };
    let left_out = server.get("tools_left_out").and_then(Value::as_u64).unwrap_or(0);
    if left_out > 0 {
        line.push_str(&format!(" ({left_out} more not added)"));
    }
    if bits::str_of(server, "approval") == "auto" {
        line.push_str(" · runs in Auto without asking");
    }
    line
}

pub fn connectors(ui: &Ui, s: Settings) -> El {
    form::block(ui, s, "connectors", move |ui, s| {
        let servers: Signal<Value> = Signal::new(json!([]));
        let took = move |out: Result<Value, String>, ok: &str| match out {
            Ok(v) => {
                servers.set(v.get("servers").cloned().unwrap_or_else(|| json!([])));
                if !ok.is_empty() {
                    s.banner.set(ok.to_string());
                }
            }
            Err(e) => s.banner.set(if e.is_empty() { "Request failed.".into() } else { e }),
        };
        let reload = move || http::request(s, "GET", "mcp/servers".into(), None, move |out| took(out, ""));
        if !s.preview {
            reload();
        }

        let name = Signal::new(String::new());
        let target = Signal::new(String::new());
        let token_env = Signal::new(String::new());
        let approval = Signal::new("ask".to_string());
        let token = w::secret_input(ui, "Token").placeholder("Only if the service needs one");
        let token_add = token.clone();
        // The field is not something a reply from another thread can touch:
        // the reply bumps this, and the field empties itself when it changes.
        let sent = Signal::new(0u64);
        let _ = token.el().bind({
            let field = token.clone();
            move || {
                if sent.get() > 0 {
                    field.set_value("");
                }
            }
        });

        let list = bits::rebuild_on(
            ui,
            move || {
                let _ = servers.get();
            },
            move |ui| {
                let rows = servers.get();
                let rows = rows.as_array().cloned().unwrap_or_default();
                let mut col = form::stack(ui);
                if rows.is_empty() {
                    return col.child(form::hint(ui, "No connectors yet."));
                }
                for server in rows {
                    let id = bits::str_of(&server, "name");
                    let (id_again, id_gone) = (id.clone(), id.clone());
                    let tool_names: Vec<String> = server
                        .get("tools")
                        .and_then(Value::as_array)
                        .map(|t| t.iter().take(8).map(|x| bits::str_of(x, "name")).collect())
                        .unwrap_or_default();
                    let mut card = form::stack(ui)
                        .child(form::label(ui, &id))
                        .child(form::hint(ui, bits::str_of(&server, "target")))
                        .child(w::text(ui, state_line(&server)));
                    if !tool_names.is_empty() {
                        card = card.child(form::hint(ui, format!("Tools: {}", tool_names.join(", "))));
                    }
                    col = col.child(
                        card.child(
                            w::row(ui)
                                .gap(8.0)
                                .child(
                                    w::button(ui, "Reconnect", move || {
                                        http::request(s, "POST", format!("mcp/servers/{id_again}/reconnect"), None, move |out| {
                                            took(out, "Reconnecting.")
                                        });
                                    })
                                    .variant("quiet"),
                                )
                                .child(
                                    w::button(ui, "Remove", move || {
                                        http::request(s, "DELETE", format!("mcp/servers/{id_gone}"), None, move |out| {
                                            took(out, "Connector removed.")
                                        });
                                    })
                                    .variant("quiet"),
                                ),
                        ),
                    );
                }
                col
            },
        );

        let add = w::button(ui, "Add connector", move || {
            if s.preview {
                s.banner.set("Not available in this preview.".into());
                return;
            }
            let mut body = match split_target(&target.get()) {
                Ok(v) => v,
                Err(e) => {
                    s.banner.set(e);
                    return;
                }
            };
            body["name"] = json!(name.get().trim().to_lowercase());
            body["approval"] = json!(approval.get());
            let env = token_env.get();
            if !env.trim().is_empty() {
                body["token_env"] = json!(env.trim());
            }
            let secret = token_add.value();
            if !secret.trim().is_empty() {
                body["token"] = json!(secret.trim());
            }
            http::request(s, "POST", "mcp/servers".into(), Some(body), move |out| {
                if out.is_ok() {
                    // The token is in the runtime's secret store now; do not keep it here.
                    sent.update(|n| *n += 1);
                    name.set(String::new());
                    target.set(String::new());
                }
                took(out, "Connector added. It connects in the background.");
            });
        });

        form::stack(ui)
            .child(form::hint(
                ui,
                "A connector gives Remedy the tools of an outside service through MCP. Add only ones you trust: a connector acts as you on that service, and what it returns is shown to your model.",
            ))
            .child(list)
            .child(w::button(ui, "Refresh", reload).variant("quiet"))
            .child(form::label(ui, "Add a connector"))
            .child(bits::field(ui, "Name", name, "github"))
            .child(bits::field(ui, "URL, or command with its arguments", target, "https://… or npx -y some-mcp-server"))
            .child(form::label(ui, "Token"))
            .child(
                w::row(ui)
                    .align(remedy_ui::layout::Align::Center)
                    .gap(8.0)
                    .fill_width()
                    .child(token.el().grow(1.0))
                    .child(w::reveal_toggle(ui, &token)),
            )
            .child(form::hint(
                ui,
                "A URL connector gets the token as a bearer header. A command connector reads it from the variable named below. It is kept in Remedy's secret store, not in the settings file.",
            ))
            .child(bits::field(ui, "Token variable (command connectors)", token_env, "GITHUB_PERSONAL_ACCESS_TOKEN"))
            .child(bits::choice(
                ui,
                "In Auto mode",
                &[("ask", "Ask me before each use"), ("auto", "Run without asking")],
                approval,
            ))
            .child(form::hint(
                ui,
                "In Ask mode every use waits for you. In Full mode none does. Messages from outside (messengers, other bots) can never use a connector.",
            ))
            .child(add)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_target_is_a_url_or_a_command_line() {
        assert_eq!(split_target(" https://example.test/mcp ").unwrap(), json!({ "url": "https://example.test/mcp" }));
        assert_eq!(
            split_target("npx -y  some-server").unwrap(),
            json!({ "command": "npx", "args": ["-y", "some-server"] })
        );
        assert!(split_target("   ").is_err());
    }

    #[test]
    fn the_state_line_says_what_the_owner_needs() {
        assert_eq!(
            state_line(&json!({"state": "connected", "tools": [{}, {}], "approval": "auto"})),
            "Connected · 2 tools · runs in Auto without asking"
        );
        assert_eq!(state_line(&json!({"state": "connected", "tools": [{}], "tools_left_out": 3})), "Connected · 1 tool (3 more not added)");
        assert_eq!(state_line(&json!({"state": "error", "error": "\"npx\" was not found on this computer"})), "Not connected: \"npx\" was not found on this computer");
        assert_eq!(state_line(&json!({"state": "connecting"})), "Connecting…");
    }
}
