//! In-process fake PC: the responder side of RemedyConnect, written from
//! `native/go/connect/{pipe,pair,dispatch,record,frame}.go`, deliberately
//! independent of the crate's own record layer (it drives snow directly).
//!
//! Behaviour mirrored from Go: the second handshake message is sent *before*
//! the payload is checked, a failed pair/hello just closes the socket, records
//! carry an explicit nonce that must equal the receive counter, REKEY resets
//! the counter to 0, PING answers PONG, requests are reassembled per id (256
//! KiB cap) and answered with HTTP/1.1 bytes in RES frames ended by an empty
//! FIN frame.

#![allow(dead_code)]

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use remedy_connect_core::frame::{
    decode_inner, decode_request, encode_inner, fragment, HttpRequest, TYPE_HTTP_REQ, TYPE_HTTP_RES, TYPE_PING,
    TYPE_PONG, TYPE_REKEY,
};
use remedy_connect_core::keys::{device_id_of, DeviceKeys};
use remedy_connect_core::pairing::b64url_encode;
use snow::params::NoiseParams;
use snow::StatelessTransportState;

pub const MAX_PLAIN: usize = 65508;
pub const MAX_FRAG_BYTES: usize = 256 * 1024;

pub type Handler = Arc<dyn Fn(HttpRequest, Out) + Send + Sync>;

struct Inner {
    sock: TcpStream,
    ts: StatelessTransportState,
    send_n: u64,
    sent_since_rekey: u64,
}

/// One authenticated connection, writable from handler threads.
pub struct Conn {
    inner: Mutex<Inner>,
    host_rekey_after: Arc<AtomicU64>,
    rekeys_out: Arc<AtomicUsize>,
}

impl Conn {
    fn write_plain(&self, plaintext: &[u8]) -> bool {
        let mut g = self.inner.lock().unwrap();
        if !Self::write_locked(&mut g, plaintext) {
            return false;
        }
        g.sent_since_rekey += 1;
        let limit = self.host_rekey_after.load(Ordering::SeqCst);
        if limit > 0 && g.sent_since_rekey >= limit {
            let f = encode_inner(TYPE_REKEY, 0, &[], true).unwrap();
            if !Self::write_locked(&mut g, &f) {
                return false;
            }
            g.ts.rekey_outgoing();
            g.send_n = 0;
            g.sent_since_rekey = 0;
            self.rekeys_out.fetch_add(1, Ordering::SeqCst);
        }
        true
    }

    fn write_locked(g: &mut Inner, plaintext: &[u8]) -> bool {
        assert!(plaintext.len() <= MAX_PLAIN);
        let n = g.send_n;
        let mut ct = vec![0u8; plaintext.len() + 16];
        g.ts.write_message(n, plaintext, &mut ct).expect("encrypt");
        let mut rec = Vec::with_capacity(4 + 12 + ct.len());
        rec.extend_from_slice(&((12 + ct.len()) as u32).to_be_bytes());
        rec.extend_from_slice(&[0, 0, 0, 0]);
        rec.extend_from_slice(&n.to_le_bytes());
        rec.extend_from_slice(&ct);
        g.send_n += 1;
        g.sock.write_all(&rec).is_ok()
    }

    pub fn kill(&self) {
        let _ = self.inner.lock().unwrap().sock.shutdown(Shutdown::Both);
    }
}

/// Handle a handler uses to answer one request.
#[derive(Clone)]
pub struct Out {
    conn: Arc<Conn>,
    id: u32,
}

impl Out {
    /// Send response bytes (any HTTP/1.1 bytes) as RES frames, FIN unset.
    pub fn send(&self, bytes: &[u8]) -> bool {
        for f in fragment(TYPE_HTTP_RES, self.id, bytes, MAX_PLAIN, false).unwrap() {
            if !self.conn.write_plain(&f) {
                return false;
            }
        }
        true
    }

    /// The empty FIN frame that ends a response.
    pub fn end(&self) {
        let f = encode_inner(TYPE_HTTP_RES, self.id, &[], true).unwrap();
        self.conn.write_plain(&f);
    }

