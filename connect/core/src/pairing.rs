//! Pairing text (QR / paste): strict parser, expiry and host-key pin.
//!
//! ```text
//! remedy-connect/1
//! hp=<urlsafe b64, 32-byte host public key>
//! ps=<urlsafe b64, 32-byte one-use pair secret>
//! lan=<ipv4:port>
//! ts=<ipv4:port>        optional, Tailscale
//! v6=<ipv6 literal>     optional
//! relay=<host:port>     parsed, never dialed
//! rdv=<host:port;...>   parsed, never dialed
//! ir= ira= irr= irm= irp=   iroh fields, parsed, never dialed
//! exp=<unix seconds>
//! ```
//!
//! Anything that does not parse fails the whole text (fail closed).

use std::collections::BTreeMap;
use std::fmt;
use std::net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr};

use serde::{Deserialize, Serialize};
use zeroize::{Zeroize, ZeroizeOnDrop};

use crate::error::{Error, Result};
use crate::util::{ct_eq, hex32, unix_now};

pub const HEADER: &str = "remedy-connect/1";
pub const DEFAULT_PORT: u16 = 7401;
const MAX_CHARS: usize = 16 * 1024;
const MAX_LINES: usize = 32;
const MAX_RDV: usize = 8;
const MAX_IROH_ADDRS: usize = 8;

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum PairingError {
    #[error("Pairing code is too large.")]
    TooLarge,
    #[error("Pairing code has too many fields.")]
    TooManyLines,
    #[error("Not a RemedyConnect pairing code.")]
    NotRemedy,
    #[error("Pairing code has a line that is not key=value.")]
    BadLine,
    #[error("Pairing code repeats a field.")]
    Duplicate,
    #[error("Pairing code is missing the {0} field.")]
    Missing(&'static str),
    #[error("Pairing field {0} is not valid urlsafe base64.")]
    BadBase64(&'static str),
    #[error("Pairing field {0} must be 32 bytes.")]
    BadKeyLength(&'static str),
    #[error("Pairing code expiry is not a number.")]
    BadExpiry,
    #[error("This pairing code has expired. Ask the PC for a new one.")]
    Expired,
    #[error("Pairing field {0}: {1}")]
    BadEndpoint(&'static str, &'static str),
    #[error("The {0} line must not carry secrets.")]
    Secrets(&'static str),
}

/// A `host:port` that is only recorded (relay, rendezvous, iroh brokers).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HostPort {
    pub host: String,
    pub port: u16,
}

impl fmt::Display for HostPort {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.host.contains(':') {
            write!(f, "[{}]:{}", self.host, self.port)
        } else {
            write!(f, "{}:{}", self.host, self.port)
        }
    }
}

/// The one-use pair secret. Zeroized on drop; never printed.
#[derive(Clone, Zeroize, ZeroizeOnDrop)]
pub struct PairSecret([u8; 32]);

impl PairSecret {
    pub fn new(bytes: [u8; 32]) -> Self {
        PairSecret(bytes)
    }

    pub(crate) fn expose(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for PairSecret {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("PairSecret(<redacted>)")
    }
}

impl PartialEq for PairSecret {
    fn eq(&self, other: &Self) -> bool {
        ct_eq(&self.0, &other.0)
    }
}

/// A parsed, validated pairing text.
#[derive(Debug, Clone, PartialEq)]
pub struct PairingInfo {
    pub host_key: [u8; 32],
    pub pair_secret: PairSecret,
    pub lan: SocketAddr,
    pub tailscale: Option<SocketAddr>,
    pub v6: Option<SocketAddr>,
    pub expires_at: u64,
    /// Parsed only. This crate never dials it.
    pub relay: Option<HostPort>,
    /// Parsed only. This crate never dials these.
    pub rdv: Vec<HostPort>,
    /// iroh endpoint id (64 lowercase hex). Parsed only.
    pub iroh_id: Option<String>,
    pub iroh_addrs: Vec<String>,
    pub iroh_relay: Option<String>,
    pub iroh_n0: bool,
    pub iroh_punch: Vec<HostPort>,
}

impl PairingInfo {
    /// Parse and validate against the system clock.
    pub fn parse(text: &str) -> Result<PairingInfo> {
        Self::parse_at(text, unix_now())
    }

    /// Parse and validate as of `now_unix`. A code is valid up to and
    /// including its `exp` second.
    pub fn parse_at(text: &str, now_unix: u64) -> Result<PairingInfo> {
        match unlink(text) {
            Some(lines) => parse(&lines, now_unix),
            None => parse(text, now_unix),
        }
        .map_err(Error::from)
    }
}

/// The one-line form of a pairing code: the same lines, base64url, behind this prefix.
/// One line survives a paste and a message, and a scanned code in it can open the app.
pub const LINK_PREFIX: &str = "remedyconnect://pair#";

/// A pairing code as one line ([`LINK_PREFIX`]).
pub fn to_link(text: &str) -> String {
    format!("{LINK_PREFIX}{}", b64url_encode(text.as_bytes()))
}

/// The pairing lines inside a link, or `None` when `text` is not a link (a link whose
/// payload does not decode gives lines that fail to parse, so the error names the code).
fn unlink(text: &str) -> Option<String> {
    let body = text.trim().strip_prefix(LINK_PREFIX)?;
    Some(b64url_decode(body).ok().and_then(|b| String::from_utf8(b).ok()).unwrap_or_default())
}

/// What is kept after a successful pairing (the pair secret is not).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PairingRecord {
    #[serde(with = "hex32")]
    pub host_key: [u8; 32],
    pub lan: SocketAddr,
    pub tailscale: Option<SocketAddr>,
    pub v6: Option<SocketAddr>,
    pub device_name: String,
    pub paired_at: u64,
    /// Raw text of fields this crate does not dial (`relay`, `rdv`, `ir*`),
    /// kept so a later transport does not need a re-pair.
    #[serde(default)]
    pub extras: BTreeMap<String, String>,
}

impl PairingRecord {
    pub fn from_info(info: &PairingInfo, device_name: &str) -> PairingRecord {
        let mut extras = BTreeMap::new();
        if let Some(r) = &info.relay {
            extras.insert("relay".into(), r.to_string());
        }
        if !info.rdv.is_empty() {
            extras.insert("rdv".into(), join_hp(&info.rdv));
        }
        if let Some(id) = &info.iroh_id {
            extras.insert("ir".into(), id.clone());
        }
        if !info.iroh_addrs.is_empty() {
            extras.insert("ira".into(), info.iroh_addrs.join(","));
        }
        if let Some(u) = &info.iroh_relay {
            extras.insert("irr".into(), u.clone());
        }
        if info.iroh_n0 {
            extras.insert("irm".into(), "n0".into());
        }
        if !info.iroh_punch.is_empty() {
            extras.insert("irp".into(), join_hp(&info.iroh_punch));
        }
        PairingRecord {
            host_key: info.host_key,
            lan: info.lan,
            tailscale: info.tailscale,
            v6: info.v6,
            device_name: device_name.to_string(),
            paired_at: unix_now(),
            extras,
        }
    }
}

fn join_hp(list: &[HostPort]) -> String {
    list.iter().map(|h| h.to_string()).collect::<Vec<_>>().join(";")
}

/// Pin check: once a PC is paired, a later code with another host key is
/// refused. `stored = None` means nothing is paired yet.
pub fn check_pin(stored: Option<&[u8; 32]>, candidate: &[u8; 32]) -> Result<()> {
    match stored {
        None => Ok(()),
        Some(s) if ct_eq(s, candidate) => Ok(()),
        Some(_) => Err(Error::PinMismatch),
    }
}

// ---- urlsafe base64 (padding optional, canonical only) -------------------

pub fn b64url_encode(data: &[u8]) -> String {
    const A: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        out.push(A[(n >> 18) as usize & 63] as char);
        out.push(A[(n >> 12) as usize & 63] as char);
        if c.len() > 1 {
            out.push(A[(n >> 6) as usize & 63] as char);
        }
        if c.len() > 2 {
            out.push(A[n as usize & 63] as char);
        }
    }
    out
}

pub fn b64url_decode(text: &str) -> std::result::Result<Vec<u8>, ()> {
    let t = text.trim();
    let t = t.trim_end_matches('=');
    if t.len() % 4 == 1 {
        return Err(());
    }
    let mut out = Vec::with_capacity(t.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0u32);
    for ch in t.bytes() {
        let v = match ch {
            b'A'..=b'Z' => ch - b'A',
            b'a'..=b'z' => ch - b'a' + 26,
            b'0'..=b'9' => ch - b'0' + 52,
            b'-' => 62,
            b'_' => 63,
            _ => return Err(()),
        } as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    if acc != 0 {
        return Err(()); // non-canonical trailing bits
    }
    Ok(out)
}

fn key32(raw: &str, label: &'static str) -> std::result::Result<[u8; 32], PairingError> {
    let bytes = b64url_decode(raw).map_err(|_| PairingError::BadBase64(label))?;
    <[u8; 32]>::try_from(bytes.as_slice()).map_err(|_| PairingError::BadKeyLength(label))
}

// ---- endpoints -------------------------------------------------------------

fn port_of(text: &str, label: &'static str) -> std::result::Result<u16, PairingError> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(PairingError::BadEndpoint(label, "port is not a number."));
    }
    match text.parse::<u32>() {
        Ok(p) if (1..=65535).contains(&p) => Ok(p as u16),
        _ => Err(PairingError::BadEndpoint(label, "port is out of range.")),
    }
}

/// `ipv4:port` (lan, ts).
fn parse_v4_endpoint(raw: &str, label: &'static str) -> std::result::Result<SocketAddr, PairingError> {
    let idx = raw
        .rfind(':')
        .filter(|&i| i > 0 && i + 1 < raw.len())
        .ok_or(PairingError::BadEndpoint(label, "must be ipv4:port."))?;
    let port = port_of(&raw[idx + 1..], label)?;
    let ip: Ipv4Addr = raw[..idx]
        .parse()
        .map_err(|_| PairingError::BadEndpoint(label, "host must be an IPv4 literal."))?;
    if ip.is_unspecified() || ip.is_multicast() || ip.is_broadcast() {
        return Err(PairingError::BadEndpoint(label, "host must be a unicast address."));
    }
    Ok(SocketAddr::new(IpAddr::V4(ip), port))
}

/// `[v6]:port`, `[v6]` or bare `v6` (port defaults to the lan port).
fn parse_v6_endpoint(raw: &str, default_port: u16) -> std::result::Result<SocketAddr, PairingError> {
    let bad = PairingError::BadEndpoint("v6", "must be an IPv6 literal.");
    let t = raw.trim();
    let (host, port) = if let Some(rest) = t.strip_prefix('[') {
        let close = rest.find(']').ok_or_else(|| bad.clone())?;
        let after = &rest[close + 1..];
        let port = match after.strip_prefix(':') {
            Some(p) => port_of(p, "v6")?,
            None if after.is_empty() => default_port,
            None => return Err(bad),
        };
        (&rest[..close], port)
    } else {
        (t, default_port)
    };
    let ip: Ipv6Addr = host.parse().map_err(|_| bad)?;
    if ip.is_unspecified() || ip.is_multicast() {
        return Err(PairingError::BadEndpoint("v6", "host must be a unicast address."));
    }
    Ok(SocketAddr::new(IpAddr::V6(ip), port))
}

/// `host:port`, bracketed v6 allowed. Names are accepted because the text is
/// only recorded; wildcards and URLs are refused.
fn parse_host_port(raw: &str, label: &'static str) -> std::result::Result<HostPort, PairingError> {
    let t = raw.trim();
    let lower = t.to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        return Err(PairingError::BadEndpoint(label, "is a TCP endpoint, not an HTTP URL."));
    }
    let idx = t
        .rfind(':')
        .filter(|&i| i > 0 && i + 1 < t.len())
        .ok_or(PairingError::BadEndpoint(label, "must be host:port."))?;
    let port = port_of(&t[idx + 1..], label)?;
    let host = t[..idx].trim().trim_matches(['[', ']']);
    if host.is_empty() || host == "0.0.0.0" || host == "*" || host == "::" {
        return Err(PairingError::BadEndpoint(label, "must not be empty or a wildcard."));
    }
    if host.chars().any(|c| c.is_whitespace() || c.is_control() || c == '/' || c == '@') {
        return Err(PairingError::BadEndpoint(label, "host has illegal characters."));
    }
    Ok(HostPort { host: host.to_string(), port })
}

