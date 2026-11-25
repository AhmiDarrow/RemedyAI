//! The tunnel to the PC: dial, handshake, multiplex HTTP, keep alive, and a
//! supervisor that reconnects with backoff.
//!
//! Only plain TCP to an IP literal is dialed (Tailscale, LAN, IPv6). Relay,
//! rendezvous and iroh endpoints from a pairing text are never contacted.

use std::collections::{HashMap, VecDeque};
use std::fmt;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use zeroize::Zeroizing;

use crate::clock::{Clock, SystemClock};
use crate::deny;
use crate::error::{Error, Result};
use crate::frame::{
    self, decode_inner, decode_response, encode_inner, encode_request, fragment, HttpRequest, HttpResponse,
    MAX_REQUEST_PAYLOAD, MAX_RESPONSE_BYTES, TYPE_HTTP_REQ, TYPE_HTTP_RES, TYPE_PING, TYPE_PONG, TYPE_REKEY,
};
use crate::keys::{load_or_create_device_keys, DeviceKeys, KeyStore};
use crate::noise::{initiator_handshake, read_record, RekeyPolicy, Transport, MAX_PLAINTEXT};
use crate::pairing::{check_pin, PairSecret, PairingInfo, PairingRecord};
use crate::shim::{ShimPipe, ShimRequest};

/// Unread response bytes one request may have queued before it is failed.
const MAX_BUFFERED_PER_STREAM: usize = 16 * 1024 * 1024;
/// The PC caps a device label at 80 bytes.
const MAX_DEVICE_NAME: usize = 80;

/// How a session reached the PC.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Via {
    Tailscale,
    Lan,
    V6,
    /// Straight to the PC's own endpoint, or through the owner's relay.
    Direct,
}

impl Via {
    pub fn as_str(&self) -> &'static str {
        match self {
            Via::Tailscale => "tailscale",
            Via::Lan => "lan",
            Via::V6 => "v6",
            Via::Direct => "direct",
        }
    }
}

impl fmt::Display for Via {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Clone)]
pub struct ClientConfig {
    /// TCP connect timeout for the Tailscale endpoint.
    pub tailscale_connect_timeout: Duration,
    /// TCP connect timeout for LAN and IPv6 endpoints.
    pub local_connect_timeout: Duration,
    /// How long the direct path may take to reach the PC's endpoint.
    pub direct_connect_timeout: Duration,
    /// Called when the PC reports a new direct address, with the updated
    /// pairing, so the app can save it. `None`: the update lives only until
    /// the client stops.
    pub on_record: Option<Arc<dyn Fn(&PairingRecord) + Send + Sync>>,
    /// Read timeout during the handshake and the first PING/PONG.
    pub handshake_timeout: Duration,
    /// Write timeout on the socket; a stuck write fails the tunnel.
    pub write_timeout: Duration,
    /// Send a PING this often (the PC drops a socket silent for 180 s).
    /// `Duration::ZERO` turns the keepalive off.
    pub keepalive: Duration,
    /// Close the tunnel if nothing at all arrives for this long.
    pub idle_timeout: Duration,
    /// Default deadline for `Tunnel::http`.
    pub request_timeout: Duration,
    pub rekey: RekeyPolicy,
    /// Reconnect backoff: first wait and ceiling (doubling).
    pub backoff_min: Duration,
    pub backoff_max: Duration,
    pub clock: Arc<dyn Clock>,
}

impl Default for ClientConfig {
    fn default() -> Self {
        ClientConfig {
            tailscale_connect_timeout: Duration::from_millis(8_000),
            local_connect_timeout: Duration::from_millis(1_500),
            direct_connect_timeout: Duration::from_millis(10_000),
            on_record: None,
            handshake_timeout: Duration::from_millis(8_000),
            write_timeout: Duration::from_secs(30),
            keepalive: Duration::from_secs(30),
            idle_timeout: Duration::from_secs(100),
            request_timeout: Duration::from_secs(30),
            rekey: RekeyPolicy::default(),
            backoff_min: Duration::from_secs(2),
            backoff_max: Duration::from_secs(30),
            clock: Arc::new(SystemClock::new()),
        }
    }
}

// ---- response streaming ------------------------------------------------------

struct StreamInner {
    chunks: VecDeque<Vec<u8>>,
    buffered: usize,
    done: bool,
    error: Option<String>,
    abandoned: bool,
}

struct StreamState {
    m: Mutex<StreamInner>,
    cv: Condvar,
}

impl StreamState {
    fn new() -> Arc<Self> {
        Arc::new(StreamState {
            m: Mutex::new(StreamInner {
                chunks: VecDeque::new(),
                buffered: 0,
                done: false,
                error: None,
                abandoned: false,
            }),
            cv: Condvar::new(),
        })
    }

