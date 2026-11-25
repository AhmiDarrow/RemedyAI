//! Inner frames and the HTTP request/response mapping.
//!
//! ```text
//! inner frame: u8 version=1 | u8 type | u32be id | u8 flags | u32be len | payload
//! ```
//!
//! `flags` bit 0 is FIN (last fragment of a message). A request travels as
//! `TYPE_HTTP_REQ` frames whose joined payload is the binary request below;
//! the PC answers with `TYPE_HTTP_RES` frames whose payloads, joined, are the
//! bytes of an HTTP/1.1 response (chunked for streams such as SSE), ended by
//! an empty FIN frame. The older binary response layout is also decoded.

use crate::error::{Error, Result};
use crate::noise::MAX_PLAINTEXT;

pub const VERSION: u8 = 1;
pub const HEADER_LEN: usize = 11;
pub const TYPE_HTTP_REQ: u8 = 0x01;
pub const TYPE_HTTP_RES: u8 = 0x02;
pub const TYPE_PING: u8 = 0x10;
pub const TYPE_PONG: u8 = 0x11;
pub const TYPE_REKEY: u8 = 0x20;
pub const FLAG_FIN: u8 = 0x01;

/// The PC refuses an assembled request payload over this size (`MaxFragBytes`).
pub const MAX_REQUEST_PAYLOAD: usize = 256 * 1024;
/// Upper bound for a response collected into memory by `Tunnel::http`.
pub const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InnerFrame {
    pub ty: u8,
    pub id: u32,
    pub flags: u8,
    pub payload: Vec<u8>,
}

impl InnerFrame {
    pub fn fin(&self) -> bool {
        self.flags & FLAG_FIN != 0
    }
}

pub fn encode_inner(ty: u8, id: u32, payload: &[u8], fin: bool) -> Result<Vec<u8>> {
    if payload.len() > MAX_PLAINTEXT {
        return Err(Error::TooLarge("inner payload exceeds the record limit"));
    }
    let mut out = Vec::with_capacity(HEADER_LEN + payload.len());
    out.push(VERSION);
    out.push(ty);
    out.extend_from_slice(&id.to_be_bytes());
    out.push(if fin { FLAG_FIN } else { 0 });
    out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    out.extend_from_slice(payload);
    Ok(out)
}

/// Strict: wrong version, oversize, truncated or trailing bytes all fail.
pub fn decode_inner(raw: &[u8]) -> Result<InnerFrame> {
    if raw.len() < HEADER_LEN {
        return Err(Error::Protocol("short inner frame"));
    }
    if raw[0] != VERSION {
        return Err(Error::Protocol("unknown inner frame version"));
    }
    let len = u32::from_be_bytes(raw[7..11].try_into().expect("4 bytes")) as usize;
    if len > MAX_PLAINTEXT {
        return Err(Error::Protocol("inner payload too large"));
    }
    if HEADER_LEN + len > raw.len() {
        return Err(Error::Protocol("short inner payload"));
    }
    if HEADER_LEN + len != raw.len() {
        return Err(Error::Protocol("trailing inner frame data"));
    }
    Ok(InnerFrame {
        ty: raw[1],
        id: u32::from_be_bytes(raw[2..6].try_into().expect("4 bytes")),
        flags: raw[6],
        payload: raw[HEADER_LEN..].to_vec(),
    })
}

/// Split `payload` into inner frames that each fit one record
/// (`max_plain` bytes including the 11-byte header).
pub fn fragment(ty: u8, id: u32, payload: &[u8], max_plain: usize, fin: bool) -> Result<Vec<Vec<u8>>> {
    let max_plain = if max_plain == 0 { MAX_PLAINTEXT } else { max_plain.min(MAX_PLAINTEXT) };
    let chunk = max_plain.saturating_sub(HEADER_LEN).max(1);
    if payload.len() <= chunk {
        return Ok(vec![encode_inner(ty, id, payload, fin)?]);
    }
    let mut out = Vec::with_capacity(payload.len().div_ceil(chunk));
    let mut off = 0;
    while off < payload.len() {
        let end = (off + chunk).min(payload.len());
        out.push(encode_inner(ty, id, &payload[off..end], fin && end == payload.len())?);
        off = end;
    }
    Ok(out)
}

