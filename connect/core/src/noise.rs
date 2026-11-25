//! Noise_IK_25519_ChaChaPoly_BLAKE2s handshake and record layer.
//!
//! Wire facts (checked against `native/go/connect/{noise,record,pipe}.go`):
//!
//! * Prologue `remedy-connect/1`. Phone is the initiator and knows the host
//!   static key from the pairing text.
//! * Handshake messages are `u32be len | bytes`, len 1..=65536.
//! * Transport records are `u32be len | nonce12 | ciphertext`, where
//!   `len = 12 + ciphertext`, `nonce12 = 0,0,0,0 || u64le(counter)`, AD empty,
//!   and `len <= 65536` (so a plaintext is at most 65508 bytes).
//! * Rekey is the Noise default (`ENCRYPT(k, 2^64-1, "", 32 zero bytes)[:32]`)
//!   and, unlike the Noise spec's `n` handling, the Go side **resets the
//!   counter to 0** on rekey. This module does the same.
//!
//! snow's `StatelessTransportState` is used so the counters (and the reset)
//! are ours; snow still does every AEAD operation and the rekey itself.

use std::io::{Read, Write};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use snow::params::NoiseParams;
use snow::{Builder, HandshakeState, StatelessTransportState};

use crate::clock::Clock;
use crate::error::{Error, Result};
use crate::keys::DeviceKeys;

pub const PROTOCOL: &str = "Noise_IK_25519_ChaChaPoly_BLAKE2s";
pub const PROLOGUE: &[u8] = b"remedy-connect/1";
pub const NONCE_LEN: usize = 12;
pub const TAG_LEN: usize = 16;
pub const MAX_RECORD_BODY: usize = 65536;
pub const MAX_PLAINTEXT: usize = MAX_RECORD_BODY - NONCE_LEN - TAG_LEN;
pub const REKEY_AFTER_RECORDS: u64 = 65536;
pub const REKEY_AFTER: Duration = Duration::from_secs(15 * 60);

/// When the sender rekeys: after this many records or this much time since
/// the last rekey, whichever comes first. Tests lower the record count.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RekeyPolicy {
    pub records: u64,
    pub interval: Duration,
}

impl Default for RekeyPolicy {
    fn default() -> Self {
        RekeyPolicy { records: REKEY_AFTER_RECORDS, interval: REKEY_AFTER }
    }
}

fn params() -> Result<NoiseParams> {
    PROTOCOL.parse().map_err(|_| Error::Noise("bad protocol name"))
}

pub(crate) fn build_initiator(
    device: &DeviceKeys,
    host_key: &[u8; 32],
    fixed_ephemeral: Option<&[u8]>,
) -> Result<HandshakeState> {
    let mut b = Builder::new(params()?)
        .local_private_key(device.secret())
        .map_err(|_| Error::Noise("bad device key"))?
        .remote_public_key(host_key)
        .map_err(|_| Error::Noise("bad host key"))?
        .prologue(PROLOGUE)
        .map_err(|_| Error::Noise("bad prologue"))?;
    if let Some(e) = fixed_ephemeral {
        b = b.fixed_ephemeral_key_for_testing_only(e);
    }
    b.build_initiator().map_err(|_| Error::Noise("handshake setup failed"))
}

fn write_prefixed<W: Write>(w: &mut W, body: &[u8]) -> Result<()> {
    if body.is_empty() || body.len() > MAX_RECORD_BODY {
        return Err(Error::Protocol("handshake length"));
    }
    let mut buf = Vec::with_capacity(4 + body.len());
    buf.extend_from_slice(&(body.len() as u32).to_be_bytes());
    buf.extend_from_slice(body);
    w.write_all(&buf)?;
    w.flush()?;
    Ok(())
}

