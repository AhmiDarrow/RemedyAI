//! Phone-side deny list: paths the phone must never forward through the
//! tunnel. Port of `DenyPath.kt`; the PC enforces its own, stricter list
//! (`native/go/connect/deny.go`), this is the first line.
//!
//! The Kotlin regexes are `(?:^|/)(?:api/)?<tail>(?:/|$|\?|#)`; they are
//! matched by hand here to keep the crate free of a regex dependency.

pub const REASON: &str = "This path is only for the PC host, not the phone remote.";

/// Why a request must not be forwarded, or `None` when it may be.
pub fn forbidden_reason(target: &str, method: &str) -> Option<&'static str> {
    is_forbidden(target, method).then_some(REASON)
}

pub fn is_forbidden(target: &str, method: &str) -> bool {
    let path = normalize(target);
    let decoded = percent_decode(&path);
    for p in [path.clone(), decoded.clone(), format!("{path}/"), format!("{decoded}/")] {
        if has(&p, "computer/jobs/next", true)
            || has(&p, "computer/host", true)
            || contains_any(&p, &["local-bootstrap", "bootstrap-link"])
            || has(&p, "computer/jobs/", false)
            || has(&p, "computer/ui/command", false)
        {
            return true;
        }
        if has(&p, "connect", true) && !connect_me(&p) && !connect_revoke(&p) {
            return true;
        }
    }
    ["CONNECT", "TRACE", "TRACK"].iter().any(|m| method.eq_ignore_ascii_case(m))
}

fn contains_any(p: &str, needles: &[&str]) -> bool {
    needles.iter().any(|n| p.contains(n))
}

/// `(?:^|/)(?:api/)?` boundary before `j`.
fn boundary(p: &str, j: usize) -> bool {
    let b = p.as_bytes();
    j == 0 || b[j - 1] == b'/' || (j == 4 && &b[..4] == b"api/")
}

/// `(?:/|$|\?|#)` after `end`.
fn terminated(p: &str, end: usize) -> bool {
    matches!(p.as_bytes().get(end), None | Some(b'/') | Some(b'?') | Some(b'#'))
}

fn has(p: &str, tail: &str, need_term: bool) -> bool {
    p.match_indices(tail)
        .any(|(j, _)| boundary(p, j) && (!need_term || terminated(p, j + tail.len())))
}

fn connect_me(p: &str) -> bool {
    has(p, "connect/me", true) || has(p, "connect/preview", true)
}

fn connect_revoke(p: &str) -> bool {
    const HEAD: &str = "connect/devices/";
    p.match_indices(HEAD).any(|(j, _)| {
        if !boundary(p, j) {
            return false;
        }
        let rest = &p[j + HEAD.len()..];
        match rest.find('/') {
            Some(i) if i > 0 => {
                let after = &rest[i..];
                after.starts_with("/revoke") && terminated(rest, i + "/revoke".len())
            }
            _ => false,
        }
    })
}

/// Trim, drop fragment, collapse `//`, force a leading `/`, lowercase.
fn normalize(target: &str) -> String {
    let mut s = target.trim();
    let q = s.find('?');
    let h = s.find('#');
    let cut = match (q, h) {
        (Some(q), Some(h)) => Some(q.min(h)),
        (q, h) => q.or(h),
    };
    let query = q.map(|i| s[i..].to_string()).unwrap_or_default();
    if let Some(c) = cut {
        s = &s[..c];
    }
    let mut path = s.to_string();
    while path.contains("//") {
        path = path.replace("//", "/");
    }
    if path.is_empty() {
        path = "/".into();
    }
    if !path.starts_with('/') {
        path.insert(0, '/');
    }
    format!("{path}{query}").to_lowercase()
}

/// Percent-decode byte by byte; bytes map to the char of the same value.
pub(crate) fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' && i + 2 < b.len() {
            if let (Some(hi), Some(lo)) = (hexval(b[i + 1]), hexval(b[i + 2])) {
                out.push((hi * 16 + lo) as char);
                i += 3;
                continue;
            }
        }
        // copy the whole UTF-8 char starting at i
        let ch = s[i..].chars().next().expect("char boundary");
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

fn hexval(c: u8) -> Option<u8> {
    (c as char).to_digit(16).map(|d| d as u8)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jobs_next_family_forbidden() {
        for p in [
            "/api/computer/jobs/next",
            "/computer/jobs/next",
            "/api/computer/jobs/next?wait_ms=5000",
            "/api/computer/jobs/next?wait_ms=5000&take=1",
            "/api/computer/jobs/next/",
            "//api/computer/jobs/next",
            "/API/COMPUTER/JOBS/NEXT",
            "/api/computer/jobs/next#x",
            "api/computer/jobs/next",
            "/api/computer/jobs/next?take=1",
            "/%61pi/computer/jobs/next",
            "/api/computer/jobs%2Fnext",
            "/api/computer/jobs%2Fnext?wait_ms=1",
            "/api/computer/jobs/next/ ",
            "/api/auth/local-bootstrap",
            "/api/auth/bootstrap-link",
            "/api/computer/host/hello",
            "/api/computer/ui/command",
            "/api/computer/jobs/abc/complete",
            "/api/connect",
            "/api/connect/pair/start",
            "/api/connect/pause",
            "/api/connect/resume",
            "/api/connect/addresses",
            "/connect/pair/start",
            "/connect/pause",
            "/API/CONNECT/PAIR/START",
            "/api/connect%2Fpair%2Fstart",
            "/%61pi/connect/pair/start",
        ] {
            assert!(is_forbidden(p, "GET"), "should deny {p}");
        }
    }

    #[test]
    fn adjacent_paths_not_forbidden() {
        for p in [
            "/api/sessions",
            "/api/sessions/abc/messages",
            "/api/jobs",
            "/next",
            "/api/computer/capture",
            "/api/approvals",
            "/?connect=1",
            "/api/sessions/next-week",
            "/api/computer/snapshot",
            "/connect/me",
            "/api/connect/me",
            "/connect/preview",
            "/api/connect/preview",
            // Self-revoke passes the coarse phone gate; the PC checks that the
            // caller is the device being revoked.
            "/api/connect/devices/abc/revoke",
            "/connect/devices/abc/revoke",
        ] {
            assert!(!is_forbidden(p, "GET"), "should allow {p}");
        }
    }

    #[test]
    fn methods_and_reason() {
        assert!(is_forbidden("/api/computer/jobs/next", "POST"));
        assert!(is_forbidden("/api/sessions", "trace"));
        assert!(is_forbidden("/api/sessions", "CONNECT"));
        assert!(!is_forbidden("/api/sessions", "POST"));
        assert_eq!(forbidden_reason("/api/connect", "GET"), Some(REASON));
        assert_eq!(forbidden_reason("/api/sessions", "GET"), None);
    }

    #[test]
    fn percent_decode_edges() {
        assert_eq!(percent_decode("%61%2f%"), "a/%");
        assert_eq!(percent_decode("%zz%4"), "%zz%4");
        assert_eq!(percent_decode("é%41"), "éA");
        assert_eq!(percent_decode("%41"), "A");
    }
}