// ---- request ---------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct HttpRequest {
    pub method: String,
    /// Origin-form target: path and optional query, starting with `/`.
    pub target: String,
    /// Header lines joined with CRLF, no trailing blank line.
    pub headers: String,
    pub body: Vec<u8>,
}

/// `u8 mlen | method | u16 tlen | target | u16 hlen | headers | u32 blen | body`
pub fn encode_request(req: &HttpRequest) -> Result<Vec<u8>> {
    let (m, t, h) = (req.method.as_bytes(), req.target.as_bytes(), req.headers.as_bytes());
    if m.is_empty() || m.len() > 255 || !m.iter().all(|b| b.is_ascii_alphanumeric() || *b == b'-') {
        return Err(Error::Protocol("bad HTTP method"));
    }
    if t.len() > 65535 || h.len() > 65535 {
        return Err(Error::TooLarge("HTTP target or headers too long"));
    }
    if t.iter().any(|b| *b == 0 || *b == b'\r' || *b == b'\n') {
        return Err(Error::Protocol("bad characters in HTTP target"));
    }
    let mut out = Vec::with_capacity(1 + m.len() + 2 + t.len() + 2 + h.len() + 4 + req.body.len());
    out.push(m.len() as u8);
    out.extend_from_slice(m);
    out.extend_from_slice(&(t.len() as u16).to_be_bytes());
    out.extend_from_slice(t);
    out.extend_from_slice(&(h.len() as u16).to_be_bytes());
    out.extend_from_slice(h);
    out.extend_from_slice(&(req.body.len() as u32).to_be_bytes());
    out.extend_from_slice(&req.body);
    Ok(out)
}

struct Cur<'a>(&'a [u8]);

impl<'a> Cur<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        if self.0.len() < n {
            return Err(Error::Protocol("short HTTP message"));
        }
        let (a, b) = self.0.split_at(n);
        self.0 = b;
        Ok(a)
    }
    fn u16(&mut self) -> Result<usize> {
        Ok(u16::from_be_bytes(self.take(2)?.try_into().expect("2 bytes")) as usize)
    }
    fn u32(&mut self) -> Result<usize> {
        Ok(u32::from_be_bytes(self.take(4)?.try_into().expect("4 bytes")) as usize)
    }
}

fn utf8(b: &[u8]) -> Result<String> {
    String::from_utf8(b.to_vec()).map_err(|_| Error::Protocol("HTTP field is not UTF-8"))
}

pub fn decode_request(payload: &[u8]) -> Result<HttpRequest> {
    let mut c = Cur(payload);
    let mlen = c.take(1)?[0] as usize;
    let method = utf8(c.take(mlen)?)?;
    let tlen = c.u16()?;
    let target = utf8(c.take(tlen)?)?;
    let hlen = c.u16()?;
    let headers = utf8(c.take(hlen)?)?;
    let blen = c.u32()?;
    let body = c.take(blen)?.to_vec();
    if !c.0.is_empty() {
        return Err(Error::Protocol("trailing bytes after HTTP request"));
    }
    Ok(HttpRequest { method, target, headers, body })
}

// ---- response --------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpResponse {
    pub status: u16,
    /// Header lines, CRLF terminated, without `Transfer-Encoding` and
    /// `Content-Length` (the body is already de-chunked).
    pub headers: String,
    pub body: Vec<u8>,
}

/// Binary response: `u16 status | u16 hlen | headers | u32 blen | body`.
pub fn encode_response(res: &HttpResponse) -> Result<Vec<u8>> {
    if !(100..=999).contains(&res.status) {
        return Err(Error::Protocol("invalid HTTP status"));
    }
    if res.headers.len() > 65535 {
        return Err(Error::TooLarge("HTTP headers too long"));
    }
    let mut out = Vec::new();
    out.extend_from_slice(&res.status.to_be_bytes());
    out.extend_from_slice(&(res.headers.len() as u16).to_be_bytes());
    out.extend_from_slice(res.headers.as_bytes());
    out.extend_from_slice(&(res.body.len() as u32).to_be_bytes());
    out.extend_from_slice(&res.body);
    Ok(out)
}