    /// Abruptly drop the connection (PC crash / network loss).
    pub fn kill(&self) {
        self.conn.kill();
    }
}

pub fn http_response(status: u16, headers: &[(&str, &str)], body: &[u8]) -> Vec<u8> {
    let mut s = format!("HTTP/1.1 {status} OK\r\nContent-Length: {}\r\n", body.len());
    for (k, v) in headers {
        s.push_str(&format!("{k}: {v}\r\n"));
    }
    s.push_str("\r\n");
    let mut out = s.into_bytes();
    out.extend_from_slice(body);
    out
}

pub struct PcInner {
    host_secret: [u8; 32],
    host_pub: [u8; 32],
    pair_secret: Mutex<Option<[u8; 32]>>,
    pub devices: Mutex<HashMap<String, ([u8; 32], String)>>,
    handler: Mutex<Handler>,
    conns: Mutex<Vec<Arc<Conn>>>,
    pub requests: Mutex<Vec<HttpRequest>>,
    pub accepted: AtomicUsize,
    pub handshakes: AtomicUsize,
    pub pings: AtomicUsize,
    /// Stop answering pings while keeping the socket open: a path that died silently.
    pub mute: AtomicBool,
    pub rekeys_in: AtomicUsize,
    pub rekeys_out: Arc<AtomicUsize>,
    pub req_frames: AtomicUsize,
    pub host_rekey_after: Arc<AtomicU64>,
    pub refuse: AtomicBool,
    stop: AtomicBool,
}

pub struct FakePc {
    pub addr: SocketAddr,
    pub inner: Arc<PcInner>,
}

fn echo_handler() -> Handler {
    Arc::new(|req: HttpRequest, out: Out| {
        let hdrs = req.headers.replace("\r\n", "|");
        let r = http_response(
            200,
            &[("Content-Type", "application/octet-stream"), ("X-Method", &req.method), ("X-Target", &req.target), ("X-Req-Headers", &hdrs)],
            &req.body,
        );
        out.send(&r);
        out.end();
    })
}

fn read_prefixed(s: &mut TcpStream) -> Option<Vec<u8>> {
    let mut h = [0u8; 4];
    s.read_exact(&mut h).ok()?;
    let n = u32::from_be_bytes(h) as usize;
    if n == 0 || n > 65536 {
        return None;
    }
    let mut b = vec![0u8; n];
    s.read_exact(&mut b).ok()?;
    Some(b)
}

impl FakePc {
    pub fn start() -> FakePc {
        let mut host_secret = [0u8; 32];
        getrandom::fill(&mut host_secret).unwrap();
        Self::start_with_secret(host_secret)
    }

    pub fn start_with_secret(host_secret: [u8; 32]) -> FakePc {
        let host_pub = *DeviceKeys::from_secret(&host_secret).unwrap().public();
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let mut secret = [0u8; 32];
        getrandom::fill(&mut secret).unwrap();
        let inner = Arc::new(PcInner {
            host_secret,
            host_pub,
            pair_secret: Mutex::new(Some(secret)),
            devices: Mutex::new(HashMap::new()),
            handler: Mutex::new(echo_handler()),
            conns: Mutex::new(Vec::new()),
            requests: Mutex::new(Vec::new()),
            accepted: AtomicUsize::new(0),
            handshakes: AtomicUsize::new(0),
            pings: AtomicUsize::new(0),
            mute: AtomicBool::new(false),
            rekeys_in: AtomicUsize::new(0),
            rekeys_out: Arc::new(AtomicUsize::new(0)),
            req_frames: AtomicUsize::new(0),
            host_rekey_after: Arc::new(AtomicU64::new(0)),
            refuse: AtomicBool::new(false),
            stop: AtomicBool::new(false),
        });
        let i = inner.clone();
        thread::spawn(move || {
            for c in listener.incoming() {
                if i.stop.load(Ordering::SeqCst) {
                    return;
                }
                let Ok(sock) = c else { continue };
                i.accepted.fetch_add(1, Ordering::SeqCst);
                if i.refuse.load(Ordering::SeqCst) {
                    let _ = sock.shutdown(Shutdown::Both);
                    continue;
                }
                let i2 = i.clone();
                thread::spawn(move || serve(i2, sock));
            }
        });
        FakePc { addr, inner }
    }

