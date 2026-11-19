//! remedy-iroh: the PC half of the RemedyConnect iroh transport.
//!
//! iroh only carries bytes. Each bidirectional stream a phone opens on ALPN
//! `remedy-connect/1` is spliced to the Go Connect gateway on loopback, where
//! the usual Noise IK handshake, pinned host key, record framing and deny
//! policy run unchanged. This process never holds a Noise key and never sees
//! plaintext.
//!
//! Control channel (owned by the Go runtime):
//!   stdin, first line: JSON [`Config`]. Keeping secrets off argv keeps them
//!   out of process listings. stdin EOF (parent gone) stops the sidecar.
//!   stdout: one JSON event per line (`ready`, `conn`, `path`, `closed`,
//!   `error`). Events carry path kind and RTT, never peer IPs or payload.
//!
//! Loopback hop: for every stream we dial `forward`, write the 32-byte
//! `token`, then copy bytes both ways. The gateway drops a hop whose token
//! does not match, so another local process cannot pose as this sidecar.

use std::net::SocketAddr;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Context, Result};
use iroh::endpoint::presets::{self, Preset};
use iroh::endpoint::Connection;
use iroh::{RelayMode, RelayUrl, SecretKey};
use n0_future::StreamExt;
use serde::Deserialize;
use serde_json::json;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::{Mutex, Semaphore};

/// ALPN for RemedyConnect over iroh. Bump the suffix on a wire change.
const ALPN: &[u8] = b"remedy-connect/1";
/// Concurrent phone connections. A household has a handful of devices; the
/// cap bounds what an unauthenticated dialer can make us hold open.
const MAX_CONNS: usize = 16;
/// Streams per connection. The phone opens one per Noise session.
const MAX_STREAMS_PER_CONN: u32 = 4;
const LOOPBACK_DIAL: Duration = Duration::from_secs(5);

#[derive(Deserialize)]
struct Config {
    /// 64 hex chars: the iroh Ed25519 secret key. Separate from the Noise
    /// host key on purpose: rotating one never invalidates the other.
    secret_key: String,
    /// Loopback `127.0.0.1:port` of the gateway's iroh hop listener.
    forward: String,
    /// 64 hex chars written first on each loopback hop.
    token: String,
    /// Optional self-hosted `iroh-relay` URL. Empty → n0's public relays.
    #[serde(default)]
    relay_url: String,
}

fn hex_decode(text: &str, want: usize) -> Result<Vec<u8>> {
    let t = text.trim();
    if t.len() != want * 2 {
        bail!("expected {} hex chars", want * 2);
    }
    (0..want)
        .map(|i| u8::from_str_radix(&t[i * 2..i * 2 + 2], 16).map_err(|e| anyhow!(e)))
        .collect()
}

/// Serialised stdout writer so concurrent tasks never interleave a line.
#[derive(Clone)]
struct Events(Arc<Mutex<tokio::io::Stdout>>);

impl Events {
    async fn emit(&self, v: serde_json::Value) {
        let mut line = v.to_string();
        line.push('\n');
        let mut out = self.0.lock().await;
        let _ = out.write_all(line.as_bytes()).await;
        let _ = out.flush().await;
    }
}

fn path_kind(conn: &Connection) -> (&'static str, u64) {
    for p in conn.paths().iter() {
        if p.is_selected() {
            let kind = if p.is_relay() { "relay" } else { "direct" };
            return (kind, p.rtt().as_millis() as u64);
        }
    }
    ("none", 0)
}