/// Accepts the HTTP/1.1 form the PC sends today and the binary form.
pub fn decode_response(payload: &[u8]) -> Result<HttpResponse> {
    if payload.starts_with(b"HTTP/") {
        return decode_http1(payload);
    }
    let mut c = Cur(payload);
    let status = u16::from_be_bytes(c.take(2)?.try_into().expect("2 bytes"));
    let hlen = c.u16()?;
    let headers = utf8(c.take(hlen)?)?;
    let blen = c.u32()?;
    let body = c.take(blen)?.to_vec();
    if !c.0.is_empty() || !(100..=999).contains(&status) {
        return Err(Error::Protocol("bad HTTP response"));
    }
    Ok(HttpResponse { status, headers, body })
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn decode_http1(raw: &[u8]) -> Result<HttpResponse> {
    let sep = find(raw, b"\r\n\r\n").ok_or(Error::Protocol("response head is incomplete"))?;
    let head = String::from_utf8_lossy(&raw[..sep]).into_owned();
    let rest = &raw[sep + 4..];
    let mut lines = head.split("\r\n");
    let status: u16 = lines
        .next()
        .and_then(|l| l.split(' ').nth(1))
        .and_then(|s| s.parse().ok())
        .filter(|s| (100..=999).contains(s))
        .ok_or(Error::Protocol("bad HTTP status line"))?;
    let (mut chunked, mut content_length) = (false, None::<usize>);
    let mut headers = String::new();
    for l in lines.filter(|l| !l.trim().is_empty()) {
        let (name, value) = l.split_once(':').unwrap_or((l, ""));
        let name = name.trim().to_ascii_lowercase();
        match name.as_str() {
            "transfer-encoding" => chunked = value.to_ascii_lowercase().contains("chunked"),
            "content-length" => content_length = value.trim().parse().ok(),
            _ => {
                headers.push_str(l);
                headers.push_str("\r\n");
            }
        }
    }
    let body = if chunked {
        dechunk(rest)?
    } else {
        if let Some(n) = content_length {
            if rest.len() < n {
                return Err(Error::Protocol("response body is truncated"));
            }
            rest[..n].to_vec()
        } else {
            rest.to_vec()
        }
    };
    Ok(HttpResponse { status, headers, body })
}

fn dechunk(mut d: &[u8]) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    loop {
        let nl = find(d, b"\r\n").ok_or(Error::Protocol("chunked body is truncated"))?;
        let line = std::str::from_utf8(&d[..nl]).map_err(|_| Error::Protocol("bad chunk size"))?;
        let size = usize::from_str_radix(line.split(';').next().unwrap_or("").trim(), 16)
            .map_err(|_| Error::Protocol("bad chunk size"))?;
        d = &d[nl + 2..];
        if size == 0 {
            return Ok(out);
        }
        if d.len() < size + 2 || &d[size..size + 2] != b"\r\n" {
            return Err(Error::Protocol("chunked body is truncated"));
        }
        out.extend_from_slice(&d[..size]);
        d = &d[size + 2..];
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture() -> serde_json::Value {
        crate::testutil::fixture("inner_framing.json")
    }

    fn hx(v: &serde_json::Value, k: &str) -> Vec<u8> {
        hex::decode(v[k].as_str().unwrap()).unwrap()
    }

    /// Go inner_framing.json, the Android request frame for id 7.
    #[test]
    fn go_vector_request_frame() {
        let fx = fixture();
        let req = HttpRequest {
            method: "GET".into(),
            target: "/api/sessions?limit=20".into(),
            headers: String::new(),
            body: vec![],
        };
        let payload = encode_request(&req).unwrap();
        assert_eq!(payload, hx(&fx, "http_req_payload"));
        let frames = fragment(TYPE_HTTP_REQ, 7, &payload, MAX_PLAINTEXT, true).unwrap();
        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0], hx(&fx, "android_http_req_id7"));
        assert_eq!(decode_request(&payload).unwrap(), req);
        let f = decode_inner(&frames[0]).unwrap();
        assert!(f.fin() && f.id == 7 && f.ty == TYPE_HTTP_REQ);
    }

    #[test]
    fn go_vector_response_ping_and_fragments() {
        let fx = fixture();
        let res = HttpResponse {
            status: 200,
            headers: "content-type: application/json\r\n".into(),
            body: br#"{"ok":true}"#.to_vec(),
        };
        assert_eq!(encode_response(&res).unwrap(), hx(&fx, "http_res_payload"));
        assert_eq!(decode_response(&hx(&fx, "http_res_payload")).unwrap(), res);
        assert_eq!(encode_inner(TYPE_PING, 1, &[], true).unwrap(), hx(&fx, "ping_id1"));
        let frags = fragment(TYPE_HTTP_RES, 9, &[b'x'; 50], 30, true).unwrap();
        let want: Vec<Vec<u8>> =
            fx["fragment_max_plain_30_payload_50"].as_array().unwrap().iter().map(|s| hex::decode(s.as_str().unwrap()).unwrap()).collect();
        assert_eq!(frags, want);
        assert!(!decode_inner(&frags[0]).unwrap().fin() && decode_inner(&frags[2]).unwrap().fin());
        assert_eq!(fx["max_plaintext"].as_u64().unwrap() as usize, MAX_PLAINTEXT);
        assert_eq!(fx["inner_hdr"].as_u64().unwrap() as usize, HEADER_LEN);
    }

    #[test]
    fn max_fragment_fits_one_record() {
        let payload = vec![7u8; MAX_PLAINTEXT * 3 + 17];
        let frames = fragment(TYPE_HTTP_REQ, 9, &payload, MAX_PLAINTEXT, true).unwrap();
        assert!(frames.len() >= 4);
        let mut total = 0;
        for (i, f) in frames.iter().enumerate() {
            assert!(f.len() <= MAX_PLAINTEXT, "frame {i} is {} bytes", f.len());
            let d = decode_inner(f).unwrap();
            assert_eq!(d.fin(), i == frames.len() - 1);
            total += d.payload.len();
        }
        assert_eq!(total, payload.len());
    }

    #[test]
    fn request_roundtrip_and_strictness() {
        let req = HttpRequest {
            method: "POST".into(),
            target: "/api/chat".into(),
            headers: "Accept: application/json\r\nX-A: b".into(),
            body: vec![1, 2, 3],
        };
        let enc = encode_request(&req).unwrap();
        assert_eq!(decode_request(&enc).unwrap(), req);
        assert!(decode_request(&enc[..enc.len() - 1]).is_err());
        let mut trailing = enc.clone();
        trailing.push(0);
        assert!(decode_request(&trailing).is_err());
        let long = HttpRequest { method: "GET".into(), target: "/".repeat(65536), ..Default::default() };
        assert!(matches!(encode_request(&long), Err(Error::TooLarge(_))));
        let bad = HttpRequest { method: "GET /x".into(), target: "/".into(), ..Default::default() };
        assert!(encode_request(&bad).is_err());
        let crlf = HttpRequest { method: "GET".into(), target: "/a\r\nHost: x".into(), ..Default::default() };
        assert!(encode_request(&crlf).is_err());
    }

    #[test]
    fn inner_frames_reject_truncation_trailing_and_overflow() {
        let f = encode_inner(TYPE_PING, 1, &[1], true).unwrap();
        assert!(decode_inner(&f[..f.len() - 1]).is_err());
        let mut t = f.clone();
        t.push(0);
        assert!(decode_inner(&t).is_err());
        let mut v = f.clone();
        v[0] = 2;
        assert!(decode_inner(&v).is_err());
        let mut big = f.clone();
        big[7..11].copy_from_slice(&((MAX_PLAINTEXT as u32) + 1).to_be_bytes());
        assert!(decode_inner(&big).is_err());
        assert!(encode_inner(TYPE_PING, 1, &vec![0; MAX_PLAINTEXT + 1], true).is_err());
        assert!(decode_inner(&[1, 2]).is_err());
    }

    #[test]
    fn http1_responses() {
        let plain = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok";
        let r = decode_response(plain).unwrap();
        assert_eq!((r.status, r.body.as_slice()), (200, b"ok".as_slice()));
        assert_eq!(r.headers, "Content-Type: text/plain\r\n");
        let chunked = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-K: v\r\n\r\n5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\n\r\n";
        let r = decode_response(chunked).unwrap();
        assert_eq!(r.body, b"hello world");
        assert_eq!(r.headers, "X-K: v\r\n");
        assert!(decode_response(b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort").is_err());
        assert!(decode_response(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel").is_err());
        assert!(decode_response(b"HTTP/1.1 200 OK\r\n").is_err());
        assert!(decode_response(b"HTTP/1.1 abc OK\r\n\r\n").is_err());
        assert!(decode_response(&[0, 200]).is_err());
    }
}
