//! The phone half of the end-to-end check, against a real runtime.
//!
//! Skipped unless `REMEDY_PHONE_E2E_DIR` names a folder where the Go side
//! (`TestPhoneEndToEndHost` in native/go/httpapi) writes a pairing code. It
//! pairs, opens the tunnel and the loopback shim exactly as the app does, and
//! talks to the PC through them.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use remedy_connect_core::{pair, ClientConfig, ConnectClient, FileKeyStore, LoopbackShim, ShimConfig};
use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::Cancel;

#[test]
fn phone_pairs_and_reaches_the_pc_through_the_tunnel() {
    let Ok(dir) = std::env::var("REMEDY_PHONE_E2E_DIR") else {
        eprintln!("skipped: set REMEDY_PHONE_E2E_DIR to run against a live runtime");
        return;
    };
    let dir = PathBuf::from(dir);
    let qr_path = dir.join("qr.txt");
    let started = Instant::now();
    let code = loop {
        if let Ok(text) = std::fs::read_to_string(&qr_path) {
            if text.contains("remedy-connect/1") {
                break text;
            }
        }
        assert!(started.elapsed() < Duration::from_secs(90), "the PC side never wrote a pairing code");
        std::thread::sleep(Duration::from_millis(200));
    };

    // The gateway advertises this PC's LAN address, but the test runtime is
    // bound to loopback so it is not reachable from the network. Dial it there.
    // With REMEDY_PHONE_E2E_DIRECT set, this is the away-from-home case: the
    // phone pairs at home, then its home-network address stops answering and
    // only the direct path (the PC's own endpoint) can carry the session.
    let direct_only = std::env::var("REMEDY_PHONE_E2E_DIRECT").is_ok();
    if direct_only {
        assert!(code.lines().any(|l| l.starts_with("ir=")), "the PC gave no direct endpoint: {code}");
    }
    let home_code = |lan: &str| -> String {
        code.lines()
            .map(|l| if l.starts_with("lan=") { lan.to_string() } else { l.to_string() })
            .collect::<Vec<_>>()
            .join("\n")
    };

    let store = FileKeyStore::new(dir.join("phone.json"));
    if direct_only {
        // A phone the PC has never seen is refused on the direct path: its
        // identity is not one of the paired phones'. Pairing happens at home.
        let stranger = FileKeyStore::new(dir.join("stranger.json"));
        let away = pair(&stranger, &home_code("lan=127.0.0.1:9"), "Stranger", &ClientConfig::default());
        assert!(away.is_err(), "pairing over the direct path must be refused");
    }
    pair(&store, &home_code("lan=127.0.0.1:17481"), "Test phone", &ClientConfig::default())
        .expect("pairing with the real gateway");
    if direct_only {
        use remedy_connect_core::KeyStore;
        let mut record = store.load_pairing().unwrap().expect("paired");
        record.lan = "127.0.0.1:9".parse().unwrap();
        store.save_pairing(&record).unwrap();
    }

    let client = ConnectClient::start_from_store(&store, ClientConfig::default()).expect("start the tunnel");
    client.wait_connected(Duration::from_secs(30)).expect("the tunnel connects with hello");
    if direct_only {
        let via = client.tunnel().map(|t| t.via());
        assert_eq!(via, Some(remedy_connect_core::Via::Direct), "the session must be on the direct path");
    }
    let shim = LoopbackShim::start(client.shim_pipe(), ShimConfig::default()).expect("start the shim");
    let origin = remedy_connect::shim_origin(&shim);

    let http = Http::new(format!("{origin}/api"));
    let cancel = Cancel::new();

    let ping = http.send_blocking(Request::get("ping"), &cancel).expect("GET /api/ping through the tunnel");
    let ping: serde_json::Value = ping.json().expect("ping is JSON");
    assert!(ping.is_object(), "ping: {ping}");

    // What Grove's home asks for first.
    let head = http.send_blocking(Request::get("partner/head"), &cancel).expect("GET /api/partner/head");
    let head: serde_json::Value = head.json().expect("head is JSON");
    assert!(head.get("head").is_some(), "partner/head: {head}");
    let goals = http.send_blocking(Request::get("goals"), &cancel).expect("GET /api/goals");
    assert!(goals.json::<serde_json::Value>().is_ok());
    let task = http.send_blocking(Request::get("life-tasks/current"), &cancel).expect("GET /api/life-tasks/current");
    assert!(task.json::<serde_json::Value>().is_ok());
    let approvals = http.send_blocking(Request::get("approvals"), &cancel).expect("GET /api/approvals");
    assert!(approvals.json::<serde_json::Value>().is_ok());

    // What a phone must never reach, however it asks.
    for refused in ["shutdown", "computer/host/hello", "terminal"] {
        let out = http.send_blocking(Request::post(refused).json(&serde_json::json!({})), &cancel);
        assert!(out.is_err(), "POST /api/{refused} must be refused for a phone");
    }

    // A wrong shim secret gets nothing.
    let stranger = Http::new(format!("http://127.0.0.1:{}/{}/api", shim.port(), "0".repeat(32)));
    assert!(stranger.send_blocking(Request::get("ping"), &cancel).is_err(), "the shim must refuse a wrong secret");

    shim.stop();
    client.stop();
    std::fs::write(dir.join("done"), b"ok").expect("tell the PC side we are done");
}