    pub fn host_public(&self) -> [u8; 32] {
        self.inner.host_pub
    }

    pub fn pair_secret(&self) -> [u8; 32] {
        self.inner.pair_secret.lock().unwrap().expect("secret consumed")
    }

    /// A pairing text as the Go host prints it.
    pub fn pairing_text(&self, exp: u64) -> String {
        self.text_with(&self.host_public(), &self.pair_secret(), exp, "")
    }

    pub fn text_with(&self, hp: &[u8; 32], ps: &[u8; 32], exp: u64, extra: &str) -> String {
        let mut t = format!(
            "remedy-connect/1\nhp={}\nps={}\nlan={}\n{}exp={exp}",
            b64url_encode(hp),
            b64url_encode(ps),
            self.addr,
            if extra.is_empty() { String::new() } else { format!("{extra}\n") }
        );
        t.push('\n');
        t
    }

    pub fn set_handler(&self, h: impl Fn(HttpRequest, Out) + Send + Sync + 'static) {
        *self.inner.handler.lock().unwrap() = Arc::new(h);
    }

    pub fn device_names(&self) -> Vec<String> {
        self.inner.devices.lock().unwrap().values().map(|d| d.1.clone()).collect()
    }

    pub fn revoke_all(&self) {
        self.inner.devices.lock().unwrap().clear();
    }

    /// Cut every live connection.
    pub fn drop_all(&self) {
        for c in self.inner.conns.lock().unwrap().drain(..) {
            c.kill();
        }
    }

    pub fn live(&self) -> usize {
        self.inner.conns.lock().unwrap().len()
    }

    pub fn request_count(&self) -> usize {
        self.inner.requests.lock().unwrap().len()
    }
}

impl Drop for FakePc {
    fn drop(&mut self) {
        self.inner.stop.store(true, Ordering::SeqCst);
        self.drop_all();
        let _ = TcpStream::connect_timeout(&self.addr, Duration::from_millis(200));
    }
}