    fn push(&self, payload: Vec<u8>, fin: bool) {
        let mut g = self.m.lock().unwrap();
        if g.abandoned || g.done {
            return;
        }
        if !payload.is_empty() {
            if g.buffered + payload.len() > MAX_BUFFERED_PER_STREAM {
                g.error = Some("response is arriving faster than it is read".into());
                g.done = true;
            } else {
                g.buffered += payload.len();
                g.chunks.push_back(payload);
            }
        }
        if fin {
            g.done = true;
        }
        self.cv.notify_all();
    }

    fn fail(&self, why: &str) {
        let mut g = self.m.lock().unwrap();
        if !g.done {
            g.error = Some(why.to_string());
            g.done = true;
        }
        self.cv.notify_all();
    }
}

/// The raw HTTP/1.1 bytes of one response, delivered as they arrive: a read
/// returns data as soon as one inner frame has come in, so a server-sent
/// event stream is passed on chunk by chunk. Dropping it abandons the request.
pub struct ResponseStream {
    state: Arc<StreamState>,
    shared: Arc<Shared>,
    id: u32,
    cur: Vec<u8>,
    pos: usize,
    timeout: Option<Duration>,
}

impl ResponseStream {
    /// Longest a single `read` may block. `None` waits as long as the tunnel lives.
    pub fn set_read_timeout(&mut self, t: Option<Duration>) {
        self.timeout = t;
    }
}

impl Read for ResponseStream {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() {
            return Ok(0);
        }
        if self.pos >= self.cur.len() {
            let deadline = self.timeout.map(|t| Instant::now() + t);
            let mut g = self.state.m.lock().unwrap();
            loop {
                if let Some(c) = g.chunks.pop_front() {
                    g.buffered -= c.len();
                    self.cur = c;
                    self.pos = 0;
                    break;
                }
                if g.done {
                    return match &g.error {
                        Some(e) => Err(io::Error::new(io::ErrorKind::ConnectionAborted, e.clone())),
                        None => Ok(0),
                    };
                }
                match deadline {
                    None => g = self.state.cv.wait(g).unwrap(),
                    Some(d) => {
                        let left = d.saturating_duration_since(Instant::now());
                        if left.is_zero() {
                            return Err(io::Error::new(io::ErrorKind::TimedOut, "no response data in time"));
                        }
                        g = self.state.cv.wait_timeout(g, left).unwrap().0;
                    }
                }
            }
        }
        let n = buf.len().min(self.cur.len() - self.pos);
        buf[..n].copy_from_slice(&self.cur[self.pos..self.pos + n]);
        self.pos += n;
        Ok(n)
    }
}

impl Drop for ResponseStream {
    fn drop(&mut self) {
        self.state.m.lock().unwrap().abandoned = true;
        self.shared.streams.lock().unwrap().remove(&self.id);
    }
}

// ---- tunnel --------------------------------------------------------------------

struct PingState {
    got: Mutex<bool>,
    cv: Condvar,
}

struct Shared {
    transport: Transport,
    writer: Mutex<TcpStream>,
    sock: TcpStream,
    closed: AtomicBool,
    reason: Mutex<Option<String>>,
    closed_cv: Condvar,
    next_id: AtomicU32,
    streams: Mutex<HashMap<u32, Arc<StreamState>>>,
    pings: Mutex<HashMap<u32, Arc<PingState>>>,
}

impl Shared {
    fn alloc_id(&self) -> u32 {
        loop {
            let id = self.next_id.fetch_add(1, Ordering::Relaxed);
            if id != 0 {
                return id;
            }
        }
    }

    fn closed_reason(&self) -> String {
        self.reason.lock().unwrap().clone().unwrap_or_else(|| "closed".into())
    }

    fn fail(&self, why: &str) {
        if self.closed.swap(true, Ordering::SeqCst) {
            return;
        }
        let mut r = self.reason.lock().unwrap();
        *r = Some(why.to_string());
        let _ = self.sock.shutdown(std::net::Shutdown::Both);
        for (_, s) in self.streams.lock().unwrap().drain() {
            s.fail(why);
        }
        for (_, p) in self.pings.lock().unwrap().drain() {
            let _g = p.got.lock().unwrap(); // pairs with the waiter's predicate check
            p.cv.notify_all();
        }
        self.closed_cv.notify_all();
        drop(r);
    }

    /// Encrypt and write one inner frame; emits a REKEY first when due.
    fn send_plain(&self, plaintext: &[u8]) -> Result<()> {
        if self.closed.load(Ordering::SeqCst) {
            return Err(Error::Closed(self.closed_reason()));
        }
        let mut w = self.writer.lock().unwrap();
        let res = (|| -> Result<()> {
            if self.transport.rekey_due() {
                let rekey = encode_inner(TYPE_REKEY, 0, &[], true)?;
                w.write_all(&self.transport.seal(&rekey)?)?;
                self.transport.rekey_send();
            }
            w.write_all(&self.transport.seal(plaintext)?)?;
            Ok(())
        })();
        drop(w);
        if let Err(e) = &res {
            self.fail(&format!("write failed: {e}"));
        }
        res
    }

