//! Loopback HTTP shim: a WebView on the phone talks plain HTTP to
//! `127.0.0.1:<random port>/<secret prefix>/...`; each request is forwarded
//! through the tunnel and the PC's HTTP/1.1 response bytes are streamed back.
//!
//! Auth is a 32-hex-character secret path prefix (or, after a request that
//! carried it, the `grove_shim` cookie / `X-Grove-Shim` header). Anything
//! else gets 403 and never a cookie. Peers that are not loopback are dropped.

use std::collections::HashMap;
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::deny;
use crate::error::{Error, Result};

/// One request the shim hands to the tunnel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ShimRequest {
    pub method: String,
    pub target: String,
    pub headers: String,
    pub body: Vec<u8>,
}

/// Sends a request through the tunnel and writes the response bytes to `out`
/// as they arrive.
pub trait ShimPipe: Send + Sync {
    fn pipe(&self, req: &ShimRequest, out: &mut dyn Write) -> Result<()>;
}

impl<F> ShimPipe for F
where
    F: Fn(&ShimRequest, &mut dyn Write) -> Result<()> + Send + Sync,
{
    fn pipe(&self, req: &ShimRequest, out: &mut dyn Write) -> Result<()> {
        self(req, out)
    }
}

pub mod auth {
    //! Token handling (port of `ShimAuth.kt`).

    use crate::deny::percent_decode;
    use crate::util::ct_eq;

    pub const COOKIE_NAME: &str = "grove_shim";
    pub const HEADER_NAME: &str = "X-Grove-Shim";
    pub const TOKEN_HEX_LEN: usize = 32;

    pub fn new_token() -> crate::error::Result<String> {
        let mut raw = [0u8; TOKEN_HEX_LEN / 2];
        getrandom::fill(&mut raw).map_err(|_| crate::error::Error::Store("no system randomness".into()))?;
        Ok(hex::encode(raw))
    }

    pub fn web_url(port: u16, token: &str) -> String {
        format!("http://127.0.0.1:{port}/{token}/?connect=1")
    }

    pub fn cookie_value(token: &str) -> String {
        format!("{COOKIE_NAME}={token}; HttpOnly; Path=/; SameSite=Strict")
    }

    fn token_shape(t: &str) -> bool {
        t.len() == TOKEN_HEX_LEN && t.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }

    fn token_eq(got: &str, expected: &str) -> bool {
        ct_eq(got.to_ascii_lowercase().as_bytes(), expected.to_ascii_lowercase().as_bytes())
    }

    fn split_target(t: &str) -> (&str, &str) {
        match t.find(['?', '#']) {
            Some(i) => (&t[..i], &t[i..]),
            None => (t, ""),
        }
    }

    pub fn path_has_token(target: &str, token: &str) -> bool {
        if !token_shape(token) {
            return false;
        }
        let (path, _) = split_target(target);
        path.split('/')
            .find(|s| !s.is_empty())
            .map(|first| token_eq(&percent_decode(first), token))
            .unwrap_or(false)
    }

    fn header_has_token(headers: &str, token: &str) -> bool {
        // `get` rather than indexing: a header line from the network may put a
        // multi-byte character across the cut, and slicing there panics
        // before the request is even authenticated.
        headers.lines().any(|l| {
            l.get(..HEADER_NAME.len()).is_some_and(|n| n.eq_ignore_ascii_case(HEADER_NAME))
                && l.get(HEADER_NAME.len()..).is_some_and(|rest| rest.starts_with(':'))
                && token_eq(l[HEADER_NAME.len() + 1..].trim(), token)
        })
    }

    fn cookie_has_token(headers: &str, token: &str) -> bool {
        headers.lines().filter(|l| l.len() > 7 && l.get(..7).is_some_and(|n| n.eq_ignore_ascii_case("cookie:"))).any(|l| {
            l[7..].split(';').any(|kv| {
                kv.trim().split_once('=').is_some_and(|(n, v)| {
                    n.trim().eq_ignore_ascii_case(COOKIE_NAME) && token_eq(v.trim(), token)
                })
            })
        })
    }

    /// True when the request carries the token in its path, header or cookie.
    pub fn allowed(target: &str, headers: &str, token: &str) -> bool {
        token_shape(token)
            && (path_has_token(target, token) || header_has_token(headers, token) || cookie_has_token(headers, token))
    }

