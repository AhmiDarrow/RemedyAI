//! Minimal STUN (RFC 5389) Binding client: learn the public address a NAT
//! gives a UDP socket. Lookup only — STUN servers never carry traffic.
//! Shared by the sidecar (`punch.rs`) and the test probe.

#![allow(dead_code)]

use std::net::{SocketAddr, UdpSocket};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use anyhow::{anyhow, bail, Result};

/// Default lookup-only STUN servers (never relay traffic).
pub const DEFAULT_STUN: &[&str] = &["stun.l.google.com:19302", "stun.cloudflare.com:3478"];

const MAGIC_COOKIE: u32 = 0x2112_A442;

static TXID_COUNTER: AtomicU64 = AtomicU64::new(1);

/// A STUN transaction id from time and a counter (uniqueness, not secrecy).
pub fn new_txid() -> [u8; 12] {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0) as u64;
    let n = TXID_COUNTER.fetch_add(1, Ordering::Relaxed);
    let mut id = [0u8; 12];
    id[..8].copy_from_slice(&nanos.to_be_bytes());
    id[8..].copy_from_slice(&(n as u32).to_be_bytes());
    id
}

/// RFC 5389 Binding Request with no attributes.
pub fn binding_request(txid: &[u8; 12]) -> [u8; 20] {
    let mut m = [0u8; 20];
    m[0..2].copy_from_slice(&0x0001u16.to_be_bytes());
    m[2..4].copy_from_slice(&0u16.to_be_bytes());
    m[4..8].copy_from_slice(&MAGIC_COOKIE.to_be_bytes());
    m[8..20].copy_from_slice(txid);
    m
}

/// The mapped address in a Binding Success response (XOR-MAPPED-ADDRESS,
/// falling back to MAPPED-ADDRESS). Fails closed on anything malformed or
/// on a transaction id that is not ours.
pub fn parse_binding_response(resp: &[u8], txid: &[u8; 12]) -> Result<SocketAddr> {
    if resp.len() < 20 {
        bail!("short STUN message");
    }
    if u16::from_be_bytes([resp[0], resp[1]]) != 0x0101 {
        bail!("not a Binding Success");
    }
    let len = u16::from_be_bytes([resp[2], resp[3]]) as usize;
    if u32::from_be_bytes([resp[4], resp[5], resp[6], resp[7]]) != MAGIC_COOKIE {
        bail!("bad magic cookie");
    }
    if &resp[8..20] != txid {
        bail!("transaction id mismatch");
    }
    if resp.len() < 20 + len {
        bail!("truncated STUN attributes");
    }
    let attrs = &resp[20..20 + len];
    let mut off = 0;
    let mut plain = None;
    while off + 4 <= attrs.len() {
        let typ = u16::from_be_bytes([attrs[off], attrs[off + 1]]);
        let alen = u16::from_be_bytes([attrs[off + 2], attrs[off + 3]]) as usize;
        off += 4;
        if off + alen > attrs.len() {
            bail!("truncated attribute");
        }
        let v = &attrs[off..off + alen];
        off += alen + (4 - alen % 4) % 4;
        match typ {
            0x0020 => return decode_addr(v, true, txid),
            0x0001 => plain = Some(decode_addr(v, false, txid)?),
            _ => {}
        }
    }
    plain.ok_or_else(|| anyhow!("no mapped address"))
}

fn decode_addr(v: &[u8], xor: bool, txid: &[u8; 12]) -> Result<SocketAddr> {
    if v.len() < 4 {
        bail!("short address attribute");
    }
    let family = v[1];
    let mut port = u16::from_be_bytes([v[2], v[3]]);
    if xor {
        port ^= (MAGIC_COOKIE >> 16) as u16;
    }
    let cookie = MAGIC_COOKIE.to_be_bytes();
    match family {
        0x01 if v.len() >= 8 => {
            let mut ip = [v[4], v[5], v[6], v[7]];
            if xor {
                for i in 0..4 {
                    ip[i] ^= cookie[i];
                }
            }
            Ok(SocketAddr::from((ip, port)))
        }
        0x02 if v.len() >= 20 => {
            let mut ip = [0u8; 16];
            ip.copy_from_slice(&v[4..20]);
            if xor {
                let mut key = [0u8; 16];
                key[..4].copy_from_slice(&cookie);
                key[4..].copy_from_slice(txid);
                for i in 0..16 {
                    ip[i] ^= key[i];
                }
            }
            Ok(SocketAddr::from((ip, port)))
        }
        _ => bail!("unknown address family"),
    }
}