    fn handle_frame(&self, f: frame::InnerFrame) -> Result<()> {
        match f.ty {
            TYPE_PING => {
                let pong = encode_inner(TYPE_PONG, f.id, &[], true)?;
                self.send_plain(&pong)?;
            }
            TYPE_PONG => {
                if let Some(p) = self.pings.lock().unwrap().get(&f.id) {
                    *p.got.lock().unwrap() = true;
                    p.cv.notify_all();
                }
            }
            TYPE_REKEY => self.transport.rekey_recv(),
            TYPE_HTTP_RES => {
                let fin = f.fin();
                let s = {
                    let mut m = self.streams.lock().unwrap();
                    if fin {
                        m.remove(&f.id)
                    } else {
                        m.get(&f.id).cloned()
                    }
                };
                // Unknown id: a response to a request this phone gave up on.
                if let Some(s) = s {
                    s.push(f.payload, fin);
                }
            }
            _ => {} // the PC ignores unknown types too
        }
        Ok(())
    }
}

fn reader_loop(shared: Arc<Shared>, mut sock: TcpStream) {
    loop {
        let body = match read_record(&mut sock) {
            Ok(b) => b,
            Err(Error::Io(e)) if e.kind() == io::ErrorKind::UnexpectedEof => {
                return shared.fail("the PC closed the connection")
            }
            Err(Error::Io(e)) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {
                return shared.fail("the PC went silent")
            }
            Err(e) => return shared.fail(&format!("read failed: {e}")),
        };
        let plain = match shared.transport.open(&body) {
            Ok(p) => p,
            Err(e) => return shared.fail(&format!("{e}")),
        };
        let frame = match decode_inner(&plain) {
            Ok(f) => f,
            Err(e) => return shared.fail(&format!("{e}")),
        };
        if let Err(e) = shared.handle_frame(frame) {
            return shared.fail(&format!("{e}"));
        }
    }
}

fn keepalive_loop(shared: Arc<Shared>, every: Duration) {
    let mut g = shared.reason.lock().unwrap();
    loop {
        if shared.closed.load(Ordering::SeqCst) {
            return;
        }
        let (ng, timeout) = shared.closed_cv.wait_timeout(g, every).unwrap();
        g = ng;
        if shared.closed.load(Ordering::SeqCst) {
            return;
        }
        if timeout.timed_out() {
            drop(g);
            let id = shared.alloc_id();
            if let Ok(f) = encode_inner(TYPE_PING, id, &[], true) {
                let _ = shared.send_plain(&f);
            }
            g = shared.reason.lock().unwrap();
        }
    }
}

/// An established, authenticated session.
pub struct Tunnel {
    shared: Arc<Shared>,
    via: Via,
    peer: SocketAddr,
    request_timeout: Duration,
}

impl fmt::Debug for Tunnel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Tunnel(via={}, peer={}, closed={})", self.via, self.peer, self.is_closed())
    }
}

impl Tunnel {
    fn start(sock: TcpStream, transport: Transport, via: Via, peer: SocketAddr, cfg: &ClientConfig) -> Result<Tunnel> {
        sock.set_read_timeout(Some(cfg.idle_timeout))?;
        let shared = Arc::new(Shared {
            transport,
            writer: Mutex::new(sock.try_clone()?),
            sock: sock.try_clone()?,
            closed: AtomicBool::new(false),
            reason: Mutex::new(None),
            closed_cv: Condvar::new(),
            next_id: AtomicU32::new(1),
            streams: Mutex::new(HashMap::new()),
            pings: Mutex::new(HashMap::new()),
        });
        let s = shared.clone();
        thread::Builder::new().name("remedy-connect-recv".into()).spawn(move || reader_loop(s, sock))?;
        if !cfg.keepalive.is_zero() {
            let s = shared.clone();
            let every = cfg.keepalive;
            thread::Builder::new().name("remedy-connect-keepalive".into()).spawn(move || keepalive_loop(s, every))?;
        }
        Ok(Tunnel { shared, via, peer, request_timeout: cfg.request_timeout })
    }

    pub fn via(&self) -> Via {
        self.via
    }

    pub fn peer(&self) -> SocketAddr {
        self.peer
    }

    pub fn is_closed(&self) -> bool {
        self.shared.closed.load(Ordering::SeqCst)
    }

    /// Why the tunnel closed, if it has.
    pub fn close_reason(&self) -> Option<String> {
        self.is_closed().then(|| self.shared.closed_reason())
    }

    pub fn close(&self) {
        self.shared.fail("closed by this phone");
    }

