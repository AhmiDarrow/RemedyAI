//! iroh-probe: a test-only phone stand-in for measuring the iroh transport.
//!
//! Listens on a loopback TCP port; each accepted socket becomes one iroh
//! bidirectional stream to `<endpoint-id>` on ALPN `remedy-connect/1`. The
//! caller (a Go test) then runs the real Noise initiator over that socket, so
//! the probe, like the sidecar, only ever carries ciphertext.
//!
//! usage: iroh-probe <endpoint-id> [--relay-only] [--relay URL] [--listen 127.0.0.1:0]
//! stdout: JSON lines `listening`, `dialed` (dial ms, path, rtt), `path`.

use std::str::FromStr;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use iroh::endpoint::presets::{self, Preset};
use iroh::endpoint::Connection;
use iroh::{EndpointAddr, EndpointId, RelayMode, RelayUrl};
use n0_future::StreamExt;
use serde_json::json;
use tokio::net::TcpListener;

const ALPN: &[u8] = b"remedy-connect/1";

fn emit(v: serde_json::Value) {
    println!("{v}");
}

fn selected(conn: &Connection) -> (&'static str, u64) {
    for p in conn.paths().iter() {
        if p.is_selected() {
            return (if p.is_relay() { "relay" } else { "direct" }, p.rtt().as_millis() as u64);
        }
    }
    ("none", 0)
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let id = args.next().context("endpoint id")?;
    let mut relay_only = false;
    let mut relay: Option<String> = None;
    let mut listen = "127.0.0.1:0".to_string();
    while let Some(a) = args.next() {
        match a.as_str() {
            "--relay-only" => relay_only = true,
            "--relay" => relay = args.next(),
            "--listen" => listen = args.next().context("--listen value")?,
            other => bail!("unknown arg {other}"),
        }
    }
    let target = EndpointId::from_str(id.trim()).context("endpoint id")?;

    let mut builder = presets::N0.apply(iroh::endpoint::Builder::empty());
    if let Some(r) = relay.as_deref().filter(|s| !s.is_empty()) {
        builder = builder.relay_mode(RelayMode::custom([RelayUrl::from_str(r)?]));
    }
    if relay_only {
        // Mobile-data worst case: no direct path at all, relay only.
        builder = builder.clear_ip_transports();
    }
    let ep = builder.bind().await?;
    let _ = tokio::time::timeout(std::time::Duration::from_secs(10), ep.online()).await;

    let ln = TcpListener::bind(listen.as_str()).await?;
    emit(json!({"event": "listening", "addr": ln.local_addr()?.to_string()}));

    let mut addr = EndpointAddr::new(target);
    if let Some(r) = relay.as_deref().filter(|s| !s.is_empty()) {
        addr = addr.with_relay_url(RelayUrl::from_str(r)?);
    }

    loop {
        let (tcp, _) = ln.accept().await?;
        tcp.set_nodelay(true)?;
        let t0 = Instant::now();
        let conn = match ep.connect(addr.clone(), ALPN).await {
            Ok(c) => c,
            Err(e) => {
                emit(json!({"event": "error", "detail": format!("{e:#}")}));
                continue;
            }
        };
        let (kind, rtt) = selected(&conn);
        emit(json!({"event": "dialed", "dial_ms": t0.elapsed().as_millis() as u64, "path": kind, "rtt_ms": rtt}));
        let watch = conn.clone();
        tokio::spawn(async move {
            let mut last = kind.to_string();
            let mut s = watch.paths_stream();
            while s.next().await.is_some() {
                let (k, r) = selected(&watch);
                if k != last {
                    last = k.to_string();
                    emit(json!({"event": "path", "path": k, "rtt_ms": r, "after_ms": t0.elapsed().as_millis() as u64}));
                }
            }
        });
        let (mut send, mut recv) = conn.open_bi().await?;
        let (mut tr, mut tw) = tcp.into_split();
        tokio::spawn(async move {
            let up = async {
                let _ = tokio::io::copy(&mut tr, &mut send).await;
                let _ = send.finish();
            };
            let down = async {
                let _ = tokio::io::copy(&mut recv, &mut tw).await;
            };
            tokio::join!(up, down);
            drop(conn);
        });
    }
}
