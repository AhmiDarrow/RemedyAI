//! Composer → mock HTTP+SSE runtime on loopback, with a streaming reply in the feed.

use remedy_desktop::{Client, Studio, shell};
use remedy_ui::a11y::Role;
use remedy_ui::testing::TestApp;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener};
use std::thread;
use std::time::{Duration, Instant};

fn start_mock() -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind mock runtime");
    listener.set_nonblocking(false).ok();
    let addr = listener.local_addr().expect("addr");
    thread::Builder::new()
        .name("mock-runtime".into())
        .spawn(move || {
            for stream in listener.incoming() {
                let Ok(mut s) = stream else { continue };
                s.set_nodelay(true).ok();
                // Read the whole request. Closing with the POST body unread
                // resets the connection on Windows and the client sees a drop.
                let mut buf = Vec::new();
                let mut chunk = [0u8; 8192];
                loop {
                    let n = match s.read(&mut chunk) {
                        Ok(0) | Err(_) => break,
                        Ok(n) => n,
                    };
                    buf.extend_from_slice(&chunk[..n]);
                    let text = String::from_utf8_lossy(&buf);
                    let Some(head_end) = text.find("\r\n\r\n") else { continue };
                    let want = text[..head_end]
                        .lines()
                        .find_map(|l| {
                            let (k, v) = l.split_once(':')?;
                            k.eq_ignore_ascii_case("content-length")
                                .then(|| v.trim().parse::<usize>().ok())?
                        })
                        .unwrap_or(0);
                    if buf.len() >= head_end + 4 + want {
                        break;
                    }
                }
                let req = String::from_utf8_lossy(&buf);
                let line = req.lines().next().unwrap_or("");
                let path = line.split_whitespace().nth(1).unwrap_or("/");
                if path.contains("/messages/stream") {
                    let body = concat!(
                        "event: token\n",
                        "data: {\"type\":\"token\",\"text\":\"Hello from the mind.\"}\n",
                        "\n",
                        "event: done\n",
                        "data: {\"type\":\"done\"}\n",
                        "\n",
                    );
                    let _ = write!(
                        s,
                        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
                        body.len(),
                        body
                    );
                } else if path.contains("/messages") {
                    json(&mut s, r#"{"messages":[]}"#);
                } else if path.starts_with("/api/sessions") && line.starts_with("POST") {
                    json(&mut s, r#"{"id":"1","title":"New chat"}"#);
                } else if path.starts_with("/api/sessions") {
                    json(&mut s, r#"{"sessions":[{"id":"1","title":"New chat"}]}"#);
                } else if path.starts_with("/api/models") {
                    json(&mut s, r#"{"models":["remedy"]}"#);
                } else if path.starts_with("/api/ping") || path.starts_with("/api/status") {
                    json(&mut s, r#"{"ok":true}"#);
                } else {
                    json(&mut s, r#"{}"#);
                }
            }
        })
        .expect("spawn mock");
    addr
}

fn json(s: &mut std::net::TcpStream, body: &str) {
    let _ = write!(
        s,
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    );
}

#[test]
fn composer_streams_a_reply() {
    unsafe { std::env::set_var("REMEDY_DESKTOP_NO_SPAWN", "1") };
    let addr = start_mock();
    let origin = format!("http://{addr}");
    // Give the listener a tick to accept.
    thread::sleep(Duration::from_millis(30));

    let studio = Studio::new();
    studio.attach_client(Client::new(&origin, Some("test-token-1")));
    let mut app = TestApp::window({
        let studio = studio.clone();
        move |ui, _| {
            ui.root().child(shell(ui, studio.clone()));
        }
    });

    // Probe + list sessions hop back through the waker.
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(2) {
        app.frames(2);
        thread::sleep(Duration::from_millis(20));
        if app.find(Role::Button, "What can you help with?").is_some() {
            break;
        }
    }
    assert!(
        app.press(Role::Button, "What can you help with?"),
        "starter sends a message"
    );

    let start = Instant::now();
    let mut saw = false;
    // Generous: the loop leaves as soon as the reply shows, and a busy machine needs it.
    while start.elapsed() < Duration::from_secs(15) {
        app.frames(2);
        thread::sleep(Duration::from_millis(25));
        if let Some(ui) = app.ui() {
            let names: Vec<String> = ui
                .reader_view()
                .into_iter()
                .filter_map(|n| n.name)
                .collect();
            if names.iter().any(|n| n.contains("Hello from the mind.")) {
                saw = true;
                break;
            }
        }
    }
    let dump: Vec<String> = app.ui().map(|ui| ui.reader_view().into_iter().filter_map(|n| n.name).collect()).unwrap_or_default();
    assert!(saw, "the streamed assistant reply appears in the feed: {dump:?}");
}
