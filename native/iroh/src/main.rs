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
use iroh::endpoint::{BindOpts, Connection, PortmapperConfig};
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
    /// Relay policy. "off" (default): no relay, no n0 address lookup — the
    /// PC is its own endpoint and the phone dials the addresses in the QR.
    /// "custom": the owner's `iroh-relay` URLs in `relay_urls`. "n0": n0's
    /// public relays and DNS lookup (advanced, opt-in only).
    #[serde(default)]
    relay_mode: String,
    #[serde(default)]
    relay_urls: Vec<String>,
    /// Legacy single relay URL (treated as relay_mode "custom").
    #[serde(default)]
    relay_url: String,
    /// Fixed UDP port so port mapping and manual forwards stay stable.
    /// 0 → ephemeral (tests).
    #[serde(default)]
    bind_port: u16,
    /// UPnP-IGD / PCP / NAT-PMP mapping of `bind_port` on the home router.
    #[serde(default = "default_true")]
    portmap: bool,
}

fn default_true() -> bool {
    true
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

    let mut urls: Vec<String> = cfg.relay_urls.iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    if !cfg.relay_url.trim().is_empty() {
        urls.push(cfg.relay_url.trim().to_string());
    }
    let mode = match cfg.relay_mode.trim() {
        "" if !urls.is_empty() => "custom",
        "" => "off",
        m => m,
    }
    .to_string();

    let mut builder = match mode.as_str() {
        // n0 preset: n0 relays + n0 DNS/pkarr address lookup.
        "n0" => presets::N0.apply(iroh::endpoint::Builder::empty()),
        // Crypto provider only: nothing is published to or fetched from n0.
        _ => presets::Minimal.apply(iroh::endpoint::Builder::empty()),
    };
    builder = builder
        .secret_key(SecretKey::from_bytes(&sk_bytes))
        .alpns(vec![ALPN.to_vec()]);
    match mode.as_str() {
        "n0" => {}
        "custom" => {
            if urls.is_empty() {
                bail!("relay_mode custom needs relay_urls");
            }
            let parsed = urls
                .iter()
                .map(|u| RelayUrl::from_str(u).context("relay url"))
                .collect::<Result<Vec<_>>>()?;
            builder = builder.relay_mode(RelayMode::custom(parsed));
        }
        "off" => builder = builder.relay_mode(RelayMode::Disabled).clear_relay_transports(),
        other => bail!("unknown relay_mode {other}"),
    }
    if cfg.bind_port != 0 {
        builder = builder
            .clear_ip_transports()
            .bind_addr(SocketAddr::from(([0, 0, 0, 0], cfg.bind_port)))
            .map_err(|e| anyhow!("bind v4: {e}"))?;
        // IPv6 is best effort: hosts without v6 still serve v4.
        builder = builder
            .bind_addr_with_opts(
                SocketAddr::from((std::net::Ipv6Addr::UNSPECIFIED, cfg.bind_port)),
                BindOpts::default().set_is_required(false),
            )
            .map_err(|e| anyhow!("bind v6: {e}"))?;
    }
    // iroh only drives its built-in portmapper alongside relay-based net
    // reports; with relays off it never runs. We map the fixed port ourselves
    // below and advertise the result, so turn the built-in one off.
    builder = builder.portmapper_config(PortmapperConfig::Disabled);
    let endpoint = builder.bind().await.context("bind iroh endpoint")?;

    let events = Events(Arc::new(Mutex::new(tokio::io::stdout())));
    if mode != "off" {
        // Wait (bounded) for a home relay so the advertised address works.
        let _ = tokio::time::timeout(Duration::from_secs(10), endpoint.online()).await;
    }
    let addr = endpoint.addr();
    events
        .emit(json!({
            "event": "ready",
            "endpoint_id": endpoint.id().to_string(),
            "relay_mode": mode,
            "relay_url": addr.relay_urls().next().map(|u| u.to_string()),
            "bound": endpoint.bound_sockets().iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        }))
        .await;

    if cfg.portmap && cfg.bind_port != 0 {
        spawn_portmap(endpoint.clone(), cfg.bind_port, events.clone());
    }

    // Report direct addresses as they settle (interfaces, then the
    // router-mapped public address once UPnP/PCP/NAT-PMP answers).
    let addr_ep = endpoint.clone();
    let addr_events = events.clone();
    tokio::spawn(async move {
        let mut last: Vec<String> = Vec::new();
        loop {
            let mut now: Vec<String> = addr_ep.addr().ip_addrs().map(|a| a.to_string()).collect();
            now.sort();
            if now != last {
                addr_events.emit(json!({"event": "addrs", "addrs": now})).await;
                last = now;
            }
            tokio::time::sleep(Duration::from_secs(2)).await;
        }
    });

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

/// Map `port` on the home router (UPnP-IGD, PCP, NAT-PMP) and advertise
/// the external address. Reports which protocols answered so the Go side
/// can explain a failure (no mapping protocol, or a CGNAT/private "external"
/// address that a phone on mobile data still cannot reach).
fn spawn_portmap(endpoint: iroh::Endpoint, port: u16, events: Events) {
    tokio::spawn(async move {
        let client = portmapper::Client::new(portmapper::Config::default());
        let probe = match tokio::time::timeout(Duration::from_secs(10), client.probe()).await {
            Ok(Ok(Ok(p))) => json!({"upnp": p.upnp, "pcp": p.pcp, "nat_pmp": p.nat_pmp}),
            Ok(Ok(Err(e))) => json!({"error": format!("{e}")}),
            _ => json!({"error": "probe timed out"}),
        };
        events.emit(json!({"event": "portmap_probe", "probe": probe})).await;
        let Some(local) = std::num::NonZeroU16::new(port) else { return };
        client.update_local_port(local);
        client.procure_mapping();
        let mut watch = client.watch_external_address();
        let mut current: Option<std::net::SocketAddrV4> = None;
        loop {
            let next = *watch.borrow_and_update();
            if next != current {
                if let Some(old) = current {
                    endpoint.remove_external_addr(&SocketAddr::V4(old)).await;
                }
                if let Some(new) = next {
                    endpoint.add_external_addr(SocketAddr::V4(new)).await;
                }
                events
                    .emit(json!({"event": "mapped", "external": next.map(|a| a.to_string())}))
                    .await;
                current = next;
            }
            if watch.changed().await.is_err() {
                break;
            }
        }
        drop(client);
    });
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_decode_checks_length_and_digits() {
        assert_eq!(hex_decode("00ff", 2).unwrap(), vec![0, 255]);
        assert!(hex_decode("00f", 2).is_err());
        assert!(hex_decode("zz00", 2).is_err());
    }

    #[test]
    fn config_defaults_are_private_and_mapped() {
        let cfg: Config = serde_json::from_str(
            r#"{"secret_key":"00","forward":"127.0.0.1:1","token":"00"}"#,
        )
        .unwrap();
        // No relay unless asked for; router mapping on by default.
        assert!(cfg.relay_mode.is_empty());
        assert!(cfg.relay_urls.is_empty() && cfg.relay_url.is_empty());
        assert!(cfg.portmap);
        assert_eq!(cfg.bind_port, 0);
    }

    #[test]
    fn config_reads_owner_choices() {
        let cfg: Config = serde_json::from_str(
            r#"{"secret_key":"00","forward":"127.0.0.1:1","token":"00","relay_mode":"custom",
                "relay_urls":["https://relay.example.com"],"bind_port":7403,"portmap":false}"#,
        )
        .unwrap();
        assert_eq!(cfg.relay_mode, "custom");
        assert_eq!(cfg.relay_urls, vec!["https://relay.example.com".to_string()]);
        assert_eq!(cfg.bind_port, 7403);
        assert!(!cfg.portmap);
    }
}