    /// Block until the tunnel closes or `timeout` passes; true if closed.
    pub fn wait_closed(&self, timeout: Duration) -> bool {
        let g = self.shared.reason.lock().unwrap();
        let (_g, _) = self
            .shared
            .closed_cv
            .wait_timeout_while(g, timeout, |_| !self.shared.closed.load(Ordering::SeqCst))
            .unwrap();
        self.is_closed()
    }

    /// PING and wait for the matching PONG.
    pub fn ping(&self, timeout: Duration) -> Result<()> {
        let id = self.shared.alloc_id();
        let st = Arc::new(PingState { got: Mutex::new(false), cv: Condvar::new() });
        self.shared.pings.lock().unwrap().insert(id, st.clone());
        let res = (|| {
            self.shared.send_plain(&encode_inner(TYPE_PING, id, &[], true)?)?;
            let g = st.got.lock().unwrap();
            let (g, _) = st
                .cv
                .wait_timeout_while(g, timeout, |got| !*got && !self.shared.closed.load(Ordering::SeqCst))
                .unwrap();
            if *g {
                Ok(())
            } else if self.is_closed() {
                Err(Error::Closed(self.shared.closed_reason()))
            } else {
                Err(Error::Timeout("the PC did not answer a ping"))
            }
        })();
        self.shared.pings.lock().unwrap().remove(&id);
        res
    }

    /// Send a request and return the response as a byte stream. The phone-side
    /// deny list is applied first.
    pub fn request(&self, req: &HttpRequest) -> Result<ResponseStream> {
        if self.is_closed() {
            return Err(Error::Closed(self.shared.closed_reason()));
        }
        if deny::is_forbidden(&req.target, &req.method) {
            return Err(Error::Denied(deny::REASON));
        }
        let payload = encode_request(req)?;
        if payload.len() > MAX_REQUEST_PAYLOAD {
            return Err(Error::TooLarge("request is larger than the PC accepts (256 KiB)"));
        }
        let frames = fragment(TYPE_HTTP_REQ, 0, &payload, MAX_PLAINTEXT, true)?;
        let id = self.shared.alloc_id();
        let state = StreamState::new();
        self.shared.streams.lock().unwrap().insert(id, state.clone());
        let stream = ResponseStream {
            state,
            shared: self.shared.clone(),
            id,
            cur: Vec::new(),
            pos: 0,
            timeout: None,
        };
        for mut f in frames {
            f[2..6].copy_from_slice(&id.to_be_bytes()); // fragment() built them with id 0
            self.shared.send_plain(&f)?; // on error the stream drops and unregisters
        }
        Ok(stream)
    }

    /// Whole-response convenience: waits for the end and parses it.
    pub fn http(
        &self,
        method: &str,
        target: &str,
        headers: &str,
        body: &[u8],
        timeout: Option<Duration>,
    ) -> Result<HttpResponse> {
        let deadline = Instant::now() + timeout.unwrap_or(self.request_timeout);
        let mut rs = self.request(&HttpRequest {
            method: method.to_string(),
            target: target.to_string(),
            headers: headers.to_string(),
            body: body.to_vec(),
        })?;
        let mut raw = Vec::new();
        let mut buf = vec![0u8; 32 * 1024];
        loop {
            let left = deadline.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Err(Error::Timeout("request timed out"));
            }
            rs.set_read_timeout(Some(left));
            match rs.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    if raw.len() + n > MAX_RESPONSE_BYTES {
                        return Err(Error::TooLarge("response is larger than 16 MiB"));
                    }
                    raw.extend_from_slice(&buf[..n]);
                }
                Err(e) if e.kind() == io::ErrorKind::TimedOut => return Err(Error::Timeout("request timed out")),
                Err(e) => return Err(Error::Closed(e.to_string())),
            }
        }
        decode_response(&raw)
    }
}

impl Drop for Tunnel {
    fn drop(&mut self) {
        self.close();
    }
}

impl ShimPipe for Tunnel {
    fn pipe(&self, req: &ShimRequest, out: &mut dyn Write) -> Result<()> {
        let mut rs = self.request(&HttpRequest {
            method: req.method.clone(),
            target: req.target.clone(),
            headers: req.headers.clone(),
            body: req.body.clone(),
        })?;
        let mut buf = vec![0u8; 64 * 1024];
        loop {
            match rs.read(&mut buf) {
                Ok(0) => return Ok(()),
                Ok(n) => {
                    out.write_all(&buf[..n])?;
                    out.flush()?;
                }
                Err(e) => return Err(Error::Closed(e.to_string())),
            }
        }
    }
}

// ---- dialing ---------------------------------------------------------------------