    /// Strip the token prefix (and a leftover `shim=` query item) before the pipe.
    pub fn strip(target: &str, token: &str) -> String {
        let (path, suffix) = split_target(target);
        let segs: Vec<&str> = path.split('/').filter(|s| !s.is_empty()).collect();
        let new_path = if !segs.is_empty() && token_shape(token) && token_eq(&percent_decode(segs[0]), token) {
            if segs.len() == 1 {
                "/".to_string()
            } else {
                format!("/{}", segs[1..].join("/"))
            }
        } else if path.is_empty() {
            "/".to_string()
        } else {
            path.to_string()
        };
        format!("{new_path}{}", strip_shim_query(suffix))
    }

    fn strip_shim_query(suffix: &str) -> String {
        if suffix.is_empty() {
            return String::new();
        }
        let (before, hash) = match suffix.find('#') {
            Some(i) => (&suffix[..i], &suffix[i..]),
            None => (suffix, ""),
        };
        let Some(q) = before.strip_prefix('?') else { return suffix.to_string() };
        let kept: Vec<&str> = q.split('&').filter(|p| *p != "shim" && !p.starts_with("shim=")).collect();
        if kept.is_empty() {
            hash.to_string()
        } else {
            format!("?{}{hash}", kept.join("&"))
        }
    }
}

#[derive(Debug, Clone)]
pub struct ShimConfig {
    pub header_deadline: Duration,
    pub body_deadline: Duration,
    /// Simultaneous connections (an SSE stream holds one open).
    pub max_connections: usize,
    /// The PC refuses an assembled request over 256 KiB.
    pub max_body: usize,
}

impl Default for ShimConfig {
    fn default() -> Self {
        ShimConfig {
            header_deadline: Duration::from_secs(10),
            body_deadline: Duration::from_secs(30),
            max_connections: 24,
            max_body: 256 * 1024,
        }
    }
}

const MAX_HEADER_BYTES: usize = 64 * 1024;

struct State {
    stop: AtomicBool,
    conns: Mutex<HashMap<u64, TcpStream>>,
    next: AtomicU64,
    active: AtomicUsize,
}

pub struct LoopbackShim {
    addr: SocketAddr,
    token: String,
    state: Arc<State>,
}

/// Only loopback peers may use the shim.
pub fn peer_allowed(peer: &SocketAddr) -> bool {
    peer.ip().to_canonical().is_loopback()
}

impl LoopbackShim {
    pub fn start(pipe: Arc<dyn ShimPipe>, cfg: ShimConfig) -> Result<LoopbackShim> {
        let token = auth::new_token()?;
        let listener = TcpListener::bind(SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), 0))?;
        let addr = listener.local_addr()?;
        let state = Arc::new(State {
            stop: AtomicBool::new(false),
            conns: Mutex::new(HashMap::new()),
            next: AtomicU64::new(1),
            active: AtomicUsize::new(0),
        });
        let (st, tok) = (state.clone(), token.clone());
        thread::Builder::new().name("remedy-shim-accept".into()).spawn(move || {
            for conn in listener.incoming() {
                if st.stop.load(Ordering::SeqCst) {
                    break;
                }
                let Ok(sock) = conn else { continue };
                let Ok(peer) = sock.peer_addr() else { continue };
                if !peer_allowed(&peer) {
                    let _ = sock.shutdown(Shutdown::Both);
                    continue;
                }
                if st.active.load(Ordering::SeqCst) >= cfg.max_connections {
                    let mut s = sock;
                    let _ = write_response(&mut s, 503, "text/plain; charset=utf-8", b"Too many connections.");
                    continue;
                }
                let _ = sock.set_nodelay(true);
                let id = st.next.fetch_add(1, Ordering::Relaxed);
                if let Ok(clone) = sock.try_clone() {
                    st.conns.lock().unwrap().insert(id, clone);
                }
                st.active.fetch_add(1, Ordering::SeqCst);
                let (st2, pipe2, tok2, cfg2) = (st.clone(), pipe.clone(), tok.clone(), cfg.clone());
                let spawned = thread::Builder::new().name("remedy-shim-conn".into()).spawn(move || {
                    handle(sock, &*pipe2, &tok2, &cfg2);
                    st2.conns.lock().unwrap().remove(&id);
                    st2.active.fetch_sub(1, Ordering::SeqCst);
                });
                if spawned.is_err() {
                    st.conns.lock().unwrap().remove(&id);
                    st.active.fetch_sub(1, Ordering::SeqCst);
                }
            }
        })?;
        Ok(LoopbackShim { addr, token, state })
    }

    pub fn port(&self) -> u16 {
        self.addr.port()
    }

    pub fn token(&self) -> &str {
        &self.token
    }

    /// The only URL a WebView should be pointed at.
    pub fn web_url(&self) -> String {
        auth::web_url(self.port(), &self.token)
    }

    pub fn stop(&self) {
        if self.state.stop.swap(true, Ordering::SeqCst) {
            return;
        }
        let _ = TcpStream::connect_timeout(&self.addr, Duration::from_millis(500)); // wake accept()
        for (_, c) in self.state.conns.lock().unwrap().drain() {
            let _ = c.shutdown(Shutdown::Both);
        }
    }
}