fn parse_host_list(raw: &str, label: &'static str) -> std::result::Result<Vec<HostPort>, PairingError> {
    let mut out = Vec::new();
    for chunk in raw.split(';') {
        let c = chunk.trim();
        if !c.is_empty() {
            out.push(parse_host_port(c, label)?);
        }
    }
    if out.is_empty() {
        return Err(PairingError::BadEndpoint(label, "list is empty."));
    }
    if out.len() > MAX_RDV {
        return Err(PairingError::BadEndpoint(label, "list has too many hosts."));
    }
    Ok(out)
}

fn parse_iroh_id(raw: &str) -> std::result::Result<String, PairingError> {
    let id = raw.trim().to_ascii_lowercase();
    if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return Err(PairingError::BadEndpoint("ir", "must be 64 hex characters."));
    }
    Ok(id)
}

fn parse_iroh_addrs(raw: &str) -> std::result::Result<Vec<String>, PairingError> {
    let mut out = Vec::new();
    for chunk in raw.split(',') {
        let c = chunk.trim();
        if c.is_empty() {
            continue;
        }
        let sa: SocketAddr = c
            .parse()
            .map_err(|_| PairingError::BadEndpoint("ira", "must be a literal ip:port or [v6]:port."))?;
        if sa.ip().is_unspecified() {
            return Err(PairingError::BadEndpoint("ira", "must not be a wildcard."));
        }
        if sa.port() == 0 {
            return Err(PairingError::BadEndpoint("ira", "port is out of range."));
        }
        out.push(c.to_string());
    }
    if out.len() > MAX_IROH_ADDRS {
        return Err(PairingError::BadEndpoint("ira", "list is too long."));
    }
    Ok(out)
}