fn read_prefixed<R: Read>(r: &mut R) -> Result<Vec<u8>> {
    let mut h = [0u8; 4];
    r.read_exact(&mut h)?;
    let len = u32::from_be_bytes(h) as usize;
    if len == 0 || len > MAX_RECORD_BODY {
        return Err(Error::Protocol("handshake length"));
    }
    let mut body = vec![0u8; len];
    r.read_exact(&mut body)?;
    Ok(body)
}

/// Run the initiator side over `io`. The host answers with its second
/// handshake message *before* it checks the payload, so success here proves
/// only that the host holds the pinned key; the caller must still see a
/// round trip (PONG) before treating pair/hello as accepted.
pub(crate) fn run_initiator<S: Read + Write>(
    mut hs: HandshakeState,
    io: &mut S,
    payload: &[u8],
) -> Result<StatelessTransportState> {
    let mut out = vec![0u8; 65535];
    let n = hs.write_message(payload, &mut out).map_err(|_| Error::Noise("handshake write failed"))?;
    write_prefixed(io, &out[..n])?;
    // A host that cannot decrypt our first message (wrong key) or is paused
    // just hangs up, so EOF here is a handshake failure, not a network error.
    let msg = read_prefixed(io).map_err(|e| match e {
        Error::Io(ioe) if ioe.kind() == std::io::ErrorKind::UnexpectedEof => {
            Error::Noise("the PC closed the connection during the handshake (not the paired PC, or Connect paused)")
        }
        other => other,
    })?;
    let mut sink = vec![0u8; 65535];
    hs.read_message(&msg, &mut sink).map_err(|_| Error::Noise("handshake rejected (wrong host key?)"))?;
    if !hs.is_handshake_finished() {
        return Err(Error::Noise("handshake incomplete"));
    }
    hs.into_stateless_transport_mode().map_err(|_| Error::Noise("handshake split failed"))
}

pub(crate) fn initiator_handshake<S: Read + Write>(
    io: &mut S,
    device: &DeviceKeys,
    host_key: &[u8; 32],
    payload: &[u8],
    policy: RekeyPolicy,
    clock: Arc<dyn Clock>,
) -> Result<Transport> {
    let hs = build_initiator(device, host_key, None)?;
    let state = run_initiator(hs, io, payload)?;
    Ok(Transport::new(state, policy, clock))
}

/// Read one transport record body (`nonce12 || ciphertext`).
pub(crate) fn read_record<R: Read>(r: &mut R) -> Result<Vec<u8>> {
    let mut h = [0u8; 4];
    r.read_exact(&mut h)?;
    let len = u32::from_be_bytes(h) as usize;
    if !(NONCE_LEN + TAG_LEN..=MAX_RECORD_BODY).contains(&len) {
        return Err(Error::Protocol("record length"));
    }
    let mut body = vec![0u8; len];
    r.read_exact(&mut body)?;
    Ok(body)
}

struct Inner {
    state: StatelessTransportState,
    send_n: u64,
    recv_n: u64,
    last_rekey: Duration,
}

/// Both directions of an established session.
pub struct Transport {
    inner: Mutex<Inner>,
    policy: RekeyPolicy,
    clock: Arc<dyn Clock>,
}

impl Transport {
    pub(crate) fn new(state: StatelessTransportState, policy: RekeyPolicy, clock: Arc<dyn Clock>) -> Transport {
        let now = clock.now();
        Transport {
            inner: Mutex::new(Inner { state, send_n: 0, recv_n: 0, last_rekey: now }),
            policy,
            clock,
        }
    }