fn serve(pc: Arc<PcInner>, mut sock: TcpStream) {
    let _ = sock.set_read_timeout(Some(Duration::from_secs(20)));
    let _ = sock.set_nodelay(true);
    let params: NoiseParams = "Noise_IK_25519_ChaChaPoly_BLAKE2s".parse().unwrap();
    let mut hs = snow::Builder::new(params)
        .local_private_key(&pc.host_secret)
        .unwrap()
        .prologue(b"remedy-connect/1")
        .unwrap()
        .build_responder()
        .unwrap();
    let Some(msg0) = read_prefixed(&mut sock) else { return };
    let mut payload = vec![0u8; 65535];
    let Ok(n) = hs.read_message(&msg0, &mut payload) else { return };
    payload.truncate(n);
    let mut out = vec![0u8; 65535];
    let Ok(m) = hs.write_message(&[], &mut out) else { return };
    let mut frame = (m as u32).to_be_bytes().to_vec();
    frame.extend_from_slice(&out[..m]);
    if sock.write_all(&frame).is_err() {
        return;
    }
    pc.handshakes.fetch_add(1, Ordering::SeqCst);
    let remote: [u8; 32] = hs.get_remote_static().unwrap().try_into().unwrap();
    let Ok(ts) = hs.into_stateless_transport_mode() else { return };

    // Go AuthenticatePayload: only now is the payload looked at.
    if !pc.authenticate(&payload, &remote) {
        let _ = sock.shutdown(Shutdown::Both);
        return;
    }
    let conn = Arc::new(Conn {
        inner: Mutex::new(Inner { sock: sock.try_clone().unwrap(), ts, send_n: 0, sent_since_rekey: 0 }),
        host_rekey_after: pc.host_rekey_after.clone(),
        rekeys_out: pc.rekeys_out.clone(),
    });
    pc.conns.lock().unwrap().push(conn.clone());
    let _ = sock.set_read_timeout(Some(Duration::from_secs(180)));
    let mut recv_n = 0u64;
    let mut frags: HashMap<u32, Vec<u8>> = HashMap::new();
    loop {
        let mut h = [0u8; 4];
        if sock.read_exact(&mut h).is_err() {
            break;
        }
        let len = u32::from_be_bytes(h) as usize;
        if !(28..=65536).contains(&len) {
            break;
        }
        let mut body = vec![0u8; len];
        if sock.read_exact(&mut body).is_err() {
            break;
        }
        let nonce = u64::from_le_bytes(body[4..12].try_into().unwrap());
        if body[..4] != [0; 4] || nonce != recv_n {
            break; // Go: "record nonce replay or out of order"
        }
        let mut plain = vec![0u8; len];
        let pt = {
            let g = conn.inner.lock().unwrap();
            match g.ts.read_message(nonce, &body[12..], &mut plain) {
                Ok(n) => n,
                Err(_) => break,
            }
        };
        recv_n += 1;
        let Ok(f) = decode_inner(&plain[..pt]) else { break };
        match f.ty {
            TYPE_REKEY => {
                conn.inner.lock().unwrap().ts.rekey_incoming();
                recv_n = 0;
                pc.rekeys_in.fetch_add(1, Ordering::SeqCst);
            }
            TYPE_PING => {
                pc.pings.fetch_add(1, Ordering::SeqCst);
                if pc.mute.load(Ordering::SeqCst) {
                    continue;
                }
                conn.write_plain(&encode_inner(TYPE_PONG, f.id, &[], true).unwrap());
            }
            TYPE_HTTP_REQ => {
                pc.req_frames.fetch_add(1, Ordering::SeqCst);
                let buf = frags.entry(f.id).or_default();
                if buf.len() + f.payload.len() > MAX_FRAG_BYTES {
                    frags.remove(&f.id);
                    let out = Out { conn: conn.clone(), id: f.id };
                    out.send(&http_response(413, &[], b"{\"error\":\"too_large\"}"));
                    out.end();
                    continue;
                }
                buf.extend_from_slice(&f.payload);
                if !f.fin() {
                    continue;
                }
                let joined = frags.remove(&f.id).unwrap();
                let out = Out { conn: conn.clone(), id: f.id };
                match decode_request(&joined) {
                    Ok(req) => {
                        pc.requests.lock().unwrap().push(req.clone());
                        let handler = pc.handler.lock().unwrap().clone();
                        thread::spawn(move || handler(req, out));
                    }
                    Err(_) => {
                        out.send(&http_response(400, &[], b"{\"error\":\"http\"}"));
                        out.end();
                    }
                }
            }
            _ => {}
        }
    }
    pc.conns.lock().unwrap().retain(|c| !Arc::ptr_eq(c, &conn));
}

impl PcInner {
    fn authenticate(&self, payload: &[u8], remote: &[u8; 32]) -> bool {
        let id = device_id_of(remote);
        let mut devices = self.devices.lock().unwrap();
        if let Some(rest) = payload.strip_prefix(b"pair\0") {
            if rest.len() < 33 || rest[32] != 0 {
                return false;
            }
            let name = String::from_utf8_lossy(&rest[33..]).trim().to_string();
            let mut slot = self.pair_secret.lock().unwrap();
            let ok = slot.as_ref().is_some_and(|s| ct(s, &rest[..32]));
            if ok {
                *slot = None; // one-use
                devices.insert(id, (*remote, if name.is_empty() { "phone".into() } else { name }));
                return true;
            }
            // Go: an allowlisted phone may reconnect after the secret is consumed.
            return devices.get(&id).is_some_and(|d| &d.0 == remote);
        }
        if let Some(rest) = payload.strip_prefix(b"hello\0") {
            let want = String::from_utf8_lossy(rest).trim().to_string();
            return devices.get(&want).is_some_and(|d| &d.0 == remote) && want == id;
        }
        false
    }
}

fn ct(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |x, (p, q)| x | (p ^ q)) == 0
}