/// Ask STUN servers (in order) which public address they see for `sock`.
pub fn stun_mapped(sock: &UdpSocket, servers: &[String]) -> Result<SocketAddr> {
    sock.set_read_timeout(Some(Duration::from_millis(1500)))?;
    let mut last = anyhow!("no STUN server answered");
    for server in servers {
        let Ok(mut addrs) = std::net::ToSocketAddrs::to_socket_addrs(server.as_str()) else {
            continue;
        };
        let Some(dst) = addrs.find(|a| a.is_ipv4()) else { continue };
        for _ in 0..2 {
            let txid = new_txid();
            if let Err(e) = sock.send_to(&binding_request(&txid), dst) {
                last = e.into();
                break;
            }
            let mut buf = [0u8; 576];
            match sock.recv_from(&mut buf) {
                Ok((n, from)) if from == dst => match parse_binding_response(&buf[..n], &txid) {
                    Ok(a) => return Ok(a),
                    Err(e) => last = e,
                },
                Ok(_) => {}
                Err(e) => last = e.into(),
            }
        }
    }
    Err(last)
}


#[cfg(test)]
mod tests {
    use super::*;

    fn success(txid: &[u8; 12], attr_type: u16, value: &[u8]) -> Vec<u8> {
        let mut m = Vec::new();
        m.extend_from_slice(&0x0101u16.to_be_bytes());
        let padded = value.len() + (4 - value.len() % 4) % 4;
        m.extend_from_slice(&((4 + padded) as u16).to_be_bytes());
        m.extend_from_slice(&MAGIC_COOKIE.to_be_bytes());
        m.extend_from_slice(txid);
        m.extend_from_slice(&attr_type.to_be_bytes());
        m.extend_from_slice(&(value.len() as u16).to_be_bytes());
        m.extend_from_slice(value);
        m.resize(20 + 4 + padded, 0);
        m
    }

    #[test]
    fn request_has_header_and_txid() {
        let txid = [7u8; 12];
        let r = binding_request(&txid);
        assert_eq!(&r[0..2], &[0, 1]);
        assert_eq!(&r[2..4], &[0, 0]);
        assert_eq!(&r[4..8], &MAGIC_COOKIE.to_be_bytes());
        assert_eq!(&r[8..], &txid);
        assert_ne!(new_txid(), new_txid());
    }

    #[test]
    fn parses_xor_mapped_ipv4() {
        let txid = [3u8; 12];
        let ip = [74u8, 167, 84, 108];
        let port: u16 = 7403;
        let c = MAGIC_COOKIE.to_be_bytes();
        let xport = port ^ (MAGIC_COOKIE >> 16) as u16;
        let mut v = vec![0, 1];
        v.extend_from_slice(&xport.to_be_bytes());
        for i in 0..4 {
            v.push(ip[i] ^ c[i]);
        }
        let got = parse_binding_response(&success(&txid, 0x0020, &v), &txid).unwrap();
        assert_eq!(got, "74.167.84.108:7403".parse().unwrap());
    }

    #[test]
    fn parses_plain_mapped_and_rejects_bad_txid() {
        let txid = [9u8; 12];
        let v = [0, 1, 0x1c, 0xf3, 203, 0, 113, 7];
        let msg = success(&txid, 0x0001, &v);
        assert_eq!(parse_binding_response(&msg, &txid).unwrap(), "203.0.113.7:7411".parse().unwrap());
        assert!(parse_binding_response(&msg, &[1u8; 12]).is_err());
        assert!(parse_binding_response(&msg[..10], &txid).is_err());
    }
}