/// `pair\0<secret>\0<device name>`; the name is cut to 80 bytes on a
/// character boundary, NULs removed.
pub(crate) fn pair_payload(secret: &PairSecret, name: &str) -> Zeroizing<Vec<u8>> {
    let mut label: String = name.chars().filter(|c| *c != '\0').collect::<String>().trim().to_string();
    if label.is_empty() {
        label = "phone".into();
    }
    while label.len() > MAX_DEVICE_NAME {
        label.pop();
    }
    let mut p = Zeroizing::new(Vec::with_capacity(5 + 32 + 1 + label.len()));
    p.extend_from_slice(b"pair\0");
    p.extend_from_slice(secret.expose());
    p.push(0);
    p.extend_from_slice(label.as_bytes());
    p
}

/// `hello\0<device id hex>`
pub(crate) fn hello_payload(device_id: &str) -> Vec<u8> {
    let mut p = b"hello\0".to_vec();
    p.extend_from_slice(device_id.as_bytes());
    p
}

/// Dial order for the supported transports: Tailscale, LAN, IPv6.
pub fn endpoint_order(
    tailscale: Option<SocketAddr>,
    lan: SocketAddr,
    v6: Option<SocketAddr>,
) -> Vec<(Via, SocketAddr)> {
    let mut v = Vec::new();
    if let Some(t) = tailscale {
        v.push((Via::Tailscale, t));
    }
    v.push((Via::Lan, lan));
    if let Some(a) = v6 {
        v.push((Via::V6, a));
    }
    v
}

fn dial(
    cfg: &ClientConfig,
    via: Via,
    addr: SocketAddr,
    device: &DeviceKeys,
    host_key: &[u8; 32],
    payload: &[u8],
) -> Result<Tunnel> {
    let connect_timeout = match via {
        Via::Tailscale => cfg.tailscale_connect_timeout,
        _ => cfg.local_connect_timeout,
    };
    let sock = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
        if e.kind() == io::ErrorKind::TimedOut {
            Error::Timeout("connect timed out")
        } else {
            Error::Io(e)
        }
    })?;
    sock.set_nodelay(true)?;
    sock.set_read_timeout(Some(cfg.handshake_timeout))?;
    sock.set_write_timeout(Some(cfg.write_timeout))?;
    let mut io = &sock;
    let transport = initiator_handshake(&mut io, device, host_key, payload, cfg.rekey, cfg.clock.clone())
        .map_err(|e| match e {
            Error::Io(ioe) if matches!(ioe.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => {
                Error::Timeout("handshake timed out")
            }
            other => other,
        })?;
    let tunnel = Tunnel::start(sock, transport, via, addr, cfg)?;
    // The PC answers the handshake before it checks the payload. Only a
    // PONG proves the pair secret / device id was accepted.
    match tunnel.ping(cfg.handshake_timeout) {
        Ok(()) => Ok(tunnel),
        Err(Error::Closed(_)) => Err(Error::Rejected),
        Err(e) => Err(e),
    }
}

fn dial_any(
    cfg: &ClientConfig,
    endpoints: &[(Via, SocketAddr)],
    device: &DeviceKeys,
    host_key: &[u8; 32],
    payload: &[u8],
) -> Result<Tunnel> {
    let mut errors = Vec::new();
    for (via, addr) in endpoints {
        match dial(cfg, *via, *addr, device, host_key, payload) {
            Ok(t) => return Ok(t),
            Err(e) => errors.push(format!("{via}: {e}")),
        }
    }
    Err(Error::Unreachable(errors.join("; ")))
}

/// The TCP endpoints first (home network, Tailscale), then the direct path
/// when the PC has one. Away from home the TCP attempts fail fast and the
/// direct one carries the session.
fn dial_all(
    cfg: &ClientConfig,
    endpoints: &[(Via, SocketAddr)],
    direct: Option<crate::direct::Target>,
    device: &DeviceKeys,
    host_key: &[u8; 32],
    payload: &[u8],
) -> Result<Tunnel> {
    let tcp = match dial_any(cfg, endpoints, device, host_key, payload) {
        Ok(t) => return Ok(t),
        Err(e) => e,
    };
    let Some(target) = direct else { return Err(tcp) };
    // A wrong pairing is wrong on every path: do not hide it behind a second dial.
    if matches!(tcp, Error::Rejected | Error::PinMismatch) {
        return Err(tcp);
    }
    match device
        .direct_seed(host_key)
        .and_then(|seed| crate::direct::bridge(&target, &seed, cfg.direct_connect_timeout))
        .and_then(|local| dial(cfg, Via::Direct, local, device, host_key, payload))
    {
        Ok(t) => Ok(t),
        Err(Error::Rejected) => Err(Error::Rejected),
        Err(e) => Err(Error::Unreachable(format!("{tcp}; direct: {e}"))),
    }
}