fn parse_iroh_relay(raw: &str) -> std::result::Result<String, PairingError> {
    let t = raw.trim();
    let lower = t.to_ascii_lowercase();
    let rest = lower
        .strip_prefix("https://")
        .or_else(|| lower.strip_prefix("http://"))
        .ok_or(PairingError::BadEndpoint("irr", "must be an http(s) URL."))?;
    let authority = rest.split('/').next().unwrap_or("");
    if authority.is_empty() || authority.contains('@') || t.contains('?') || t.contains('#') {
        return Err(PairingError::BadEndpoint("irr", "must not carry credentials or parameters."));
    }
    if t.chars().any(|c| c.is_whitespace() || c.is_control()) {
        return Err(PairingError::BadEndpoint("irr", "is not a valid URL."));
    }
    Ok(t.to_string())
}

// ---- the parser ------------------------------------------------------------

fn parse(text: &str, now_unix: u64) -> std::result::Result<PairingInfo, PairingError> {
    if text.len() > MAX_CHARS {
        return Err(PairingError::TooLarge);
    }
    let lines: Vec<&str> = text
        .split(['\n', '\r'])
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .collect();
    if lines.len() > MAX_LINES {
        return Err(PairingError::TooManyLines);
    }
    if lines.first().copied() != Some(HEADER) {
        return Err(PairingError::NotRemedy);
    }
    let mut fields: BTreeMap<String, String> = BTreeMap::new();
    for line in &lines[1..] {
        let eq = line.find('=').filter(|&i| i > 0).ok_or(PairingError::BadLine)?;
        let key = line[..eq].trim().to_ascii_lowercase();
        let value = line[eq + 1..].trim().to_string();
        // The host never puts local auth into a pairing text; if it shows up
        // the text was tampered with. (hp/ps are random base64, skipped.)
        if key != "hp" && key != "ps" {
            let low = value.to_ascii_lowercase();
            if low.contains("bearer") || low.contains("local_api_token") {
                return Err(PairingError::Secrets("pairing"));
            }
        }
        if fields.insert(key, value).is_some() {
            return Err(PairingError::Duplicate);
        }
    }
    let get = |k: &str| fields.get(k).map(String::as_str);
    let non_empty = |k: &str| fields.get(k).map(String::as_str).filter(|v| !v.is_empty());

    let hp = get("hp").ok_or(PairingError::Missing("hp"))?;
    let ps = get("ps").ok_or(PairingError::Missing("ps"))?;
    let exp_raw = get("exp").ok_or(PairingError::Missing("exp"))?;
    let lan_raw = get("lan").ok_or(PairingError::Missing("lan"))?;

    let host_key = key32(hp, "hp")?;
    let mut secret = key32(ps, "ps")?;
    let pair_secret = PairSecret::new(secret);
    secret.zeroize();
    let expires_at: u64 = exp_raw.parse().map_err(|_| PairingError::BadExpiry)?;
    if now_unix > expires_at {
        return Err(PairingError::Expired);
    }
    let lan = parse_v4_endpoint(lan_raw, "lan")?;
    let tailscale = non_empty("ts").map(|v| parse_v4_endpoint(v, "ts")).transpose()?;
    let v6 = non_empty("v6").map(|v| parse_v6_endpoint(v, lan.port())).transpose()?;
    let relay = non_empty("relay").map(|v| parse_host_port(v, "relay")).transpose()?;
    let rdv = non_empty("rdv").map(|v| parse_host_list(v, "rdv")).transpose()?.unwrap_or_default();
    let iroh_id = non_empty("ir").map(parse_iroh_id).transpose()?;
    let iroh_relay = non_empty("irr").map(parse_iroh_relay).transpose()?;
    let iroh_addrs = non_empty("ira").map(parse_iroh_addrs).transpose()?.unwrap_or_default();
    let iroh_n0 = match get("irm").map(|v| v.trim().to_ascii_lowercase()).as_deref() {
        None | Some("") => false,
        Some("n0") => true,
        Some(_) => return Err(PairingError::BadEndpoint("irm", "unknown iroh relay mode.")),
    };
    let iroh_punch = non_empty("irp").map(|v| parse_host_list(v, "irp")).transpose()?.unwrap_or_default();

    Ok(PairingInfo {
        host_key,
        pair_secret,
        lan,
        tailscale,
        v6,
        expires_at,
        relay,
        rdv,
        iroh_id,
        iroh_addrs,
        iroh_relay,
        iroh_n0,
        iroh_punch,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_800_000_000;
    const HP: [u8; 32] = [0x11; 32];
    const PS: [u8; 32] = [0x22; 32];
    const IROH: &str = "32f964994affa248ee05d85780291e07535c4229dfcba0bbde4c44f94594c602";

    struct Q {
        hp: String,
        ps: String,
        lan: Option<String>,
        exp: Option<String>,
        extra: String,
        header: String,
    }

    fn q() -> Q {
        Q {
            hp: b64url_encode(&HP),
            ps: b64url_encode(&PS),
            lan: Some("192.168.1.10:7401".into()),
            exp: Some((NOW + 3600).to_string()),
            extra: String::new(),
            header: HEADER.into(),
        }
    }

    impl Q {
        fn text(&self) -> String {
            let mut lines = vec![self.header.clone()];
            if !self.hp.is_empty() {
                lines.push(format!("hp={}", self.hp));
            }
            if !self.ps.is_empty() {
                lines.push(format!("ps={}", self.ps));
            }
            if let Some(l) = &self.lan {
                lines.push(format!("lan={l}"));
            }
            if let Some(e) = &self.exp {
                lines.push(format!("exp={e}"));
            }
            if !self.extra.is_empty() {
                lines.push(self.extra.clone());
            }
            lines.join("\n")
        }
        fn extra(mut self, e: &str) -> Self {
            self.extra = e.into();
            self
        }
    }

    #[test]
    fn a_one_line_link_parses_like_its_lines() {
        let lines = q().text();
        let link = to_link(&lines);
        assert!(link.starts_with(LINK_PREFIX) && !link.contains('\n'));
        let from_link = PairingInfo::parse_at(&format!("  {link}\n"), NOW).unwrap();
        assert_eq!(from_link, PairingInfo::parse_at(&lines, NOW).unwrap());
        // A damaged link is refused as a bad code, not read as something else.
        assert!(PairingInfo::parse_at(&format!("{LINK_PREFIX}!!!"), NOW).is_err());
        assert!(PairingInfo::parse_at(LINK_PREFIX, NOW).is_err());
    }

    fn parse_ok(q: &Q) -> PairingInfo {
        PairingInfo::parse_at(&q.text(), NOW).unwrap()
    }

    fn err(q: &Q) -> PairingError {
        match PairingInfo::parse_at(&q.text(), NOW).unwrap_err() {
            Error::Pairing(e) => e,
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn parse_valid_unpadded() {
        let p = parse_ok(&q());
        assert_eq!(p.host_key, HP);
        assert_eq!(p.pair_secret.expose(), &PS);
        assert_eq!(p.lan, "192.168.1.10:7401".parse().unwrap());
        assert!(p.v6.is_none() && p.tailscale.is_none() && p.relay.is_none());
        assert_eq!(p.expires_at, NOW + 3600);
    }

    #[test]
    fn parse_valid_padded_and_crlf() {
        let mut x = q();
        x.hp.push('=');
        x.ps.push('=');
        assert_eq!(x.hp.len(), 44);
        let text = x.text().replace('\n', "\r\n");
        let p = PairingInfo::parse_at(&text, NOW).unwrap();
        assert_eq!(p.host_key, HP);
        assert_eq!(p.pair_secret.expose(), &PS);
    }

    #[test]
    fn parse_optional_v6_forms() {
        let p = parse_ok(&q().extra("v6=[fe80::1]:7402"));
        assert_eq!(p.v6, Some("[fe80::1]:7402".parse().unwrap()));
        let p = parse_ok(&q().extra("v6=2001:db8::5"));
        assert_eq!(p.v6, Some("[2001:db8::5]:7401".parse().unwrap()));
        assert!(matches!(err(&q().extra("v6=example.com")), PairingError::BadEndpoint("v6", _)));
    }

    #[test]
    fn parse_optional_relay_is_recorded_not_dialed() {
        let p = parse_ok(&q().extra("relay=192.0.2.9:7402"));
        assert_eq!(p.relay, Some(HostPort { host: "192.0.2.9".into(), port: 7402 }));
    }

    #[test]
    fn parse_optional_rdv() {
        let p = parse_ok(&q().extra("rdv=broker.emqx.io:1883;broker.hivemq.com:1883"));
        let hosts: Vec<_> = p.rdv.iter().map(|h| (h.host.as_str(), h.port)).collect();
        assert_eq!(hosts, vec![("broker.emqx.io", 1883), ("broker.hivemq.com", 1883)]);
        assert!(parse_ok(&q()).rdv.is_empty());
    }

    #[test]
    fn parse_optional_tailscale() {
        let p = parse_ok(&q().extra("ts=100.101.102.103:7401"));
        assert_eq!(p.tailscale, Some("100.101.102.103:7401".parse().unwrap()));
        assert!(parse_ok(&q()).tailscale.is_none());
    }

    #[test]
    fn reject_secrets_in_any_line() {
        for e in ["ts=100.101.102.103:7401;local_api_token x", "rdv=broker.emqx.io:1883;bearer x"] {
            assert_eq!(err(&q().extra(e)), PairingError::Secrets("pairing"), "{e}");
        }
    }

    #[test]
    fn reject_garbage_endpoints() {
        for e in [
            "ts=not-an-ip:7401",
            "ts=100.1.1.1:0",
            "ts=100.1.1.1:70000",
            "rdv=not-a-host:port",
            "relay=https://example.com/x",
            "relay=0.0.0.0:7402",
        ] {
            assert!(matches!(err(&q().extra(e)), PairingError::BadEndpoint(..)), "{e}");
        }
        let mut x = q();
        x.lan = Some("example.com:7401".into());
        assert!(matches!(err(&x), PairingError::BadEndpoint("lan", _)));
        x.lan = Some("0.0.0.0:7401".into());
        assert!(matches!(err(&x), PairingError::BadEndpoint("lan", _)));
    }

    #[test]
    fn reject_missing_fields() {
        let mut x = q();
        x.hp.clear();
        assert_eq!(err(&x), PairingError::Missing("hp"));
        let mut x = q();
        x.ps.clear();
        assert_eq!(err(&x), PairingError::Missing("ps"));
        let mut x = q();
        x.exp = None;
        assert_eq!(err(&x), PairingError::Missing("exp"));
        let mut x = q();
        x.lan = None;
        assert_eq!(err(&x), PairingError::Missing("lan"));
    }

    #[test]
    fn expiry_boundary() {
        let mut x = q();
        x.exp = Some((NOW - 1).to_string());
        assert_eq!(err(&x), PairingError::Expired);
        x.exp = Some(NOW.to_string());
        assert_eq!(parse_ok(&x).expires_at, NOW);
        x.exp = Some("soon".into());
        assert_eq!(err(&x), PairingError::BadExpiry);
    }

    #[test]
    fn reject_wrong_header_short_key_duplicates_and_junk_lines() {
        let mut x = q();
        x.header = "remedy-connect/2".into();
        assert_eq!(err(&x), PairingError::NotRemedy);
        let mut x = q();
        x.hp = b64url_encode(&[1u8; 16]);
        assert_eq!(err(&x), PairingError::BadKeyLength("hp"));
        let mut x = q();
        x.hp = "!!!".into();
        assert_eq!(err(&x), PairingError::BadBase64("hp"));
        let dup = format!("hp={}", b64url_encode(&HP));
        assert_eq!(err(&q().extra(&dup)), PairingError::Duplicate);
        assert_eq!(err(&q().extra("rubbish")), PairingError::BadLine);
        // unknown keys are ignored
        parse_ok(&q().extra("future=1"));
    }

    #[test]
    fn b64_rejects_noncanonical_and_bad_length() {
        assert!(b64url_decode("A").is_err());
        assert!(b64url_decode("AB").is_err()); // trailing bits set
        assert_eq!(b64url_decode("AA").unwrap(), vec![0]);
        assert!(b64url_decode("a+b/").is_err());
        let data: Vec<u8> = (0..=255u8).collect();
        assert_eq!(b64url_decode(&b64url_encode(&data)).unwrap(), data);
    }

    #[test]
    fn parse_iroh_fields_recorded() {
        let up = IROH.to_ascii_uppercase();
        let p = parse_ok(&q().extra(&format!("ir={up}\nirr=https://relay.example.net/")));
        assert_eq!(p.iroh_id.as_deref(), Some(IROH));
        assert_eq!(p.iroh_relay.as_deref(), Some("https://relay.example.net/"));
        let p = parse_ok(&q().extra(&format!(
            "ir={IROH}\nira=74.167.84.108:7402,[2a00:1450::1]:7402,192.168.0.46:7402\nirm=n0\nirp=broker.emqx.io:1883;broker.hivemq.com:1883"
        )));
        assert_eq!(p.iroh_addrs.len(), 3);
        assert!(p.iroh_n0);
        assert_eq!(p.iroh_punch.len(), 2);
        let rec = PairingRecord::from_info(&p, "pixel");
        assert!(rec.extras.contains_key("ir") && rec.extras.contains_key("irp"));
    }

    #[test]
    fn reject_bad_iroh_fields() {
        for bad in [
            "ir=abc".to_string(),
            format!("ir={}", "g".repeat(64)),
            "ira=example.com:7402".into(),
            "ira=1.2.3.4".into(),
            "ira=0.0.0.0:7402".into(),
            "ira=1.2.3.4:99999".into(),
            "irm=other".into(),
            "irr=ftp://relay.example.net".into(),
            "irr=https://user:pw@relay.example.net".into(),
            "irr=https://relay.example.net/?token=x".into(),
            "irp=http://evil".into(),
        ] {
            assert!(matches!(err(&q().extra(&bad)), PairingError::BadEndpoint(..)), "{bad}");
        }
    }

    #[test]
    fn reject_oversized_code_and_rdv_list() {
        assert_eq!(
            PairingInfo::parse_at(&"x".repeat(16 * 1024 + 1), NOW).unwrap_err().to_string(),
            "Pairing code is too large."
        );
        let list = (1..=9).map(|i| format!("broker{i}.example:1883")).collect::<Vec<_>>().join(";");
        assert!(matches!(err(&q().extra(&format!("rdv={list}"))), PairingError::BadEndpoint("rdv", _)));
        let many = (0..40).map(|i| format!("k{i}=v")).collect::<Vec<_>>().join("\n");
        assert_eq!(err(&q().extra(&many)), PairingError::TooManyLines);
    }

    #[test]
    fn debug_never_shows_secret() {
        let p = parse_ok(&q());
        let dbg = format!("{p:?}");
        assert!(dbg.contains("redacted"));
        assert!(!dbg.contains("34, 34") && !dbg.contains(&b64url_encode(&PS)));
    }

    #[test]
    fn pin_check() {
        let a = [1u8; 32];
        let mut b = a;
        b[31] ^= 1;
        assert!(check_pin(None, &a).is_ok());
        assert!(check_pin(Some(&a), &a).is_ok());
        assert!(matches!(check_pin(Some(&a), &b), Err(Error::PinMismatch)));
        assert!(Error::PinMismatch.to_string().contains("not the PC"));
    }
}