#[tokio::main]
async fn main() -> Result<()> {
    let mut stdin = BufReader::new(tokio::io::stdin());
    let mut first = String::new();
    stdin.read_line(&mut first).await.context("read config line")?;
    let cfg: Config = serde_json::from_str(first.trim()).context("parse config")?;

    let sk_bytes: [u8; 32] = hex_decode(&cfg.secret_key, 32)?
        .try_into()
        .map_err(|_| anyhow!("secret key length"))?;
    let token: Arc<[u8]> = hex_decode(&cfg.token, 32)?.into();
    let forward = SocketAddr::from_str(cfg.forward.trim()).context("forward addr")?;
    if !forward.ip().is_loopback() {
        bail!("forward must be loopback");
    }

    let mut builder = presets::N0.apply(iroh::endpoint::Builder::empty());
    builder = builder
        .secret_key(SecretKey::from_bytes(&sk_bytes))
        .alpns(vec![ALPN.to_vec()]);
    let relay = cfg.relay_url.trim().to_string();
    if !relay.is_empty() {
        let url = RelayUrl::from_str(&relay).context("relay_url")?;
        builder = builder.relay_mode(RelayMode::custom([url]));
    }
    let endpoint = builder.bind().await.context("bind iroh endpoint")?;

    let events = Events(Arc::new(Mutex::new(tokio::io::stdout())));
    // Wait (bounded) for a home relay so the advertised address is usable
    // from mobile data straight away. Direct-only still works without it.
    let _ = tokio::time::timeout(Duration::from_secs(10), endpoint.online()).await;
    let addr = endpoint.addr();
    events
        .emit(json!({
            "event": "ready",
            "endpoint_id": endpoint.id().to_string(),
            "relay_url": addr.relay_urls().next().map(|u| u.to_string()),
        }))
        .await;

    let slots = Arc::new(Semaphore::new(MAX_CONNS));
    let accept_ep = endpoint.clone();
    let accept_events = events.clone();
    let accept = tokio::spawn(async move {
        while let Some(incoming) = accept_ep.accept().await {
            let Ok(permit) = slots.clone().try_acquire_owned() else {
                incoming.refuse();
                continue;
            };
            let events = accept_events.clone();
            let token = token.clone();
            tokio::spawn(async move {
                let _permit = permit;
                if let Err(e) = serve_conn(incoming, forward, token, events.clone()).await {
                    events.emit(json!({"event": "error", "detail": format!("{e:#}")})).await;
                }
            });
        }
    });

    // Parent owns our lifetime: stdin EOF → shut down.
    let mut sink = Vec::new();
    let _ = stdin.read_to_end(&mut sink).await;
    accept.abort();
    endpoint.close().await;
    Ok(())
}

async fn serve_conn(
    incoming: iroh::endpoint::Incoming,
    forward: SocketAddr,
    token: Arc<[u8]>,
    events: Events,
) -> Result<()> {
    let conn = incoming.await.context("iroh handshake")?;
    if conn.alpn() != ALPN {
        conn.close(1u32.into(), b"alpn");
        return Ok(());
    }
    let remote = conn.remote_id().fmt_short().to_string();
    let (kind, rtt) = path_kind(&conn);
    events
        .emit(json!({"event": "conn", "remote": remote, "path": kind, "rtt_ms": rtt}))
        .await;

    // Report holepunch upgrades (relay → direct) for the measurement log.
    let watch_conn = conn.clone();
    let watch_events = events.clone();
    let watch_remote = remote.clone();
    let watcher = tokio::spawn(async move {
        let mut last = String::new();
        let mut stream = watch_conn.paths_stream();
        while stream.next().await.is_some() {
            let (kind, rtt) = path_kind(&watch_conn);
            if kind != last {
                last = kind.to_string();
                watch_events
                    .emit(json!({"event": "path", "remote": watch_remote, "path": kind, "rtt_ms": rtt}))
                    .await;
            }
        }
    });

    let mut streams = 0u32;
    loop {
        let (send, recv) = match conn.accept_bi().await {
            Ok(s) => s,
            Err(_) => break,
        };
        streams += 1;
        if streams > MAX_STREAMS_PER_CONN {
            break;
        }
        let token = token.clone();
        tokio::spawn(async move {
            let _ = splice(send, recv, forward, &token).await;
        });
    }
    watcher.abort();
    let reason = conn.close_reason().map(|r| r.to_string()).unwrap_or_default();
    events
        .emit(json!({"event": "closed", "remote": remote, "reason": reason}))
        .await;
    Ok(())
}

async fn splice(
    mut send: iroh::endpoint::SendStream,
    mut recv: iroh::endpoint::RecvStream,
    forward: SocketAddr,
    token: &[u8],
) -> Result<()> {
    let tcp = tokio::time::timeout(LOOPBACK_DIAL, TcpStream::connect(forward))
        .await
        .context("loopback dial timeout")??;
    tcp.set_nodelay(true)?;
    let (mut tr, mut tw) = tcp.into_split();
    tw.write_all(token).await?;
    let up = async {
        let r = tokio::io::copy(&mut recv, &mut tw).await;
        let _ = tw.shutdown().await;
        r
    };
    let down = async {
        let r = tokio::io::copy(&mut tr, &mut send).await;
        let _ = send.finish();
        r
    };
    let _ = tokio::join!(up, down);
    Ok(())
}