impl Tunnel {
    /// First contact after scanning a pairing text.
    pub fn connect_pair(
        info: &PairingInfo,
        device: &DeviceKeys,
        device_name: &str,
        cfg: &ClientConfig,
    ) -> Result<Tunnel> {
        let payload = pair_payload(&info.pair_secret, device_name);
        let eps = endpoint_order(info.tailscale, info.lan, info.v6);
        let direct = crate::direct::Target::from_fields(
            info.iroh_id.as_deref(),
            &info.iroh_addrs.join(","),
            info.iroh_relay.as_deref(),
        );
        dial_all(cfg, &eps, direct, device, &info.host_key, &payload)
    }

    /// Reconnect of an already paired phone (`hello`).
    pub fn connect_hello(record: &PairingRecord, device: &DeviceKeys, cfg: &ClientConfig) -> Result<Tunnel> {
        let payload = hello_payload(&device.device_id());
        let eps = endpoint_order(record.tailscale, record.lan, record.v6);
        let direct = crate::direct::Target::from_record(record);
        dial_all(cfg, &eps, direct, device, &record.host_key, &payload)
    }
}

/// Pair with the PC named by `text`, then keep the pairing in `store`.
///
/// Fails closed on a malformed or expired text, on a host key different from
/// an existing pairing, and unless the PC answers after the handshake. The
/// one-use secret is not stored.
pub fn pair(store: &dyn KeyStore, text: &str, device_name: &str, cfg: &ClientConfig) -> Result<PairingRecord> {
    let info = PairingInfo::parse(text)?;
    if let Some(old) = store.load_pairing()? {
        check_pin(Some(&old.host_key), &info.host_key)?;
    }
    let device = load_or_create_device_keys(store)?;
    let tunnel = Tunnel::connect_pair(&info, &device, device_name, cfg)?;
    let record = PairingRecord::from_info(&info, device_name);
    store.save_pairing(&record)?;
    tunnel.close();
    Ok(record)
}

// ---- supervisor ------------------------------------------------------------------------

/// Reconnect waits: `min`, doubling, capped at `max`.
#[derive(Debug, Clone)]
pub struct Backoff {
    min: Duration,
    max: Duration,
    cur: Duration,
}