    /// Encrypt one plaintext into a complete wire record
    /// (`u32be len | nonce12 | ciphertext`) and advance the send counter.
    pub fn seal(&self, plaintext: &[u8]) -> Result<Vec<u8>> {
        if plaintext.len() > MAX_PLAINTEXT {
            return Err(Error::TooLarge("record plaintext exceeds 65508 bytes"));
        }
        let mut g = self.inner.lock().unwrap();
        if g.send_n == u64::MAX {
            return Err(Error::Noise("send nonce exhausted"));
        }
        let body_len = NONCE_LEN + plaintext.len() + TAG_LEN;
        let mut out = vec![0u8; 4 + body_len];
        out[..4].copy_from_slice(&(body_len as u32).to_be_bytes());
        out[8..16].copy_from_slice(&g.send_n.to_le_bytes()); // nonce12 = 4 zero bytes || u64le
        let n = g
            .state
            .write_message(g.send_n, plaintext, &mut out[4 + NONCE_LEN..])
            .map_err(|_| Error::Noise("encrypt failed"))?;
        debug_assert_eq!(n, plaintext.len() + TAG_LEN);
        g.send_n += 1;
        Ok(out)
    }

    /// Decrypt a record body (`nonce12 || ciphertext`). The nonce must be the
    /// next expected counter; a failed record does not advance the counter.
    pub fn open(&self, body: &[u8]) -> Result<Vec<u8>> {
        if !(NONCE_LEN + TAG_LEN..=MAX_RECORD_BODY).contains(&body.len()) {
            return Err(Error::Protocol("record length"));
        }
        let mut g = self.inner.lock().unwrap();
        let counter = u64::from_le_bytes(body[4..12].try_into().expect("8 bytes"));
        if body[..4] != [0, 0, 0, 0] || counter != g.recv_n || g.recv_n == u64::MAX {
            return Err(Error::Noise("record nonce replay or out of order"));
        }
        let mut out = vec![0u8; body.len() - NONCE_LEN];
        let n = g
            .state
            .read_message(counter, &body[NONCE_LEN..], &mut out)
            .map_err(|_| Error::Noise("record failed authentication"))?;
        out.truncate(n);
        g.recv_n += 1;
        Ok(out)
    }

    /// True when the sender should emit a REKEY frame now.
    pub fn rekey_due(&self) -> bool {
        let g = self.inner.lock().unwrap();
        g.send_n >= self.policy.records || self.clock.now().saturating_sub(g.last_rekey) >= self.policy.interval
    }

    /// Rekey the send direction (call right after the REKEY frame is written).
    pub fn rekey_send(&self) {
        let mut g = self.inner.lock().unwrap();
        g.state.rekey_outgoing();
        g.send_n = 0;
        g.last_rekey = self.clock.now();
    }

    /// Rekey the receive direction (call when a REKEY frame arrives).
    pub fn rekey_recv(&self) {
        let mut g = self.inner.lock().unwrap();
        g.state.rekey_incoming();
        g.recv_n = 0;
    }