impl Drop for LoopbackShim {
    fn drop(&mut self) {
        self.stop();
    }
}

// ---- one connection --------------------------------------------------------------

enum ReqError {
    BodyTooLarge(u64),
    HeaderTooLarge,
    HeaderTimeout,
    BodyTimeout,
    Bad(&'static str),
    Io,
}

struct Req {
    method: String,
    target: String,
    headers: Vec<String>,
    body: Vec<u8>,
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn timed_read(sock: &mut TcpStream, buf: &mut [u8], deadline: Instant, on_timeout: ReqError) -> std::result::Result<usize, ReqError> {
    let left = deadline.saturating_duration_since(Instant::now());
    if left.is_zero() {
        return Err(on_timeout);
    }
    sock.set_read_timeout(Some(left)).map_err(|_| ReqError::Io)?;
    match sock.read(buf) {
        Ok(n) => Ok(n),
        Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => Err(on_timeout),
        Err(_) => Err(ReqError::Io),
    }
}

fn read_request(sock: &mut TcpStream, cfg: &ShimConfig) -> std::result::Result<Option<Req>, ReqError> {
    // headers under one absolute deadline (trickling cannot extend it)
    let deadline = Instant::now() + cfg.header_deadline;
    let mut buf: Vec<u8> = Vec::with_capacity(1024);
    let mut tmp = [0u8; 4096];
    let (head_end, _) = loop {
        let n = timed_read(sock, &mut tmp, deadline, ReqError::HeaderTimeout)?;
        if n == 0 {
            if buf.is_empty() {
                return Ok(None);
            }
            return Err(ReqError::Bad("Incomplete request headers."));
        }
        buf.extend_from_slice(&tmp[..n]);
        match find(&buf, b"\r\n\r\n") {
            Some(p) if p <= MAX_HEADER_BYTES => break (p, ()),
            Some(_) => return Err(ReqError::HeaderTooLarge),
            None if buf.len() >= MAX_HEADER_BYTES => return Err(ReqError::HeaderTooLarge),
            None => {}
        }
    };
    let head = String::from_utf8_lossy(&buf[..head_end]).into_owned();
    let mut prefix = buf[head_end + 4..].to_vec();
    let mut lines = head.split("\r\n");
    let request_line = lines.next().unwrap_or("");
    let parts: Vec<&str> = request_line.split(' ').collect();
    if parts.len() != 3 || !(parts[2] == "HTTP/1.1" || parts[2] == "HTTP/1.0") {
        return Err(ReqError::Bad("Malformed request line."));
    }
    let (method, target) = (parts[0], parts[1]);
    if method.is_empty() || !method.bytes().all(|b| b.is_ascii_alphabetic()) {
        return Err(ReqError::Bad("Malformed request method."));
    }
    if !target.starts_with('/') || target.starts_with("//") || target.bytes().any(|b| b < 0x21 || b == 0x7f) {
        return Err(ReqError::Bad("Request target must be an origin-form path."));
    }
    let mut headers = Vec::new();
    let mut lengths: Vec<u64> = Vec::new();
    for l in lines.filter(|l| !l.is_empty()) {
        let Some((name, value)) = l.split_once(':') else {
            return Err(ReqError::Bad("Malformed header line."));
        };
        let name = name.trim().to_ascii_lowercase();
        if name == "transfer-encoding" {
            return Err(ReqError::Bad("Transfer-Encoding is not accepted by the phone remote."));
        }
        if name == "content-length" {
            let v = value.trim();
            if v.is_empty() || !v.bytes().all(|b| b.is_ascii_digit()) {
                return Err(ReqError::Bad("Invalid Content-Length."));
            }
            lengths.push(v.parse().map_err(|_| ReqError::Bad("Invalid Content-Length."))?);
        }
        headers.push(l.to_string());
    }
    lengths.dedup();
    if lengths.len() > 1 {
        return Err(ReqError::Bad("Conflicting Content-Length headers."));
    }
    let cl = lengths.first().copied().unwrap_or(0);
    if cl > cfg.max_body as u64 {
        return Err(ReqError::BodyTooLarge(cl));
    }
    let cl = cl as usize;
    // body under one absolute deadline
    let body_deadline = Instant::now() + cfg.body_deadline;
    prefix.truncate(cl);
    let mut body = prefix;
    body.reserve(cl - body.len());
    while body.len() < cl {
        let mut chunk = vec![0u8; (cl - body.len()).min(16 * 1024)];
        let n = timed_read(sock, &mut chunk, body_deadline, ReqError::BodyTimeout)?;
        if n == 0 {
            return Err(ReqError::Bad("Incomplete request body."));
        }
        body.extend_from_slice(&chunk[..n]);
    }
    Ok(Some(Req { method: method.to_string(), target: target.to_string(), headers, body }))
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        408 => "Request Timeout",
        413 => "Payload Too Large",
        431 => "Request Header Fields Too Large",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        _ => "Error",
    }
}

fn write_response(out: &mut dyn Write, status: u16, ctype: &str, body: &[u8]) -> io::Result<()> {
    let head = format!(
        "HTTP/1.1 {status} {}\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        reason(status),
        body.len()
    );
    out.write_all(head.as_bytes())?;
    out.write_all(body)?;
    out.flush()
}

fn text(sock: &mut TcpStream, status: u16, msg: &str) {
    let _ = write_response(sock, status, "text/plain; charset=utf-8", msg.as_bytes());
}

/// Forwards the PC's response to the browser. Holds back only the head, to
/// replace `Connection` and add the cookie; everything after is written and
/// flushed as it arrives.
struct Injector<'a> {
    out: &'a mut TcpStream,
    cookie: String,
    head: Vec<u8>,
    done: bool,
}

