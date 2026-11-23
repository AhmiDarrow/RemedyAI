//! HTTP for chat-depth. Paths are relative to the client's `/api` base.

use crate::runtime::Client;
use remedy_ui::net::{Http, Request};
use remedy_ui::reactive::Cancel;
use serde_json::Value;

pub fn spawn_json(
    client: &Client,
    req: Request,
    done: impl FnOnce(Result<Value, String>) + Send + 'static,
) {
    let http = client.http();
    let _ = std::thread::Builder::new()
        .name("remedy-depth".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(req, &cancel)
                .map_err(|e| e.to_string())
                .and_then(|r| r.json::<Value>().map_err(|e| e.to_string()));
            done(out);
        });
}

pub fn spawn_bytes(
    client: &Client,
    req: Request,
    done: impl FnOnce(Result<Vec<u8>, String>) + Send + 'static,
) {
    let http = client.http();
    let _ = std::thread::Builder::new()
        .name("remedy-depth-bytes".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(req, &cancel)
                .map(|r| r.body)
                .map_err(|e| e.to_string());
            done(out);
        });
}

pub fn query(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

pub fn str_field(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string()
}

pub fn bool_field(v: &Value, key: &str) -> bool {
    match v.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        Some(Value::String(s)) => matches!(s.as_str(), "1" | "true" | "yes"),
        _ => false,
    }
}

pub fn u64_field(v: &Value, key: &str) -> u64 {
    v.get(key).and_then(|x| x.as_u64()).unwrap_or(0)
}

pub fn rows<'a>(v: &'a Value, key: &str) -> Vec<&'a Value> {
    v.get(key)
        .and_then(|x| x.as_array())
        .or_else(|| v.as_array())
        .map(|a| a.iter().collect())
        .unwrap_or_default()
}

/// Standard base64, with padding. No extra crate.
pub fn b64_encode(bytes: &[u8]) -> String {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::new();
    let mut i = 0;
    while i + 3 <= bytes.len() {
        let n = ((bytes[i] as u32) << 16) | ((bytes[i + 1] as u32) << 8) | bytes[i + 2] as u32;
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        out.push(T[((n >> 6) & 63) as usize] as char);
        out.push(T[(n & 63) as usize] as char);
        i += 3;
    }
    if i < bytes.len() {
        let left = bytes.len() - i;
        let mut n = (bytes[i] as u32) << 16;
        if left == 2 {
            n |= (bytes[i + 1] as u32) << 8;
        }
        out.push(T[((n >> 18) & 63) as usize] as char);
        out.push(T[((n >> 12) & 63) as usize] as char);
        if left == 2 {
            out.push(T[((n >> 6) & 63) as usize] as char);
            out.push('=');
        } else {
            out.push('=');
            out.push('=');
        }
    }
    out
}

pub fn b64_decode(text: &str) -> Option<Vec<u8>> {
    fn val(c: u8) -> Option<u8> {
        match c {
            b'A'..=b'Z' => Some(c - b'A'),
            b'a'..=b'z' => Some(c - b'a' + 26),
            b'0'..=b'9' => Some(c - b'0' + 52),
            b'+' => Some(62),
            b'/' => Some(63),
            _ => None,
        }
    }
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i].is_ascii_whitespace() {
            i += 1;
            continue;
        }
        let mut chunk = [0u8; 4];
        let mut n = 0;
        let mut pad = 0;
        while n < 4 && i < bytes.len() {
            let c = bytes[i];
            i += 1;
            if c.is_ascii_whitespace() {
                continue;
            }
            if c == b'=' {
                pad += 1;
                chunk[n] = 0;
            } else {
                chunk[n] = val(c)?;
            }
            n += 1;
        }
        if n == 0 {
            break;
        }
        if n < 4 {
            return None;
        }
        let v = ((chunk[0] as u32) << 18)
            | ((chunk[1] as u32) << 12)
            | ((chunk[2] as u32) << 6)
            | chunk[3] as u32;
        out.push((v >> 16) as u8);
        if pad < 2 {
            out.push((v >> 8) as u8);
        }
        if pad < 1 {
            out.push(v as u8);
        }
    }
    Some(out)
}

/// 16-bit PCM WAV, mono, at `rate`.
pub fn wav_bytes(samples: &[f32], rate: u32) -> Vec<u8> {
    let mut pcm = Vec::with_capacity(samples.len() * 2);
    for s in samples {
        let n = (s.clamp(-1.0, 1.0) * 32767.0) as i16;
        pcm.extend_from_slice(&n.to_le_bytes());
    }
    let mut out = Vec::with_capacity(44 + pcm.len());
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36u32 + pcm.len() as u32).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&rate.to_le_bytes());
    out.extend_from_slice(&(rate * 2).to_le_bytes());
    out.extend_from_slice(&2u16.to_le_bytes());
    out.extend_from_slice(&16u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&(pcm.len() as u32).to_le_bytes());
    out.extend(pcm);
    out
}

pub fn fetch_bytes(url: &str, done: impl FnOnce(Result<Vec<u8>, String>) + Send + 'static) {
    let http = Http::new("http://127.0.0.1");
    let req = Request::get(url);
    let _ = std::thread::Builder::new()
        .name("remedy-image".into())
        .spawn(move || {
            let cancel = Cancel::new();
            let out = http
                .send_blocking(req, &cancel)
                .map(|r| r.body)
                .map_err(|e| e.to_string());
            done(out);
        });
}

/// Reads up to 20 MB. Larger files are refused.
pub const ATTACH_CAP: u64 = 20 * 1024 * 1024;
pub const IMAGE_CAP: usize = 8 * 1024 * 1024;