    #[cfg(test)]
    pub(crate) fn counters(&self) -> (u64, u64) {
        let g = self.inner.lock().unwrap();
        (g.send_n, g.recv_n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::clock::FakeClock;
    use serde_json::Value;
    use std::io::Cursor;

    use crate::testutil::fixture;

    fn h(v: &Value, k: &str) -> Vec<u8> {
        hex::decode(v[k].as_str().unwrap_or_else(|| panic!("missing {k}"))).unwrap()
    }

    fn a32(v: Vec<u8>) -> [u8; 32] {
        v.try_into().unwrap()
    }

    /// Scripted peer: returns `reply` on read, records what is written.
    struct Duplex {
        input: Cursor<Vec<u8>>,
        output: Vec<u8>,
    }
    impl Read for Duplex {
        fn read(&mut self, b: &mut [u8]) -> std::io::Result<usize> {
            self.input.read(b)
        }
    }
    impl Write for Duplex {
        fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
            self.output.extend_from_slice(b);
            Ok(b.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    fn prefixed(body: &[u8]) -> Vec<u8> {
        let mut v = (body.len() as u32).to_be_bytes().to_vec();
        v.extend_from_slice(body);
        v
    }

    /// The Go "remedy_pair_secret" vector: fixed keys, prologue
    /// `remedy-connect/1`, first payload = the raw secret. Checks our
    /// initiator emits the exact msg0 bytes and accepts the exact msg1 bytes.
    #[test]
    fn go_vector_pair_secret_handshake_and_post_split() {
        let fx = fixture("noise_ik_pair_secret.json");
        let post = fixture("noise_ik_post_split.json");
        let device = DeviceKeys::from_secret(&a32(h(&fx, "init_static_priv_hex"))).unwrap();
        assert_eq!(device.public().as_slice(), h(&fx, "init_static_pub_hex"));
        let host_pub = a32(h(&fx, "resp_static_pub_hex"));
        let hs = build_initiator(&device, &host_pub, Some(&h(&fx, "init_eph_priv_hex"))).unwrap();
        let mut io = Duplex { input: Cursor::new(prefixed(&h(&fx, "msg1_ciphertext_hex"))), output: vec![] };
        let state = run_initiator(hs, &mut io, &h(&fx, "msg0_payload_hex")).unwrap();
        assert_eq!(io.output, prefixed(&h(&fx, "msg0_ciphertext_hex")));

        let t = Transport::new(state, RekeyPolicy::default(), FakeClock::new());
        // initiator -> responder, counter 0, AD empty
        let rec = t.seal(&h(&post, "transport_i_to_r_plaintext_hex")).unwrap();
        assert_eq!(&rec[..4], &(12u32 + 13 + 16).to_be_bytes());
        assert_eq!(&rec[4..16], &[0u8; 12]);
        assert_eq!(&rec[16..], h(&post, "transport_i_to_r_ciphertext_hex").as_slice());
        // responder -> initiator
        let mut body = vec![0u8; 12];
        body.extend_from_slice(&h(&post, "transport_r_to_i_ciphertext_hex"));
        assert_eq!(t.open(&body).unwrap(), h(&post, "transport_r_to_i_plaintext_hex"));
    }

    /// Go record_framing.json: full records for counters 0..3 in both
    /// directions, plus the reject cases.
    #[test]
    fn go_vector_record_framing() {
        let fx = fixture("noise_ik_pair_secret.json");
        let rf = fixture("record_framing.json");
        let device = DeviceKeys::from_secret(&a32(h(&fx, "init_static_priv_hex"))).unwrap();
        let host_pub = a32(h(&fx, "resp_static_pub_hex"));
        let hs = build_initiator(&device, &host_pub, Some(&h(&fx, "init_eph_priv_hex"))).unwrap();
        let mut io = Duplex { input: Cursor::new(prefixed(&h(&fx, "msg1_ciphertext_hex"))), output: vec![] };
        let t = Transport::new(
            run_initiator(hs, &mut io, &h(&fx, "msg0_payload_hex")).unwrap(),
            RekeyPolicy::default(),
            FakeClock::new(),
        );
        for v in rf["encrypt_i_to_r"].as_array().unwrap() {
            assert_eq!(t.seal(&h(v, "plaintext_hex")).unwrap(), h(v, "record_hex"), "{v}");
        }
        for v in rf["encrypt_r_to_i"].as_array().unwrap() {
            let rec = h(v, "record_hex");
            assert_eq!(t.open(&rec[4..]).unwrap(), h(v, "plaintext_hex"));
        }
        // length / size rejects
        let rej = &rf["reject"];
        assert!(read_record(&mut Cursor::new(h(rej, "oversize_length_field_hex"))).is_err());
        assert!(read_record(&mut Cursor::new(h(rej, "too_short_length_hex"))).is_err());
        assert!(read_record(&mut Cursor::new(h(rej, "truncated_claim12_hex"))).is_err());
        // pack vectors: our seal layout equals length||nonce||ct
        for v in rf["pack_vectors"].as_array().unwrap() {
            let mut rec = (v["length"].as_u64().unwrap() as u32).to_be_bytes().to_vec();
            rec.extend(h(v, "nonce_hex"));
            rec.extend(h(v, "ciphertext_hex"));
            assert_eq!(rec, h(v, "record_hex"));
        }
    }

    /// The snow reference vector (different prologue, so built directly):
    /// guards the snow dependency and parameter string.
    #[test]
    fn go_vector_snow_reference_handshake() {
        let fx = fixture("noise_ik_snow.json");
        let mut ini = Builder::new(params().unwrap())
            .local_private_key(&h(&fx, "init_static_priv_hex"))
            .unwrap()
            .remote_public_key(&h(&fx, "resp_static_pub_hex"))
            .unwrap()
            .prologue(&h(&fx, "prologue_hex"))
            .unwrap()
            .fixed_ephemeral_key_for_testing_only(&h(&fx, "init_eph_priv_hex"))
            .build_initiator()
            .unwrap();
        let mut buf = vec![0u8; 512];
        let n = ini.write_message(&h(&fx, "msg0_payload_hex"), &mut buf).unwrap();
        assert_eq!(&buf[..n], h(&fx, "msg0_ciphertext_hex").as_slice());
        let mut out = vec![0u8; 512];
        let n = ini.read_message(&h(&fx, "msg1_ciphertext_hex"), &mut out).unwrap();
        assert_eq!(&out[..n], h(&fx, "msg1_payload_hex").as_slice());
    }

    /// Both ends in one process: sequential records, replay, reorder, tamper.
    fn pair() -> (Transport, Transport) {
        let host = DeviceKeys::from_secret(&[0x22; 32]).unwrap();
        let phone = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        let ini = build_initiator(&phone, host.public(), None).unwrap();
        let mut resp = Builder::new(params().unwrap())
            .local_private_key(host.secret())
            .unwrap()
            .prologue(PROLOGUE)
            .unwrap()
            .build_responder()
            .unwrap();
        let mut ini = ini;
        let mut b1 = vec![0u8; 512];
        let mut b2 = vec![0u8; 512];
        let mut sink = vec![0u8; 512];
        let n = ini.write_message(b"hello", &mut b1).unwrap();
        resp.read_message(&b1[..n], &mut sink).unwrap();
        let n = resp.write_message(&[], &mut b2).unwrap();
        ini.read_message(&b2[..n], &mut sink).unwrap();
        let clock = FakeClock::new();
        let pol = RekeyPolicy { records: 3, interval: Duration::from_secs(60) };
        (
            Transport::new(ini.into_stateless_transport_mode().unwrap(), pol, clock.clone()),
            Transport::new(resp.into_stateless_transport_mode().unwrap(), pol, clock),
        )
    }

    #[test]
    fn records_replay_reorder_and_tamper_fail_closed() {
        let (a, b) = pair();
        let r0 = a.seal(b"zero").unwrap();
        let r1 = a.seal(b"one").unwrap();
        // out of order
        assert!(matches!(b.open(&r1[4..]), Err(Error::Noise(_))));
        assert_eq!(b.open(&r0[4..]).unwrap(), b"zero");
        // replay
        assert!(matches!(b.open(&r0[4..]), Err(Error::Noise(_))));
        // tamper, counter must not advance on failure
        let mut bad = r1.clone();
        let last = bad.len() - 1;
        bad[last] ^= 1;
        assert!(b.open(&bad[4..]).is_err());
        assert_eq!(b.open(&r1[4..]).unwrap(), b"one");
        // nonce prefix must be zero
        let mut pre = a.seal(b"x").unwrap();
        pre[4] = 1;
        assert!(b.open(&pre[4..]).is_err());
        // size limits
        assert!(matches!(a.seal(&vec![0u8; MAX_PLAINTEXT + 1]), Err(Error::TooLarge(_))));
        let max = a.seal(&vec![7u8; MAX_PLAINTEXT]).unwrap();
        assert_eq!(max.len() - 4, MAX_RECORD_BODY);
    }

    #[test]
    fn rekey_resets_counters_and_both_sides_agree() {
        let (a, b) = pair();
        for i in 0..3u8 {
            let r = a.seal(&[i]).unwrap();
            assert_eq!(b.open(&r[4..]).unwrap(), vec![i]);
        }
        assert!(a.rekey_due());
        a.rekey_send();
        b.rekey_recv();
        assert_eq!(a.counters().0, 0);
        assert!(!a.rekey_due());
        let r = a.seal(b"after").unwrap();
        assert_eq!(&r[8..16], &0u64.to_le_bytes());
        assert_eq!(b.open(&r[4..]).unwrap(), b"after");
        // the other direction is independent and untouched
        let back = b.seal(b"reply").unwrap();
        assert_eq!(&back[8..16], &0u64.to_le_bytes());
        assert_eq!(a.open(&back[4..]).unwrap(), b"reply");
        // a record sealed under the old key no longer opens
        let (c, d) = pair();
        let old = c.seal(b"old").unwrap();
        c.rekey_send();
        d.rekey_recv();
        assert!(d.open(&old[4..]).is_err());
    }

    #[test]
    fn rekey_by_time() {
        let host = DeviceKeys::from_secret(&[0x22; 32]).unwrap();
        let phone = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        let clock = FakeClock::new();
        let mut ini = build_initiator(&phone, host.public(), None).unwrap();
        let mut resp = Builder::new(params().unwrap())
            .local_private_key(host.secret())
            .unwrap()
            .prologue(PROLOGUE)
            .unwrap()
            .build_responder()
            .unwrap();
        let (mut b1, mut b2, mut s) = (vec![0u8; 512], vec![0u8; 512], vec![0u8; 512]);
        let n = ini.write_message(b"", &mut b1).unwrap();
        resp.read_message(&b1[..n], &mut s).unwrap();
        let n = resp.write_message(b"", &mut b2).unwrap();
        ini.read_message(&b2[..n], &mut s).unwrap();
        let t = Transport::new(ini.into_stateless_transport_mode().unwrap(), RekeyPolicy::default(), clock.clone());
        assert!(!t.rekey_due());
        clock.advance(REKEY_AFTER - Duration::from_secs(1));
        assert!(!t.rekey_due());
        clock.advance(Duration::from_secs(1));
        assert!(t.rekey_due());
        t.rekey_send();
        assert!(!t.rekey_due());
    }

    /// Generates the byte vectors used by the Go cross-check test quoted in
    /// the crate report (run with `--nocapture` to print them). The Go side
    /// has not been run against these; this test only proves the vectors are
    /// self-consistent with an independent snow responder.
    #[test]
    fn go_crosscheck_vectors_are_self_consistent() {
        use crate::client::pair_payload;
        use crate::frame::{
            encode_inner, encode_request, fragment, HttpRequest, TYPE_HTTP_REQ, TYPE_PING, TYPE_PONG, TYPE_REKEY,
        };
        use crate::pairing::PairSecret;
        let phone = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        let host = DeviceKeys::from_secret(&[0x22; 32]).unwrap();
        let payload = pair_payload(&PairSecret::new([7; 32]), "Pixel 9");
        let mut ini = build_initiator(&phone, host.public(), Some(&[0x33; 32])).unwrap();
        let mut resp = Builder::new(params().unwrap())
            .local_private_key(host.secret())
            .unwrap()
            .prologue(PROLOGUE)
            .unwrap()
            .fixed_ephemeral_key_for_testing_only(&[0x44; 32])
            .build_responder()
            .unwrap();
        let (mut m0, mut m1, mut sink) = (vec![0u8; 512], vec![0u8; 512], vec![0u8; 512]);
        let n0 = ini.write_message(&payload, &mut m0).unwrap();
        let got = resp.read_message(&m0[..n0], &mut sink).unwrap();
        assert_eq!(&sink[..got], payload.as_slice());
        let n1 = resp.write_message(&[], &mut m1).unwrap();
        let mut io = Duplex { input: Cursor::new(prefixed(&m1[..n1])), output: vec![] };
        // finish the initiator through the crate's own code path
        let ini2 = build_initiator(&phone, host.public(), Some(&[0x33; 32])).unwrap();
        let state = run_initiator(ini2, &mut io, &payload).unwrap();
        assert_eq!(io.output, prefixed(&m0[..n0]), "deterministic msg0");
        let phone_t = Transport::new(state, RekeyPolicy::default(), FakeClock::new());
        let host_t =
            Transport::new(resp.into_stateless_transport_mode().unwrap(), RekeyPolicy::default(), FakeClock::new());

        let ping1 = encode_inner(TYPE_PING, 1, &[], true).unwrap();
        let rekey = encode_inner(TYPE_REKEY, 0, &[], true).unwrap();
        let ping2 = encode_inner(TYPE_PING, 2, &[], true).unwrap();
        let req = encode_request(&HttpRequest {
            method: "GET".into(),
            target: "/api/ping".into(),
            headers: "Accept: */*".into(),
            body: vec![],
        })
        .unwrap();
        let req_frame = fragment(TYPE_HTTP_REQ, 3, &req, MAX_PLAINTEXT, true).unwrap().remove(0);
        let r1 = phone_t.seal(&ping1).unwrap();
        let r2 = phone_t.seal(&rekey).unwrap();
        phone_t.rekey_send();
        let r3 = phone_t.seal(&ping2).unwrap();
        let r4 = phone_t.seal(&req_frame).unwrap();
        for (rec, want) in [(&r1, &ping1), (&r2, &rekey)] {
            assert_eq!(&host_t.open(&rec[4..]).unwrap(), want);
        }
        host_t.rekey_recv();
        for (rec, want) in [(&r3, &ping2), (&r4, &req_frame)] {
            assert_eq!(&host_t.open(&rec[4..]).unwrap(), want);
        }
        let pong1 = encode_inner(TYPE_PONG, 1, &[], true).unwrap();
        let pong2 = encode_inner(TYPE_PONG, 2, &[], true).unwrap();
        let h1 = host_t.seal(&pong1).unwrap();
        let h2 = host_t.seal(&rekey).unwrap();
        host_t.rekey_send();
        let h3 = host_t.seal(&pong2).unwrap();
        assert_eq!(phone_t.open(&h1[4..]).unwrap(), pong1);
        assert_eq!(phone_t.open(&h2[4..]).unwrap(), rekey);
        phone_t.rekey_recv();
        assert_eq!(phone_t.open(&h3[4..]).unwrap(), pong2);
        let x = hex::encode;
        println!("VEC msg0={}", x(&m0[..n0]));
        println!("VEC msg1={}", x(&m1[..n1]));
        println!("VEC req={}", x(&req));
        for (k, v) in [("r1", &r1), ("r2", &r2), ("r3", &r3), ("r4", &r4), ("h1", &h1), ("h2", &h2), ("h3", &h3)] {
            println!("VEC {k}={}", x(v));
        }
    }

    #[test]
    fn wrong_host_key_fails_handshake() {
        let phone = DeviceKeys::from_secret(&[0x11; 32]).unwrap();
        let real = DeviceKeys::from_secret(&[0x22; 32]).unwrap();
        let fake = DeviceKeys::from_secret(&[0x33; 32]).unwrap();
        // Responder holds `real`; phone was told `fake`.
        let mut ini = build_initiator(&phone, fake.public(), None).unwrap();
        let mut resp = Builder::new(params().unwrap())
            .local_private_key(real.secret())
            .unwrap()
            .prologue(PROLOGUE)
            .unwrap()
            .build_responder()
            .unwrap();
        let (mut b1, mut s) = (vec![0u8; 512], vec![0u8; 512]);
        let n = ini.write_message(b"", &mut b1).unwrap();
        assert!(resp.read_message(&b1[..n], &mut s).is_err());
    }
}