impl Write for Injector<'_> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        if self.done {
            self.out.write_all(data)?;
            self.out.flush()?;
            return Ok(data.len());
        }
        self.head.extend_from_slice(data);
        if let Some(p) = find(&self.head, b"\r\n\r\n") {
            let mut out = Vec::with_capacity(p + 128);
            for (i, line) in split_crlf(&self.head[..p]).into_iter().enumerate() {
                if i > 0 && starts_with_ci(line, b"connection:") {
                    continue;
                }
                out.extend_from_slice(line);
                out.extend_from_slice(b"\r\n");
            }
            out.extend_from_slice(b"Connection: close\r\nSet-Cookie: ");
            out.extend_from_slice(self.cookie.as_bytes());
            out.extend_from_slice(b"\r\n\r\n");
            out.extend_from_slice(&self.head[p + 4..]);
            self.done = true;
            self.head = Vec::new();
            self.out.write_all(&out)?;
            self.out.flush()?;
        } else if self.head.len() > MAX_HEADER_BYTES {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "response head too large"));
        }
        Ok(data.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        if self.done {
            self.out.flush()
        } else {
            Ok(())
        }
    }
}

fn split_crlf(b: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut start = 0;
    while let Some(p) = find(&b[start..], b"\r\n") {
        out.push(&b[start..start + p]);
        start += p + 2;
    }
    out.push(&b[start..]);
    out
}

fn starts_with_ci(line: &[u8], prefix: &[u8]) -> bool {
    line.len() >= prefix.len() && line[..prefix.len()].eq_ignore_ascii_case(prefix)
}