impl Backoff {
    pub fn new(min: Duration, max: Duration) -> Backoff {
        Backoff { min, max, cur: min }
    }
    /// The wait to use now; the next one doubles.
    pub fn next_delay(&mut self) -> Duration {
        let d = self.cur;
        self.cur = (self.cur * 2).min(self.max);
        d
    }
    pub fn reset(&mut self) {
        self.cur = self.min;
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConnState {
    Connecting { attempt: u32 },
    Connected { via: Via },
    /// The last attempt failed; the next one starts after `retry_in`.
    Failed { reason: String, retry_in: Duration },
    Stopped,
}

struct ClientInner {
    state: Mutex<ConnState>,
    state_cv: Condvar,
    subs: Mutex<Vec<Sender<ConnState>>>,
    tunnel: Mutex<Option<Arc<Tunnel>>>,
    stop: AtomicBool,
    cfg: ClientConfig,
}

impl ClientInner {
    fn publish(&self, s: ConnState) {
        *self.state.lock().unwrap() = s.clone();
        self.subs.lock().unwrap().retain(|tx| tx.send(s.clone()).is_ok());
        self.state_cv.notify_all();
    }
}

/// Keeps a tunnel to the PC up: connects with `hello`, redials at once when
/// a live session drops, and backs off 2 s, 4 s ... 30 s between failed
/// attempts. A session that dies within `backoff_min` of starting counts as
/// a failure, so a PC that accepts and then drops cannot cause a tight loop.
pub struct ConnectClient {
    inner: Arc<ClientInner>,
    handle: Mutex<Option<JoinHandle<()>>>,
}

impl ConnectClient {
    pub fn start(record: PairingRecord, device: DeviceKeys, cfg: ClientConfig) -> ConnectClient {
        let inner = Arc::new(ClientInner {
            state: Mutex::new(ConnState::Connecting { attempt: 1 }),
            state_cv: Condvar::new(),
            subs: Mutex::new(Vec::new()),
            tunnel: Mutex::new(None),
            stop: AtomicBool::new(false),
            cfg,
        });
        let i = inner.clone();
        let handle = thread::Builder::new()
            .name("remedy-connect-supervisor".into())
            .spawn(move || supervise(i, record, device))
            .expect("spawn supervisor");
        ConnectClient { inner, handle: Mutex::new(Some(handle)) }
    }

    /// Start from what `pair` saved. Fails if the phone is not paired.
    pub fn start_from_store(store: &dyn KeyStore, cfg: ClientConfig) -> Result<ConnectClient> {
        let record = store.load_pairing()?.ok_or(Error::Store("this phone is not paired".into()))?;
        let device = store.load_device_keys()?.ok_or(Error::Store("no device key".into()))?;
        Ok(Self::start(record, device, cfg))
    }

    pub fn state(&self) -> ConnState {
        self.inner.state.lock().unwrap().clone()
    }

    /// Every state change from now on. The receiver is dropped to unsubscribe.
    pub fn subscribe(&self) -> Receiver<ConnState> {
        let (tx, rx) = channel();
        self.inner.subs.lock().unwrap().push(tx);
        rx
    }

    /// Wait (real time) until a session is up.
    pub fn wait_connected(&self, timeout: Duration) -> Result<()> {
        let g = self.inner.state.lock().unwrap();
        let (g, _) = self
            .inner
            .state_cv
            .wait_timeout_while(g, timeout, |s| !matches!(s, ConnState::Connected { .. } | ConnState::Stopped))
            .unwrap();
        match &*g {
            ConnState::Connected { .. } => Ok(()),
            ConnState::Failed { reason, .. } => Err(Error::Unreachable(reason.clone())),
            _ => Err(Error::Timeout("not connected in time")),
        }
    }

    pub fn tunnel(&self) -> Option<Arc<Tunnel>> {
        self.inner.tunnel.lock().unwrap().clone()
    }

    pub fn request(&self, req: &HttpRequest) -> Result<ResponseStream> {
        self.tunnel().ok_or(Error::NotConnected)?.request(req)
    }

    pub fn http(&self, method: &str, target: &str, headers: &str, body: &[u8]) -> Result<HttpResponse> {
        self.tunnel().ok_or(Error::NotConnected)?.http(method, target, headers, body, None)
    }

    /// A pipe for `LoopbackShim` that follows reconnects.
    pub fn shim_pipe(&self) -> Arc<dyn ShimPipe> {
        self.inner.clone()
    }

    pub fn stop(&self) {
        self.inner.stop.store(true, Ordering::SeqCst);
        if let Some(t) = self.inner.tunnel.lock().unwrap().take() {
            t.close();
        }
        if let Some(h) = self.handle.lock().unwrap().take() {
            let _ = h.join();
        }
    }
}

impl Drop for ConnectClient {
    fn drop(&mut self) {
        self.stop();
    }
}

/// How long a request through the shim waits for the tunnel to come up.
const PIPE_WAIT: Duration = Duration::from_secs(8);

impl ShimPipe for ClientInner {
    fn pipe(&self, req: &ShimRequest, out: &mut dyn Write) -> Result<()> {
        if self.tunnel.lock().unwrap().is_none() {
            // The app asks as soon as it opens, and again right after a drop: hold the
            // request while the tunnel comes up instead of failing it at once.
            let g = self.state.lock().unwrap();
            let _ = self
                .state_cv
                .wait_timeout_while(g, PIPE_WAIT, |s| {
                    !matches!(s, ConnState::Connected { .. } | ConnState::Stopped)
                })
                .unwrap();
        }
        let t = self.tunnel.lock().unwrap().clone().ok_or(Error::NotConnected)?;
        t.pipe(req, out)
    }
}

/// The pairing with the direct address the PC reports right now, when that
/// differs from what is stored. The reply came through the authenticated
/// tunnel, so it is the PC's own word. `None`: nothing to change (or the PC
/// has no direct path, in which case what was stored is kept).
fn refreshed_direct(record: &PairingRecord, me_body: &[u8]) -> Option<PairingRecord> {
    let v: serde_json::Value = serde_json::from_slice(me_body).ok()?;
    let d = v.get("direct")?;
    let id = d.get("id")?.as_str()?.trim();
    if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let addrs: Vec<String> = d
        .get("addrs")
        .and_then(|a| a.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str())
                .filter(|x| x.parse::<SocketAddr>().is_ok_and(|sa| !sa.ip().is_unspecified() && sa.port() != 0))
                .take(6)
                .map(String::from)
                .collect()
        })
        .unwrap_or_default();
    let relay = d.get("relay").and_then(|r| r.as_str()).unwrap_or("").trim();
    let relay = if relay.starts_with("https://") { relay } else { "" };
    let mut next = record.clone();
    let mut put = |k: &str, val: String| {
        if val.is_empty() {
            next.extras.remove(k);
        } else {
            next.extras.insert(k.to_string(), val);
        }
    };
    put("ir", id.to_ascii_lowercase());
    put("ira", addrs.join(","));
    put("irr", relay.to_string());
    (next.extras != record.extras).then_some(next)
}

