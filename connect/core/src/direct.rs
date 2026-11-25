//! The path that works away from home: a QUIC stream straight to the PC's own
//! endpoint (iroh), or through a relay the owner runs. No third party is
//! involved unless the owner chose one: relays are off unless the pairing code
//! names one.
//!
//! The PC's endpoint hands each stream to the same gateway the LAN listener
//! feeds, so what travels inside is the same Noise session as on every other
//! path. To keep one tunnel implementation, the stream is carried to it
//! through a loopback socket: [`bridge`] returns a `127.0.0.1` address, and a
//! connection to it is the stream to the PC.

use std::net::SocketAddr;
use std::str::FromStr;
use std::sync::OnceLock;
use std::time::Duration;

use iroh::endpoint::presets::{self, Preset};
use iroh::endpoint::PortmapperConfig;
use iroh::{EndpointAddr, EndpointId, RelayMode, RelayUrl};
use tokio::io::AsyncWriteExt;
use tokio::net::TcpListener;
use tokio::runtime::Runtime;

use crate::error::{Error, Result};
use crate::pairing::PairingRecord;

/// The protocol name the PC's endpoint accepts. Bump with the PC's on a wire change.
const ALPN: &[u8] = b"remedy-connect/1";

/// How long the loopback side waits for the tunnel code to connect to it.
const LOCAL_ACCEPT: Duration = Duration::from_secs(5);

/// Where the PC's endpoint is: its id, the addresses it gave at pairing, and
/// the owner's relay when there is one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Target {
    pub id: String,
    pub addrs: Vec<SocketAddr>,
    pub relay: Option<String>,
}

impl Target {
    /// From the pairing code's `ir` / `ira` / `irr` lines. `None` when the PC
    /// has no endpoint for this path (the owner has not turned it on).
    pub fn from_fields(id: Option<&str>, addrs: &str, relay: Option<&str>) -> Option<Target> {
        let id = id?.trim();
        if id.is_empty() {
            return None;
        }
        let addrs: Vec<SocketAddr> = addrs.split(',').filter_map(|a| a.trim().parse().ok()).collect();
        let relay = relay.map(str::trim).filter(|r| !r.is_empty()).map(String::from);
        // Nothing to dial: no address the PC gave, and no relay to find it through.
        if addrs.is_empty() && relay.is_none() {
            return None;
        }
        Some(Target { id: id.to_string(), addrs, relay })
    }

    /// From a stored pairing (the lines are kept in `extras`).
    pub fn from_record(record: &PairingRecord) -> Option<Target> {
        let get = |k: &str| record.extras.get(k).map(String::as_str);
        Target::from_fields(get("ir"), get("ira").unwrap_or(""), get("irr"))
    }
}

fn runtime() -> Result<&'static Runtime> {
    static RT: OnceLock<std::result::Result<Runtime, String>> = OnceLock::new();
    RT.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .thread_name("remedy-direct")
            .enable_all()
            .build()
            .map_err(|e| e.to_string())
    })
    .as_ref()
    .map_err(|e| Error::Unreachable(format!("direct path could not start: {e}")))
}

/// Opens a stream to the PC's endpoint and returns the loopback address that
/// carries it. The first connection to that address gets the stream; when it
/// closes, the stream and the endpoint go with it.
pub fn bridge(target: &Target, timeout: Duration) -> Result<SocketAddr> {
    let rt = runtime()?;
    let target = target.clone();
    rt.block_on(async move {
        match tokio::time::timeout(timeout, open(target)).await {
            Ok(r) => r,
            Err(_) => Err(Error::Timeout("the PC's direct address did not answer")),
        }
    })
}

async fn open(target: Target) -> Result<SocketAddr> {
    let unreachable = |what: &str, e: &dyn std::fmt::Display| Error::Unreachable(format!("direct: {what}: {e}"));
    let peer = EndpointId::from_str(&target.id).map_err(|e| unreachable("endpoint id", &e))?;

    // Crypto only: nothing is published to or looked up from anyone.
    let mut builder = presets::Minimal.apply(iroh::endpoint::Builder::empty());
    builder = match &target.relay {
        Some(url) => {
            let url = RelayUrl::from_str(url).map_err(|e| unreachable("relay address", &e))?;
            builder.relay_mode(RelayMode::custom([url]))
        }
        None => builder.relay_mode(RelayMode::Disabled).clear_relay_transports(),
    };
    builder = builder.portmapper_config(PortmapperConfig::Disabled);
    let endpoint = builder.bind().await.map_err(|e| unreachable("could not open a socket", &e))?;

    let mut addr = EndpointAddr::new(peer);
    for a in &target.addrs {
        addr = addr.with_ip_addr(*a);
    }
    if let Some(url) = &target.relay {
        if let Ok(url) = RelayUrl::from_str(url) {
            addr = addr.with_relay_url(url);
        }
    }
    let conn = endpoint.connect(addr, ALPN).await.map_err(|e| unreachable("no answer", &e))?;
    let (mut send, mut recv) = conn.open_bi().await.map_err(|e| unreachable("stream", &e))?;

    let listener = TcpListener::bind(("127.0.0.1", 0)).await.map_err(Error::Io)?;
    let local = listener.local_addr().map_err(Error::Io)?;
    tokio::spawn(async move {
        // Held until the tunnel is done with the stream.
        let _keep = (endpoint, conn);
        let Ok(Ok((tcp, _))) = tokio::time::timeout(LOCAL_ACCEPT, listener.accept()).await else {
            return;
        };
        drop(listener);
        let _ = tcp.set_nodelay(true);
        let (mut tr, mut tw) = tcp.into_split();
        let up = async {
            let r = tokio::io::copy(&mut tr, &mut send).await;
            let _ = send.finish();
            r
        };
        let down = async {
            let r = tokio::io::copy(&mut recv, &mut tw).await;
            let _ = tw.shutdown().await;
            r
        };
        let _ = tokio::join!(up, down);
    });
    Ok(local)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_target_needs_an_id_and_somewhere_to_dial() {
        let id = "32f964994affa248ee05d85780291e07535c4229dfcba0bbde4c44f94594c602";
        assert_eq!(Target::from_fields(None, "203.0.113.5:7403", None), None);
        assert_eq!(Target::from_fields(Some("  "), "203.0.113.5:7403", None), None);
        assert_eq!(Target::from_fields(Some(id), "", None), None, "no address and no relay");
        let t = Target::from_fields(Some(id), "203.0.113.5:7403, [2001:db8::1]:7403,junk", None).unwrap();
        assert_eq!(t.addrs.len(), 2);
        assert_eq!(t.relay, None);
        let r = Target::from_fields(Some(id), "", Some(" https://relay.example ")).unwrap();
        assert!(r.addrs.is_empty());
        assert_eq!(r.relay.as_deref(), Some("https://relay.example"));
    }

    #[test]
    fn an_unanswered_address_fails_within_the_timeout() {
        // TEST-NET-3: nothing answers. The dial must give up, not hang.
        let id = "32f964994affa248ee05d85780291e07535c4229dfcba0bbde4c44f94594c602";
        let t = Target::from_fields(Some(id), "203.0.113.5:7403", None).unwrap();
        let started = std::time::Instant::now();
        assert!(bridge(&t, Duration::from_millis(600)).is_err());
        assert!(started.elapsed() < Duration::from_secs(5));
    }
}