fn handle(mut sock: TcpStream, pipe: &dyn ShimPipe, token: &str, cfg: &ShimConfig) {
    let req = match read_request(&mut sock, cfg) {
        Ok(Some(r)) => r,
        Ok(None) | Err(ReqError::Io) => return,
        Err(ReqError::BodyTooLarge(n)) => {
            return text(&mut sock, 413, &format!("Request body is {n} bytes; the phone remote accepts up to {}.", cfg.max_body))
        }
        Err(ReqError::HeaderTooLarge) => return text(&mut sock, 431, "Request headers are too large."),
        Err(ReqError::HeaderTimeout) => return text(&mut sock, 408, "Request headers timed out."),
        Err(ReqError::BodyTimeout) => return text(&mut sock, 408, "Request body timed out."),
        Err(ReqError::Bad(m)) => return text(&mut sock, 400, m),
    };
    let header_text = req.headers.join("\r\n");
    if !auth::allowed(&req.target, &header_text, token) {
        return text(&mut sock, 403, "shim");
    }
    let dest = auth::strip(&req.target, token);
    if deny::is_forbidden(&dest, &req.method) {
        return text(&mut sock, 403, deny::REASON);
    }
    // Hop-level headers and the shim's own credentials never go to the PC.
    let forwarded: Vec<&str> = req
        .headers
        .iter()
        .map(String::as_str)
        .filter(|l| {
            !["host:", "content-length:", "connection:", "cookie:", "x-grove-shim:", "proxy-"]
                .iter()
                .any(|p| starts_with_ci(l.as_bytes(), p.as_bytes()))
        })
        .collect();
    let sreq = ShimRequest { method: req.method, target: dest, headers: forwarded.join("\r\n"), body: req.body };
    let mut inj = Injector { out: &mut sock, cookie: auth::cookie_value(token), head: Vec::new(), done: false };
    let res = pipe.pipe(&sreq, &mut inj);
    let (done, empty) = (inj.done, inj.head.is_empty());
    drop(inj);
    match res {
        Ok(()) if done => {}
        Ok(()) if empty => text(&mut sock, 502, "The PC sent an empty response."),
        Ok(()) => text(&mut sock, 502, "The PC sent an incomplete response."),
        Err(_) if done => {} // mid-stream failure: closing the socket is the signal
        Err(e) => {
            let (status, msg) = match &e {
                Error::Denied(m) => (403, (*m).to_string()),
                Error::TooLarge(m) => (413, (*m).to_string()),
                Error::NotConnected | Error::Closed(_) => (502, "The PC is not connected.".to_string()),
                _ => (502, "Tunnel error.".to_string()),
            };
            text(&mut sock, status, &msg);
        }
    }
    let _ = sock.shutdown(Shutdown::Both);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as StdMutex;

    fn token() -> String {
        "a".repeat(auth::TOKEN_HEX_LEN)
    }

    // ---- ShimAuth tests (port of ShimAuthTest.kt) ----

    #[test]
    fn new_token_is_32_lower_hex() {
        let t = auth::new_token().unwrap();
        assert_eq!(t.len(), 32);
        assert!(t.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)));
        assert_ne!(auth::new_token().unwrap(), t);
    }

    #[test]
    fn web_url_is_a_path_prefix_not_bare_root() {
        let t = token();
        let url = auth::web_url(9001, &t);
        assert_eq!(url, format!("http://127.0.0.1:9001/{t}/?connect=1"));
        assert!(!url.contains("127.0.0.1:9001/?") && !url.contains("shim="));
    }

    #[test]
    fn unauthenticated_document_family_denied() {
        let (t, other) = (token(), "b".repeat(32));
        let short = format!("/{}/?connect=1", &t[..31]);
        let cases: Vec<(String, String)> = vec![
            ("/".into(), "".into()),
            ("/index.html".into(), "".into()),
            ("/INDEX.HTML".into(), "".into()),
            ("/assets/index-abc.js".into(), "".into()),
            ("/assets/index-abc.js.map".into(), "".into()),
            ("/?connect=1".into(), "".into()),
            ("/index.html?connect=1".into(), "".into()),
            ("/api/chat".into(), "".into()),
            ("/api/settings".into(), "".into()),
            ("/api/connect".into(), "".into()),
            ("/api/chat".into(), "Cookie: grove_shim=\r\n".into()),
            ("/api/chat".into(), format!("Cookie: grove_shim={other}\r\n")),
            ("/api/chat".into(), format!("Cookie: other={t}\r\n")),
            ("/api/chat".into(), format!("Cookie: notgrove_shim={t}\r\n")),
            ("/api/chat".into(), format!("Cookie: grove_shim={t}extra\r\n")),
            ("/api/chat".into(), format!("X-Grove-Shim: {other}\r\n")),
            ("/api/chat".into(), "X-Grove-Shim:\r\n".into()),
            (format!("/{other}/?connect=1"), "".into()),
            (short, "".into()),
            (format!("/assets/{t}/index.js"), "".into()),
            ("GET / HTTP/1.1".into(), "".into()),
        ];
        for (target, headers) in cases {
            assert!(!auth::allowed(&target, &headers, &t), "should deny {target} {headers:?}");
        }
    }

    #[test]
    fn token_path_prefix_allows_and_strips() {
        let t = token();
        let compact = format!("/{t}/?connect=1");
        assert!(auth::allowed(&compact, "", &t));
        assert_eq!(auth::strip(&compact, &t), "/?connect=1");
        assert_eq!(auth::strip(&format!("/{t}"), &t), "/");
        assert_eq!(auth::strip(&format!("/{t}/"), &t), "/");
        assert_eq!(auth::strip(&format!("/{t}/index.html"), &t), "/index.html");
        assert_eq!(auth::strip(&format!("/{t}/assets/index-abc.js"), &t), "/assets/index-abc.js");
        assert_eq!(auth::strip(&format!("/{t}/api/chat"), &t), "/api/chat");
        assert_eq!(auth::strip(&format!("/{t}/x?shim=1&a=b#f"), &t), "/x?a=b#f");
        assert_eq!(auth::strip("/api/chat", &t), "/api/chat");
        // percent-encoded prefix still matches
        let enc = format!("/%61{}/x", &t[1..]);
        assert!(auth::allowed(&enc, "", &t));
    }

    #[test]
    fn multibyte_header_lines_are_refused_not_a_panic() {
        let t = token();
        // "X-Grove-Shi" + a 2-byte char puts a char boundary across byte 12,
        // and "cookie" + "é" does the same at byte 7.
        for headers in ["X-Grove-Shi\u{e9}: x\r\n", "cookie\u{e9}: a=b\r\n", "\u{1f600}\u{1f600}\u{1f600}\u{1f600}\r\n"] {
            assert!(!auth::allowed("/assets/a.js", headers, &t));
        }
    }

    #[test]
    fn cookie_and_header_allow_later_fetches() {
        let t = token();
        assert!(auth::allowed("/assets/index-abc.js", &format!("Cookie: grove_shim={t}\r\n"), &t));
        assert!(auth::allowed("/api/chat", &format!("Cookie: grove_shim={t}; theme=dark\r\n"), &t));
        assert!(auth::allowed("/api/settings", &format!("Cookie: theme=dark; grove_shim={t}\r\n"), &t));
        assert!(auth::allowed("/api/connect", &format!("X-Grove-Shim: {t}\r\n"), &t));
        assert!(auth::allowed("/?connect=1", &format!("x-grove-shim: {t}\r\n"), &t));
    }

    #[test]
    fn empty_or_wrong_length_token_never_allows() {
        let t = token();
        assert!(!auth::allowed(&format!("/{t}/?connect=1"), "", ""));
        assert!(!auth::allowed("/", &format!("Cookie: grove_shim={t}\r\n"), "aa"));
        assert!(!auth::allowed("/api/chat", &format!("X-Grove-Shim: {t}\r\n"), &format!("{t}aa")));
        assert!(!auth::allowed(&format!("/{}/", "A".repeat(32)), "", &"A".repeat(32))); // not lower hex
    }

    // ---- LoopbackShim tests (port of LoopbackShimTest.kt) ----

    fn ok_pipe(log: Arc<StdMutex<Vec<ShimRequest>>>) -> Arc<dyn ShimPipe> {
        Arc::new(move |r: &ShimRequest, out: &mut dyn Write| -> Result<()> {
            log.lock().unwrap().push(r.clone());
            out.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok")?;
            Ok(())
        })
    }

    fn exchange(port: u16, request: &[u8]) -> String {
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
        s.write_all(request).unwrap();
        let mut out = Vec::new();
        let _ = s.read_to_end(&mut out);
        String::from_utf8_lossy(&out).into_owned()
    }

    fn has_cookie(res: &str) -> bool {
        res.lines().any(|l| l.to_ascii_lowercase().starts_with("set-cookie:"))
    }

    #[test]
    fn unauthenticated_get_does_not_mint_a_cookie() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let shim = LoopbackShim::start(ok_pipe(log.clone()), ShimConfig::default()).unwrap();
        for line in [
            "GET / HTTP/1.1",
            "HEAD / HTTP/1.1",
            "GET /index.html HTTP/1.1",
            "GET /assets/index-abc.js HTTP/1.1",
            "GET /?connect=1 HTTP/1.1",
            "GET /api/chat HTTP/1.1",
            "GET /api/settings HTTP/1.1",
            "POST /api/chat HTTP/1.1",
            "GET /api/connect HTTP/1.1",
        ] {
            let res = exchange(shim.port(), format!("{line}\r\nHost: 127.0.0.1\r\n\r\n").as_bytes());
            assert!(res.starts_with("HTTP/1.1 403"), "{line}: {res}");
            assert!(!has_cookie(&res), "{line}: {res}");
        }
        assert!(log.lock().unwrap().is_empty(), "pipe must not run for unauthenticated requests");
        shim.stop();
    }

    #[test]
    fn path_prefix_mints_cookie_then_cookie_and_header_work() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let shim = LoopbackShim::start(ok_pipe(log.clone()), ShimConfig::default()).unwrap();
        let (port, t) = (shim.port(), shim.token().to_string());
        assert_eq!(shim.web_url(), format!("http://127.0.0.1:{port}/{t}/?connect=1"));
        assert_eq!(t.len(), 32);

        let authed = exchange(port, format!("GET /{t}/?connect=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n").as_bytes());
        assert!(authed.starts_with("HTTP/1.1 200"), "{authed}");
        assert!(has_cookie(&authed) && authed.contains(&format!("grove_shim={t}")));
        // the PC's Connection header is replaced
        assert!(authed.contains("Connection: close") && !authed.contains("keep-alive"));
        assert_eq!(log.lock().unwrap().last().unwrap().target, "/?connect=1");

        let c = exchange(port, format!("GET /api/chat HTTP/1.1\r\nHost: x\r\nCookie: grove_shim={t}\r\n\r\n").as_bytes());
        assert!(c.starts_with("HTTP/1.1 200"));
        let h = exchange(port, format!("GET /assets/a.js HTTP/1.1\r\nHost: x\r\nX-Grove-Shim: {t}\r\n\r\n").as_bytes());
        assert!(h.starts_with("HTTP/1.1 200"));
        let log = log.lock().unwrap();
        assert_eq!(log[1].target, "/api/chat");
        assert_eq!(log[2].target, "/assets/a.js");
        // the shim's own credentials and hop headers are not forwarded
        for r in log.iter() {
            let low = r.headers.to_ascii_lowercase();
            assert!(!low.contains("grove") && !low.contains("cookie") && !low.contains("host:"), "{:?}", r.headers);
        }
        drop(log);
        shim.stop();
    }

    #[test]
    fn wrong_prefix_and_denied_path() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let shim = LoopbackShim::start(ok_pipe(log.clone()), ShimConfig::default()).unwrap();
        let port = shim.port();
        let wrong = "b".repeat(32);
        let r = exchange(port, format!("GET /{wrong}/api/chat HTTP/1.1\r\n\r\n").as_bytes());
        assert!(r.starts_with("HTTP/1.1 403") && !has_cookie(&r), "{r}");
        let t = shim.token().to_string();
        let r = exchange(port, format!("GET /{t}/api/computer/jobs/next?take=1 HTTP/1.1\r\n\r\n").as_bytes());
        assert!(r.starts_with("HTTP/1.1 403") && r.contains("only for the PC host") && !has_cookie(&r), "{r}");
        let r = exchange(port, format!("TRACE /{t}/x HTTP/1.1\r\n\r\n").as_bytes());
        assert!(r.starts_with("HTTP/1.1 403"), "{r}");
        assert!(log.lock().unwrap().is_empty());
        shim.stop();
    }

    #[test]
    fn rejects_bad_framing() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let shim = LoopbackShim::start(ok_pipe(log), ShimConfig::default()).unwrap();
        let (port, t) = (shim.port(), shim.token().to_string());
        let oversized = format!("GET / HTTP/1.1\r\nX-Fill: {}", "x".repeat(64 * 1024));
        assert!(exchange(port, oversized.as_bytes()).starts_with("HTTP/1.1 431"));
        for (hdr, want) in [
            ("Content-Length: -1", "400"),
            ("Content-Length: 0\r\nContent-Length: 1", "400"),
            ("Transfer-Encoding: chunked", "400"),
            ("Content-Length: 99999999", "413"),
            ("Content-Length: abc", "400"),
        ] {
            let r = exchange(port, format!("POST /{t}/ HTTP/1.1\r\n{hdr}\r\n\r\n").as_bytes());
            assert!(r.starts_with(&format!("HTTP/1.1 {want}")), "{hdr}: {r}");
        }
        assert!(exchange(port, b"GET http://evil.example/ HTTP/1.1\r\n\r\n").starts_with("HTTP/1.1 400"));
        assert!(exchange(port, b"GET //evil/x HTTP/1.1\r\n\r\n").starts_with("HTTP/1.1 400"));
        assert!(exchange(port, b"NONSENSE\r\n\r\n").starts_with("HTTP/1.1 400"));
        shim.stop();
    }

    #[test]
    fn trickled_headers_and_body_cannot_extend_the_deadline() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let cfg = ShimConfig {
            header_deadline: Duration::from_millis(150),
            body_deadline: Duration::from_millis(150),
            ..ShimConfig::default()
        };
        let shim = LoopbackShim::start(ok_pipe(log), cfg).unwrap();
        let (port, t) = (shim.port(), shim.token().to_string());

        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let line = format!("GET /{t}/ HTTP/1.1\r\n");
        let mut w = s.try_clone().unwrap();
        let sender = thread::spawn(move || {
            for b in line.bytes() {
                if w.write_all(&[b]).is_err() {
                    return;
                }
                thread::sleep(Duration::from_millis(30));
            }
        });
        let mut out = Vec::new();
        let _ = s.read_to_end(&mut out);
        sender.join().unwrap();
        assert!(String::from_utf8_lossy(&out).starts_with("HTTP/1.1 408"));

        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        s.write_all(format!("POST /{t}/ HTTP/1.1\r\nContent-Length: 64\r\n\r\n").as_bytes()).unwrap();
        let mut w = s.try_clone().unwrap();
        let sender = thread::spawn(move || {
            for _ in 0..64 {
                if w.write_all(b"x").is_err() {
                    return;
                }
                thread::sleep(Duration::from_millis(30));
            }
        });
        let mut out = Vec::new();
        let _ = s.read_to_end(&mut out);
        sender.join().unwrap();
        assert!(String::from_utf8_lossy(&out).starts_with("HTTP/1.1 408"));
        shim.stop();
    }

    #[test]
    fn body_is_forwarded_and_pipe_errors_map_to_status() {
        let log = Arc::new(StdMutex::new(Vec::new()));
        let l2 = log.clone();
        let pipe: Arc<dyn ShimPipe> = Arc::new(move |r: &ShimRequest, _out: &mut dyn Write| -> Result<()> {
            l2.lock().unwrap().push(r.clone());
            match r.target.as_str() {
                "/big" => Err(Error::TooLarge("too big")),
                "/gone" => Err(Error::NotConnected),
                "/empty" => Ok(()),
                _ => Err(Error::Noise("boom")),
            }
        });
        let shim = LoopbackShim::start(pipe, ShimConfig::default()).unwrap();
        let (port, t) = (shim.port(), shim.token().to_string());
        let r = exchange(port, format!("POST /{t}/api/chat HTTP/1.1\r\nContent-Length: 5\r\nContent-Type: a/b\r\n\r\nhello").as_bytes());
        assert!(r.starts_with("HTTP/1.1 502 "), "{r}");
        {
            let l = log.lock().unwrap();
            assert_eq!(l[0].body, b"hello");
            assert_eq!(l[0].method, "POST");
            assert_eq!(l[0].headers, "Content-Type: a/b");
        }
        assert!(exchange(port, format!("GET /{t}/big HTTP/1.1\r\n\r\n").as_bytes()).starts_with("HTTP/1.1 413"));
        let gone = exchange(port, format!("GET /{t}/gone HTTP/1.1\r\n\r\n").as_bytes());
        assert!(gone.starts_with("HTTP/1.1 502") && gone.contains("not connected"));
        assert!(exchange(port, format!("GET /{t}/empty HTTP/1.1\r\n\r\n").as_bytes()).starts_with("HTTP/1.1 502"));
        shim.stop();
    }

    #[test]
    fn only_loopback_peers_are_allowed() {
        assert!(peer_allowed(&"127.0.0.1:5".parse().unwrap()));
        assert!(peer_allowed(&"127.8.8.8:5".parse().unwrap()));
        assert!(peer_allowed(&"[::1]:5".parse().unwrap()));
        assert!(peer_allowed(&"[::ffff:127.0.0.1]:5".parse().unwrap()));
        assert!(!peer_allowed(&"192.168.1.20:5".parse().unwrap()));
        assert!(!peer_allowed(&"10.0.0.1:5".parse().unwrap()));
        assert!(!peer_allowed(&"[2001:db8::1]:5".parse().unwrap()));
        assert!(!peer_allowed(&"0.0.0.0:5".parse().unwrap()));
        let shim = LoopbackShim::start(ok_pipe(Arc::new(StdMutex::new(Vec::new()))), ShimConfig::default()).unwrap();
        assert!(shim.addr.ip().is_loopback(), "must bind loopback only");
        shim.stop();
    }
}
