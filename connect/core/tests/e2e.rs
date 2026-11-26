//! End-to-end tests against the in-process fake PC (`tests/common`).
//!
//! Interop with the real Go listener is NOT covered here; see the byte-level
//! vectors in the unit tests and the cross-check test in the crate report.

mod common;

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::Ordering;
use std::sync::mpsc::channel;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use common::{http_response, FakePc};
use remedy_connect_core::clock::FakeClock;
use remedy_connect_core::frame::HttpRequest;
use remedy_connect_core::noise::RekeyPolicy;
use remedy_connect_core::pairing::{b64url_encode, PairingError};
use remedy_connect_core::{
    pair, ClientConfig, ConnState, ConnectClient, DeviceKeys, Error, KeyStore, LoopbackShim, MemoryKeyStore,
    PairingInfo, PairingRecord, ShimConfig, Tunnel,
};

fn now() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs()
}

fn fast() -> ClientConfig {
    ClientConfig {
        local_connect_timeout: Duration::from_secs(2),
        handshake_timeout: Duration::from_secs(3),
        keepalive: Duration::ZERO,
        backoff_min: Duration::from_millis(50),
        backoff_max: Duration::from_millis(200),
        ..ClientConfig::default()
    }
}

fn wait_until(what: &str, secs: u64, mut f: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(secs);
    while Instant::now() < end {
        if f() {
            return;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("timed out waiting for {what}");
}

fn paired(pc: &FakePc, cfg: &ClientConfig) -> (MemoryKeyStore, PairingRecord) {
    let store = MemoryKeyStore::new();
    let rec = pair(&store, &pc.pairing_text(now() + 60), "Pixel 9", cfg).expect("pair");
    (store, rec)
}

fn connected(pc: &FakePc, cfg: ClientConfig) -> (MemoryKeyStore, ConnectClient) {
    let (store, _) = paired(pc, &cfg);
    let c = ConnectClient::start_from_store(&store, cfg).unwrap();
    c.wait_connected(Duration::from_secs(5)).expect("connected");
    (store, c)
}

fn shim_get(port: u16, req: &str) -> String {
    let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    s.write_all(req.as_bytes()).unwrap();
    let mut out = Vec::new();
    let _ = s.read_to_end(&mut out);
    String::from_utf8_lossy(&out).into_owned()
}

// ---- pairing, hello, rejection ------------------------------------------------

#[test]
fn pair_saves_record_names_the_device_and_hello_connects() {
    let pc = FakePc::start();
    let (store, rec) = paired(&pc, &fast());
    assert_eq!(rec.host_key, pc.host_public());
    assert_eq!(rec.lan, pc.addr);
    assert_eq!(pc.device_names(), vec!["Pixel 9".to_string()]);
    assert_eq!(store.load_pairing().unwrap().unwrap(), rec);
    let keys = store.load_device_keys().unwrap().unwrap();
    assert!(pc.inner.devices.lock().unwrap().contains_key(&keys.device_id()));

    let c = ConnectClient::start_from_store(&store, fast()).unwrap();
    c.wait_connected(Duration::from_secs(5)).unwrap();
    assert!(matches!(c.state(), ConnState::Connected { via: remedy_connect_core::Via::Lan }));
    let r = c.http("GET", "/api/ping?x=1", "Accept: */*", b"").unwrap();
    assert_eq!(r.status, 200);
    assert!(r.headers.contains("X-Target: /api/ping?x=1"), "{}", r.headers);
    assert!(r.headers.contains("X-Req-Headers: Accept: */*"));
}

#[test]
fn hello_from_an_unknown_device_is_rejected() {
    let pc = FakePc::start();
    let (_store, rec) = paired(&pc, &fast());
    let stranger = DeviceKeys::generate().unwrap();
    let err = Tunnel::connect_hello(&rec, &stranger, &fast()).unwrap_err();
    assert!(err.to_string().contains("after the handshake"), "{err}");
}

#[test]
fn wrong_pair_secret_fails_closed_and_stores_nothing() {
    let pc = FakePc::start();
    let mut bad = [0u8; 32];
    bad[0] = 9;
    let text = pc.text_with(&pc.host_public(), &bad, now() + 60, "");
    let store = MemoryKeyStore::new();
    let err = pair(&store, &text, "Pixel 9", &fast()).unwrap_err();
    assert!(err.to_string().contains("after the handshake"), "{err}");
    assert!(store.load_pairing().unwrap().is_none());
    assert!(pc.device_names().is_empty());
    // the secret never shows in errors or Debug output
    let info = PairingInfo::parse(&text).unwrap();
    let shown = format!("{err} {err:?} {info:?}");
    assert!(!shown.contains(&b64url_encode(&bad)) && !shown.contains(&hex::encode(bad)));
    // the real secret still works afterwards (one failed try does not burn it)
    assert!(pair(&store, &pc.pairing_text(now() + 60), "Pixel 9", &fast()).is_ok());
}

#[test]
fn expired_pairing_text_is_refused_without_touching_the_network() {
    let pc = FakePc::start();
    let text = pc.pairing_text(now() - 1);
    let store = MemoryKeyStore::new();
    let err = pair(&store, &text, "Pixel 9", &fast()).unwrap_err();
    assert!(matches!(err, Error::Pairing(PairingError::Expired)), "{err:?}");
    assert_eq!(pc.inner.accepted.load(Ordering::SeqCst), 0);
    assert!(store.load_device_keys().unwrap().is_none());
}

#[test]
fn host_key_mismatch_fails_the_handshake() {
    let pc = FakePc::start();
    let other = *DeviceKeys::generate().unwrap().public();
    let text = pc.text_with(&other, &pc.pair_secret(), now() + 60, "");
    let store = MemoryKeyStore::new();
    let err = pair(&store, &text, "Pixel 9", &fast()).unwrap_err();
    assert!(err.to_string().to_lowercase().contains("noise"), "{err}");
    assert_eq!(pc.inner.handshakes.load(Ordering::SeqCst), 0);
    assert!(pc.device_names().is_empty() && store.load_pairing().unwrap().is_none());
}

#[test]
fn pin_refuses_a_different_pc_before_any_connection() {
    let a = FakePc::start();
    let (store, _) = paired(&a, &fast());
    let b = FakePc::start();
    let err = pair(&store, &b.pairing_text(now() + 60), "Pixel 9", &fast()).unwrap_err();
    assert!(matches!(err, Error::PinMismatch), "{err:?}");
    assert_eq!(b.inner.accepted.load(Ordering::SeqCst), 0);
    // the old pairing is untouched
    assert_eq!(store.load_pairing().unwrap().unwrap().host_key, a.host_public());
}

#[test]
fn relay_rdv_and_iroh_fields_are_never_dialed() {
    let decoy = TcpListener::bind("127.0.0.1:0").unwrap();
    decoy.set_nonblocking(true).unwrap();
    let dport = decoy.local_addr().unwrap().port();
    let pc = FakePc::start();
    pc.inner.refuse.store(true, Ordering::SeqCst);
    let extra = format!(
        "relay=127.0.0.1:{dport}\nrdv=127.0.0.1:{dport}\nir={}\nira=127.0.0.1:{dport}\nirp=127.0.0.1:{dport}",
        "ab".repeat(32)
    );
    let text = pc.text_with(&pc.host_public(), &pc.pair_secret(), now() + 60, &extra);
    let info = PairingInfo::parse(&text).unwrap();
    assert!(info.relay.is_some() && !info.rdv.is_empty() && info.iroh_id.is_some(), "parsed and kept");
    let store = MemoryKeyStore::new();
    assert!(pair(&store, &text, "Pixel 9", &fast()).is_err());
    std::thread::sleep(Duration::from_millis(150));
    assert!(decoy.accept().is_err(), "nothing may connect to the relay/rdv/iroh endpoints");
}

// ---- requests ----------------------------------------------------------------------

#[test]
fn seventy_kb_body_is_split_across_records_and_comes_back_whole() {
    let pc = FakePc::start();
    let (_s, c) = connected(&pc, fast());
    let body: Vec<u8> = (0..70_000u32).map(|i| (i % 251) as u8).collect();
    let before = pc.inner.req_frames.load(Ordering::SeqCst);
    let r = c.http("POST", "/api/chat", "Content-Type: application/octet-stream", &body).unwrap();
    assert_eq!(r.status, 200);
    assert_eq!(r.body, body);
    assert!(pc.inner.req_frames.load(Ordering::SeqCst) - before >= 2, "request must span several records");
    // over the PC's 256 KiB assembly cap: refused here, never sent
    let n = pc.request_count();
    let huge = vec![0u8; 300 * 1024];
    assert!(matches!(c.http("POST", "/api/chat", "", &huge), Err(Error::TooLarge(_))));
    assert_eq!(pc.request_count(), n);
}

#[test]
fn rekey_in_both_directions_after_a_forced_low_threshold() {
    let pc = FakePc::start();
    pc.inner.host_rekey_after.store(3, Ordering::SeqCst);
    let cfg = ClientConfig { rekey: RekeyPolicy { records: 4, interval: Duration::from_secs(900) }, ..fast() };
    let (_s, c) = connected(&pc, cfg);
    for i in 0..30 {
        let body = format!("req-{i}");
        let r = c.http("POST", "/api/chat", "", body.as_bytes()).unwrap();
        assert_eq!(r.body, body.as_bytes(), "request {i}");
    }
    assert!(pc.inner.rekeys_in.load(Ordering::SeqCst) >= 6, "phone must rekey its send side");
    assert!(pc.inner.rekeys_out.load(Ordering::SeqCst) >= 6, "phone must rekey its receive side");
    // a big body straddling a rekey still works
    let big = vec![5u8; 70_000];
    assert_eq!(c.http("POST", "/api/chat", "", &big).unwrap().body, big);
}

#[test]
fn denied_path_never_reaches_the_pc() {
    let pc = FakePc::start();
    let (_s, c) = connected(&pc, fast());
    let n = pc.request_count();
    for (m, t) in [("GET", "/api/computer/jobs/next"), ("POST", "/api/connect/pair/start"), ("TRACE", "/api/sessions")] {
        assert!(matches!(c.http(m, t, "", b""), Err(Error::Denied(_))), "{m} {t}");
    }
    assert_eq!(pc.request_count(), n);
}

#[test]
fn shim_round_trip_and_wrong_prefix() {
    let pc = FakePc::start();
    let (_s, c) = connected(&pc, fast());
    let shim = LoopbackShim::start(c.shim_pipe(), ShimConfig::default()).unwrap();
    let (port, t) = (shim.port(), shim.token().to_string());
    assert_eq!(t.len(), 32);

    let r = shim_get(port, &format!("GET /{t}/api/ping?x=1 HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: a=b\r\nAccept: text/plain\r\n\r\n"));
    assert!(r.starts_with("HTTP/1.1 200"), "{r}");
    assert!(r.contains("X-Target: /api/ping?x=1"), "{r}");
    assert!(r.contains(&format!("Set-Cookie: grove_shim={t}")), "{r}");
    assert!(r.contains("Connection: close"));
    // cookie, shim credentials and Host are not passed to the PC
    let seen = pc.inner.requests.lock().unwrap().last().unwrap().clone();
    assert_eq!(seen.headers, "Accept: text/plain");

    let r = shim_get(port, &format!("POST /{t}/api/chat HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"));
    assert!(r.starts_with("HTTP/1.1 200") && r.ends_with("hello"), "{r}");

    let n = pc.request_count();
    let wrong = "0".repeat(32);
    let r = shim_get(port, &format!("GET /{wrong}/api/ping HTTP/1.1\r\n\r\n"));
    assert!(r.starts_with("HTTP/1.1 403") && !r.to_lowercase().contains("set-cookie"), "{r}");
    let r = shim_get(port, "GET /api/ping HTTP/1.1\r\n\r\n");
    assert!(r.starts_with("HTTP/1.1 403"));
    let r = shim_get(port, &format!("GET /{t}/api/computer/jobs/next HTTP/1.1\r\n\r\n"));
    assert!(r.starts_with("HTTP/1.1 403") && r.contains("only for the PC host"), "{r}");
    assert_eq!(pc.request_count(), n, "refused requests must not reach the PC");
    shim.stop();
}

#[test]
fn sse_response_is_delivered_chunk_by_chunk() {
    let pc = FakePc::start();
    let (gate_tx, gate_rx) = channel::<()>();
    let gate = Arc::new(Mutex::new(gate_rx));
    pc.set_handler(move |req: HttpRequest, out| {
        // The client asks for the PC's current addresses when it connects.
        if req.target.contains("/connect/me") {
            out.send(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}");
            out.end();
            return;
        }
        out.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n");
        out.send(b"b\r\ndata: one\n\n\r\n");
        gate.lock().unwrap().recv_timeout(Duration::from_secs(10)).ok();
        out.send(b"b\r\ndata: two\n\n\r\n0\r\n\r\n");
        out.end();
    });
    let (_s, c) = connected(&pc, fast());
    let shim = LoopbackShim::start(c.shim_pipe(), ShimConfig::default()).unwrap();
    let mut s = TcpStream::connect(("127.0.0.1", shim.port())).unwrap();
    s.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
    s.write_all(format!("GET /{}/api/events HTTP/1.1\r\n\r\n", shim.token()).as_bytes()).unwrap();
    let mut got = Vec::new();
    let mut buf = [0u8; 4096];
    while !String::from_utf8_lossy(&got).contains("data: one") {
        let n = s.read(&mut buf).expect("first event must arrive before the stream ends");
        assert!(n > 0, "stream ended early");
        got.extend_from_slice(&buf[..n]);
    }
    let first = String::from_utf8_lossy(&got).into_owned();
    assert!(first.contains("text/event-stream") && !first.contains("data: two"), "{first}");
    gate_tx.send(()).unwrap();
    s.read_to_end(&mut got).unwrap();
    let all = String::from_utf8_lossy(&got);
    assert!(all.contains("data: two") && all.ends_with("0\r\n\r\n"), "{all}");
}

#[test]
fn peer_disconnect_mid_request_fails_the_request_and_the_client_reconnects() {
    let pc = FakePc::start();
    pc.set_handler(|req: HttpRequest, out| {
        if req.target == "/cut" {
            out.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nTransfer-Encoding: chunked\r\n\r\n7\r\npartial\r\n");
            std::thread::sleep(Duration::from_millis(100));
            out.kill();
            return;
        }
        out.send(&http_response(200, &[], b"alive"));
        out.end();
    });
    let (_s, c) = connected(&pc, fast());
    let old = c.tunnel().unwrap();

    let mut rs = c
        .request(&HttpRequest { method: "GET".into(), target: "/cut".into(), ..Default::default() })
        .unwrap();
    let mut got = Vec::new();
    let err = rs.read_to_end(&mut got).unwrap_err();
    assert!(String::from_utf8_lossy(&got).contains("partial"), "data before the cut is still delivered");
    assert_eq!(err.kind(), std::io::ErrorKind::ConnectionAborted);
    wait_until("tunnel to notice", 5, || old.is_closed());
    assert!(old.close_reason().unwrap().contains("closed"), "{:?}", old.close_reason());
    assert!(matches!(old.http("GET", "/x", "", b"", None), Err(Error::Closed(_))));

    wait_until("reconnect", 10, || {
        c.tunnel().is_some_and(|t| !Arc::ptr_eq(&t, &old) && !t.is_closed())
    });
    assert_eq!(c.http("GET", "/ok", "", b"").unwrap().body, b"alive");

    // through the shim the browser sees the truncated stream and a closed socket
    let shim = LoopbackShim::start(c.shim_pipe(), ShimConfig::default()).unwrap();
    let r = shim_get(shim.port(), &format!("GET /{}/cut HTTP/1.1\r\n\r\n", shim.token()));
    assert!(r.contains("partial") && !r.ends_with("0\r\n\r\n"), "{r}");
}

#[test]
fn keepalive_pings_reach_the_pc() {
    let pc = FakePc::start();
    let cfg = ClientConfig { keepalive: Duration::from_millis(40), pong_grace: Duration::ZERO, ..fast() };
    let (_s, _c) = connected(&pc, cfg);
    let base = pc.inner.pings.load(Ordering::SeqCst);
    wait_until("keepalive pings", 5, || pc.inner.pings.load(Ordering::SeqCst) >= base + 3);
}

#[test]
fn a_pc_that_stops_answering_pings_is_not_shown_as_connected() {
    let pc = FakePc::start();
    let cfg = ClientConfig {
        keepalive: Duration::from_millis(60),
        pong_grace: Duration::from_millis(120),
        ..fast()
    };
    let (_s, c) = connected(&pc, cfg);
    // An answering PC stays connected through several pings.
    let base = pc.inner.pings.load(Ordering::SeqCst);
    wait_until("pings answered", 5, || pc.inner.pings.load(Ordering::SeqCst) >= base + 3);
    assert!(matches!(c.state(), ConnState::Connected { .. }), "{:?}", c.state());
    // The path dies without the socket closing; the default read timeout is 100 s.
    pc.inner.mute.store(true, Ordering::SeqCst);
    wait_until("the dead link is noticed", 5, || !matches!(c.state(), ConnState::Connected { .. }));
}

// ---- reconnect and state -------------------------------------------------------------

fn sleeps_secs(clock: &FakeClock) -> Vec<u64> {
    clock.sleeps().iter().map(|d| d.as_secs()).collect()
}

#[test]
fn reconnect_backoff_runs_2_4_8_16_30_30_with_a_fake_clock() {
    let pc = FakePc::start();
    let (store, _) = paired(&pc, &fast());
    pc.inner.refuse.store(true, Ordering::SeqCst); // accepts, then hangs up: handshake fails
    let clock = FakeClock::new();
    let cfg = ClientConfig { clock: clock.clone(), backoff_min: Duration::from_secs(2), backoff_max: Duration::from_secs(30), ..fast() };
    let c = ConnectClient::start_from_store(&store, cfg).unwrap();
    wait_until("seven failed attempts", 20, || clock.sleeps().len() >= 7);
    assert_eq!(&sleeps_secs(&clock)[..7], &[2, 4, 8, 16, 30, 30, 30]);
    match c.state() {
        ConnState::Failed { retry_in, reason } => {
            assert_eq!(retry_in, Duration::from_secs(30));
            assert!(!reason.is_empty());
        }
        ConnState::Connecting { .. } => {}
        other => panic!("unexpected {other:?}"),
    }
    c.stop();
    assert_eq!(c.state(), ConnState::Stopped);
}

#[test]
fn a_healthy_session_resets_the_backoff() {
    let pc = FakePc::start();
    let (store, _) = paired(&pc, &fast());
    pc.inner.refuse.store(true, Ordering::SeqCst);
    let clock = FakeClock::new();
    let cfg = ClientConfig { clock: clock.clone(), backoff_min: Duration::from_secs(2), backoff_max: Duration::from_secs(30), ..fast() };
    let c = ConnectClient::start_from_store(&store, cfg).unwrap();
    wait_until("three failures", 10, || clock.sleeps().len() >= 3);
    assert_eq!(&sleeps_secs(&clock)[..3], &[2, 4, 8]);
    pc.inner.refuse.store(false, Ordering::SeqCst);
    c.wait_connected(Duration::from_secs(10)).unwrap();
    let n0 = clock.sleeps().len();
    // the session has been up for a minute, then the PC vanishes
    clock.advance(Duration::from_secs(60));
    pc.inner.refuse.store(true, Ordering::SeqCst);
    pc.drop_all();
    wait_until("a new failed attempt", 10, || clock.sleeps().len() > n0);
    assert_eq!(clock.sleeps()[n0], Duration::from_secs(2), "backoff must start over after a healthy session");
    c.stop();
}

#[test]
fn state_reports_failed_with_a_reason_after_the_pc_forgets_the_phone() {
    let pc = FakePc::start();
    let (_s, c) = connected(&pc, fast());
    let rx = c.subscribe();
    pc.revoke_all();
    pc.drop_all();
    let reason = loop {
        match rx.recv_timeout(Duration::from_secs(10)).expect("a Failed state") {
            // the first Failed may just be the dropped session; wait for the redial's verdict
            ConnState::Failed { reason, .. } if reason.contains("after the handshake") => break reason,
            _ => continue,
        }
    };
    assert!(reason.contains("after the handshake"), "{reason}");
}