fn supervise(inner: Arc<ClientInner>, mut record: PairingRecord, device: DeviceKeys) {
    let cfg = &inner.cfg;
    let mut backoff = Backoff::new(cfg.backoff_min, cfg.backoff_max);
    let mut attempt = 0u32;
    while !inner.stop.load(Ordering::SeqCst) {
        attempt += 1;
        inner.publish(ConnState::Connecting { attempt });
        let started = cfg.clock.now();
        let failure = match Tunnel::connect_hello(&record, &device, cfg) {
            Ok(t) => {
                let t = Arc::new(t);
                *inner.tunnel.lock().unwrap() = Some(t.clone());
                if inner.stop.load(Ordering::SeqCst) {
                    t.close();
                    break;
                }
                inner.publish(ConnState::Connected { via: t.via() });
                // Learn where the PC can be reached away from home now, while
                // it is reachable: a home address changes, and this is the
                // only way the phone hears of it without a third party.
                if let Ok(me) = t.http("GET", "/connect/me", "", &[], Some(cfg.handshake_timeout)) {
                    if let Some(next) = refreshed_direct(&record, &me.body) {
                        record = next;
                        if let Some(save) = &cfg.on_record {
                            save(&record);
                        }
                    }
                }
                while !inner.stop.load(Ordering::SeqCst) && !t.wait_closed(Duration::from_millis(100)) {}
                *inner.tunnel.lock().unwrap() = None;
                if inner.stop.load(Ordering::SeqCst) {
                    break;
                }
                let lived = cfg.clock.now().saturating_sub(started);
                if lived >= cfg.backoff_min {
                    // A healthy session ended: redial at once, from the start.
                    backoff.reset();
                    attempt = 0;
                    continue;
                }
                t.close_reason().unwrap_or_else(|| "connection dropped".into())
            }
            Err(e) => e.to_string(),
        };
        let retry_in = backoff.next_delay();
        inner.publish(ConnState::Failed { reason: failure, retry_in });
        if !cfg.clock.sleep(retry_in, &inner.stop) {
            break;
        }
    }
    inner.publish(ConnState::Stopped);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pairing::PairSecret;

    #[test]
    fn a_new_direct_address_from_the_pc_updates_the_pairing() {
        let mut record = PairingRecord {
            host_key: [7; 32],
            lan: "192.168.1.10:7401".parse().unwrap(),
            tailscale: None,
            v6: None,
            device_name: "phone".into(),
            paired_at: 1,
            extras: Default::default(),
        };
        let id = "32f964994affa248ee05d85780291e07535c4229dfcba0bbde4c44f94594c602";
        let body = format!(
            r#"{{"direct":{{"id":"{id}","addrs":["203.0.113.9:7403","0.0.0.0:7403","junk"],"relay":"ftp://x"}}}}"#
        );
        let next = refreshed_direct(&record, body.as_bytes()).expect("a direct path appeared");
        assert_eq!(next.extras.get("ir").map(String::as_str), Some(id));
        assert_eq!(next.extras.get("ira").map(String::as_str), Some("203.0.113.9:7403"));
        assert!(!next.extras.contains_key("irr"), "only an https relay is kept");
        // The same report again changes nothing.
        record = next;
        assert!(refreshed_direct(&record, body.as_bytes()).is_none());
        // No direct path reported: what is stored stays.
        assert!(refreshed_direct(&record, br#"{"direct":null}"#).is_none());
        assert!(refreshed_direct(&record, b"not json").is_none());
        assert!(refreshed_direct(&record, br#"{"direct":{"id":"short","addrs":[]}}"#).is_none());
    }

    #[test]
    fn backoff_doubles_to_the_ceiling_and_resets() {
        let mut b = Backoff::new(Duration::from_secs(2), Duration::from_secs(30));
        let got: Vec<u64> = (0..7).map(|_| b.next_delay().as_secs()).collect();
        assert_eq!(got, vec![2, 4, 8, 16, 30, 30, 30]);
        b.reset();
        assert_eq!(b.next_delay().as_secs(), 2);
    }

    #[test]
    fn endpoint_order_is_tailscale_lan_v6() {
        let lan = "192.168.1.2:7401".parse().unwrap();
        let ts = "100.64.0.9:7401".parse().unwrap();
        let v6 = "[2001:db8::1]:7401".parse().unwrap();
        let order: Vec<Via> = endpoint_order(Some(ts), lan, Some(v6)).into_iter().map(|e| e.0).collect();
        assert_eq!(order, vec![Via::Tailscale, Via::Lan, Via::V6]);
        let order: Vec<Via> = endpoint_order(None, lan, None).into_iter().map(|e| e.0).collect();
        assert_eq!(order, vec![Via::Lan]);
    }

    #[test]
    fn payload_shapes() {
        let secret = PairSecret::new([7; 32]);
        let p = pair_payload(&secret, "  Pixel 9\0 ");
        assert_eq!(&p[..5], b"pair\0");
        assert_eq!(&p[5..37], &[7u8; 32]);
        assert_eq!(p[37], 0);
        assert_eq!(&p[38..], b"Pixel 9");
        assert_eq!(&pair_payload(&secret, "")[38..], b"phone");
        // 80-byte cap on a char boundary
        let long = "é".repeat(60);
        let p = pair_payload(&secret, &long);
        assert_eq!(p.len() - 38, 80);
        assert!(std::str::from_utf8(&p[38..]).is_ok());
        assert_eq!(hello_payload("abcd"), b"hello\0abcd");
    }
}
