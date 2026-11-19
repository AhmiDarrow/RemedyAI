//! Relay-free hole punching for phones behind carrier-grade NAT.
//!
//! The phone and this PC each learn the public address of a fresh UDP port
//! with STUN, swap those addresses through the Go runtime (encrypted, over the
//! public MQTT rendezvous), then both send at once so each NAT opens a
//! mapping for the other. iroh is bound to the very port STUN used, so it
//! inherits the mapping. No relay carries any traffic. Works when both NATs
//! map endpoint-independently; symmetric-to-symmetric still needs a relay.

use std::net::{SocketAddr, UdpSocket};
use std::str::FromStr;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, Context, Result};
use iroh::endpoint::presets::{self, Preset};
use iroh::endpoint::PortmapperConfig;
use iroh::{EndpointAddr, EndpointId, RelayMode, SecretKey};
use serde::Deserialize;
use serde_json::json;
use tokio::sync::Semaphore;

use crate::stun::{stun_mapped, DEFAULT_STUN};
use crate::{spawn_accept, Events, ALPN};

/// ALPN the opener uses: the phone never accepts it, the packets only open NATs.
const OPENER_ALPN: &[u8] = b"remedy-punch/0";
/// How long the PC keeps sending toward the phone.
const OPEN_FOR: Duration = Duration::from_secs(15);
/// A punch endpoint nobody used closes after this.
const IDLE_CLOSE: Duration = Duration::from_secs(60);

/// One `{"cmd":"punch",…}` line from the Go runtime.
#[derive(Deserialize)]
pub struct PunchCmd {
    pub id: String,
    pub peer_id: String,
    #[serde(default)]
    pub peer_addrs: Vec<String>,
    #[serde(default)]
    pub stun: Option<Vec<String>>,
}

/// What a punch endpoint needs from the main sidecar.
#[derive(Clone)]
pub struct PunchCtx {
    pub secret: [u8; 32],
    pub forward: SocketAddr,
    pub token: Arc<[u8]>,
    pub events: Events,
    pub slots: Arc<Semaphore>,
}

/// Run one punch: learn a fresh mapping, report it, open our NAT toward the
/// phone, and serve whatever connects on that port.
pub async fn run_punch(ctx: PunchCtx, cmd: PunchCmd) {
    let id = cmd.id.clone();
    if let Err(e) = punch(ctx.clone(), cmd).await {
        ctx.events
            .emit(json!({"event": "punch_ready", "id": id, "error": format!("{e:#}"), "addrs": []}))
            .await;
    }
}

async fn punch(ctx: PunchCtx, cmd: PunchCmd) -> Result<()> {
    let peer = EndpointId::from_str(cmd.peer_id.trim()).context("peer id")?;
    let mut peer_addrs: Vec<SocketAddr> = Vec::new();
    for a in cmd.peer_addrs.iter().take(8) {
        peer_addrs.push(SocketAddr::from_str(a.trim()).context("peer addr")?);
    }
    if peer_addrs.is_empty() {
        bail!("no peer addresses");
    }
    let servers: Vec<String> = match cmd.stun.as_ref().filter(|v| !v.is_empty()) {
        Some(v) => v.clone(),
        None => DEFAULT_STUN.iter().map(|s| s.to_string()).collect(),
    };
    // Learn the mapping on a plain socket, then hand the port to iroh.
    let (port, mapped) = tokio::task::spawn_blocking(move || -> Result<(u16, SocketAddr)> {
        let sock = UdpSocket::bind("0.0.0.0:0")?;
        let port = sock.local_addr()?.port();
        let mapped = stun_mapped(&sock, &servers)?;
        Ok((port, mapped))
    })
    .await??;

    let ep = presets::Minimal
        .apply(iroh::endpoint::Builder::empty())
        .secret_key(SecretKey::from_bytes(&ctx.secret))
        .alpns(vec![ALPN.to_vec()])
        .relay_mode(RelayMode::Disabled)
        .clear_relay_transports()
        .portmapper_config(PortmapperConfig::Disabled)
        .clear_ip_transports()
        .bind_addr(SocketAddr::from(([0, 0, 0, 0], port)))
        .map_err(|e| anyhow!("bind punch port: {e}"))?
        .bind()
        .await
        .context("bind punch endpoint")?;

    let mut addrs = vec![mapped.to_string()];
    for a in ep.addr().ip_addrs() {
        let s = a.to_string();
        if !addrs.contains(&s) {
            addrs.push(s);
        }
    }
    ctx.events
        .emit(json!({"event": "punch_ready", "id": cmd.id, "addrs": addrs, "port": port}))
        .await;

    // Open our NAT toward the phone: every attempt sends QUIC Initials to its
    // public mapping. The phone does not speak OPENER_ALPN, so these never
    // become connections; they only make our NAT accept its packets.
    let opener_ep = ep.clone();
    let mut target = EndpointAddr::new(peer);
    for a in &peer_addrs {
        target = target.with_ip_addr(*a);
    }
    tokio::spawn(async move {
        let start = Instant::now();
        while start.elapsed() < OPEN_FOR {
            let _ = tokio::time::timeout(Duration::from_millis(800), opener_ep.connect(target.clone(), OPENER_ALPN)).await;
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
    });

    let live = Arc::new(AtomicUsize::new(0));
    let used = Arc::new(AtomicUsize::new(0));
    let accept = spawn_accept(
        ep.clone(),
        ctx.forward,
        ctx.token.clone(),
        ctx.events.clone(),
        ctx.slots.clone(),
        Some((live.clone(), used.clone())),
    );
    let start = Instant::now();
    loop {
        tokio::time::sleep(Duration::from_secs(5)).await;
        let idle = live.load(Ordering::SeqCst) == 0;
        if idle && (used.load(Ordering::SeqCst) > 0 || start.elapsed() > IDLE_CLOSE) {
            break;
        }
    }
    accept.abort();
    ep.close().await;
    Ok(())
}

