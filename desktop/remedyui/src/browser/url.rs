//! Address-bar and host URL rules from `browserUrl.ts` and `normalize_url`.

use url::Url;

/// Default homepage (Settings → `browser_home_url` overrides this).
pub const DEFAULT_BROWSER_HOME: &str = "https://github.com/AhmiDarrow/RemedyAI";

/// Omnibox search. `{query}` is percent-encoded.
pub const DEFAULT_BROWSER_SEARCH: &str = "https://duckduckgo.com/?q={query}";

const PLACEHOLDER_HOMES: &[&str] = &[
    "https://example.com",
    "https://example.com/",
    "http://example.com",
    "http://example.com/",
    "https://www.example.com",
    "https://www.example.com/",
    "about:blank",
];

/// Host check shared by the address bar and the Rust navigate gate.
pub fn blocked_browser_host(hostname: &str) -> bool {
    let host = hostname.trim().trim_end_matches('.').to_ascii_lowercase();
    if host.is_empty() {
        return true;
    }
    if matches!(
        host.as_str(),
        "metadata" | "metadata.google.internal" | "metadata.goog" | "instance-data"
    ) {
        return true;
    }
    if host.split('.').any(|label| label == "metadata") {
        return true;
    }
    if host.ends_with(".internal")
        || host.ends_with(".nip.io")
        || host.ends_with(".sslip.io")
        || host.ends_with(".xip.io")
        || host.starts_with("169.254.")
    {
        return true;
    }
    false
}

fn has_http(lower: &str) -> bool {
    lower.starts_with("http://") || lower.starts_with("https://")
}

fn strip_http(s: &str) -> &str {
    let lower = s.to_ascii_lowercase();
    if let Some(rest) = lower.strip_prefix("https://") {
        &s[s.len() - rest.len()..]
    } else if let Some(rest) = lower.strip_prefix("http://") {
        &s[s.len() - rest.len()..]
    } else {
        s
    }
}

/// Address-bar normalize from `browserUrl.ts`. Empty means "not a URL".
pub fn normalize_browser_url(raw: &str) -> String {
    let mut u = raw.trim().to_string();
    if u.is_empty() {
        return String::new();
    }
    let lower = u.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("data:")
        || lower.starts_with("vbscript:")
        || lower.starts_with("file:")
    {
        return String::new();
    }
    if u.chars().any(char::is_whitespace) || (u.contains('@') && !has_http(&lower)) {
        return String::new();
    }
    let before_query = u.split(['?', '#']).next().unwrap_or(u.as_str());
    let host_part = strip_http(before_query);
    if host_part.chars().any(|c| matches!(c, ',' | ';' | '"' | '\'')) {
        return String::new();
    }
    if !has_http(&lower) && !lower.starts_with("about:") {
        if !is_bare_host(&u) && u != "localhost" && !localhost_port(&u) {
            return String::new();
        }
        u = format!("https://{u}");
    }
    if u.to_ascii_lowercase().starts_with("about:") {
        return u;
    }
    let Ok(parsed) = Url::parse(&u) else {
        return String::new();
    };
    let host = parsed.host_str().unwrap_or("");
    if host.is_empty() || host.chars().any(char::is_whitespace) {
        return String::new();
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return String::new();
    }
    if let Some(auth) = u.split("://").nth(1).and_then(|r| r.split(['/', '?', '#']).next()) {
        if auth.contains('@') {
            return String::new();
        }
    }
    if blocked_browser_host(host) {
        return String::new();
    }
    u
}

fn is_bare_host(u: &str) -> bool {
    // (?:www\.)?[a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}(?:/.*)?
    let (host, rest_ok) = match u.split_once('/') {
        Some((h, _)) => (h, true),
        None => (u, true),
    };
    if !rest_ok {
        return false;
    }
    let host = host.strip_prefix("www.").unwrap_or(host);
    let mut labels = host.split('.');
    let Some(first) = labels.next() else {
        return false;
    };
    if first.is_empty()
        || !first.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
        || !first.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return false;
    }
    let Some(tld) = host.rsplit('.').next() else {
        return false;
    };
    if !host.contains('.') || tld.len() < 2 || !tld.chars().all(|c| c.is_ascii_alphabetic()) {
        return false;
    }
    host.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '.')
}

fn localhost_port(u: &str) -> bool {
    let Some(rest) = u.strip_prefix("localhost:") else {
        return false;
    };
    !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit())
}

/// Search URL for free text. Empty when the query is a blocked scheme.
pub fn browser_search_url(query: &str, template: Option<&str>) -> String {
    let q = query.trim();
    if q.is_empty() {
        return String::new();
    }
    let lower = q.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("data:")
        || lower.starts_with("vbscript:")
        || lower.starts_with("file:")
    {
        return String::new();
    }
    let tpl = template.unwrap_or(DEFAULT_BROWSER_SEARCH);
    let tpl = if tpl.contains("{query}") { tpl } else { DEFAULT_BROWSER_SEARCH };
    let encoded = urlencoding_query(q);
    tpl.replace("{query}", &encoded)
}

fn urlencoding_query(q: &str) -> String {
    let mut out = String::new();
    for b in q.as_bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            b' ' => out.push_str("%20"),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

/// Go / Enter: a real URL when the text is one, otherwise web search.
pub fn resolve_browser_address_bar(raw: &str, search_template: Option<&str>) -> String {
    let as_url = normalize_browser_url(raw);
    if !as_url.is_empty() {
        return as_url;
    }
    let q = raw.trim();
    if q.is_empty() {
        return String::new();
    }
    browser_search_url(q, search_template)
}

/// Settings / Home. Placeholder homes fall back to the Remedy repository.
pub fn resolve_browser_home(raw: Option<&str>) -> String {
    let n = normalize_browser_url(raw.unwrap_or(""));
    if n.is_empty() {
        return DEFAULT_BROWSER_HOME.into();
    }
    let key = n.trim_end_matches('/').to_ascii_lowercase();
    let with_slash = format!("{key}/");
    let blocked = PLACEHOLDER_HOMES.iter().any(|p| {
        let p = *p;
        p == n || p.eq_ignore_ascii_case(&n) || p.eq_ignore_ascii_case(&key) || p.eq_ignore_ascii_case(&with_slash)
    }) || key == "https://example.com"
        || key == "http://example.com";
    if blocked {
        DEFAULT_BROWSER_HOME.into()
    } else {
        n
    }
}

/// Rust navigate gate (`normalize_url` in the Tauri host). Stricter than the
/// address bar: spaces and public IPs are errors, not searches.
pub fn normalize_url(raw: &str) -> Result<String, String> {
    let u = raw.trim();
    if u.is_empty() {
        return Err("empty url".into());
    }
    if u.contains(' ') || u.contains('\n') || u.contains('\t') {
        return Err("invalid url: spaces (refusing task-text leak)".into());
    }
    let lower = u.to_ascii_lowercase();
    if u.contains('@') && !lower.starts_with("http://") && !lower.starts_with("https://") {
        return Err("invalid url: looks like email, not a page URL".into());
    }
    let host_part = u.split('?').next().unwrap_or(u).split('#').next().unwrap_or(u);
    if host_part.contains(',') || host_part.contains(';') || host_part.contains('"') {
        return Err("invalid url: illegal characters in host".into());
    }
    if lower.starts_with("javascript:") || lower.starts_with("data:") || lower.starts_with("file:") {
        return Err("unsupported url scheme".into());
    }
    let candidate = if lower.starts_with("http://") || lower.starts_with("https://") || lower.starts_with("about:")
    {
        u.to_string()
    } else {
        format!("https://{u}")
    };
    if candidate.to_ascii_lowercase().starts_with("about:") {
        return Ok(candidate);
    }
    let parsed = Url::parse(&candidate).map_err(|e| format!("invalid url: {e}"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return Err(format!("unsupported url scheme: {scheme}"));
    }
    if let Some(after) = candidate.splitn(2, "://").nth(1) {
        let authority = after.split(['/', '?', '#']).next().unwrap_or(after);
        if authority.contains('@') {
            return Err("blocked url userinfo".into());
        }
    }
    let host = parsed.host_str().ok_or_else(|| "invalid url: missing host".to_string())?;
    if host.contains(' ') || host.is_empty() {
        return Err("invalid url: bad host".into());
    }
    let host_for_ip = if let Ok(v6) = host.parse::<std::net::Ipv6Addr>() {
        v6.to_ipv4_mapped().map(|v4| v4.to_string()).unwrap_or_else(|| host.to_string())
    } else {
        host.to_string()
    };
    let ok_host = host == "localhost"
        || host.parse::<std::net::Ipv4Addr>().is_ok()
        || host_for_ip.parse::<std::net::Ipv4Addr>().is_ok()
        || host.contains('.');
    if !ok_host {
        return Err(format!("invalid url host: {host}"));
    }
    let host_lc = host.to_ascii_lowercase();
    let ip_lc = host_for_ip.to_ascii_lowercase();
    if blocked_browser_host(&host_lc) || ip_lc.starts_with("169.254.") {
        return Err(format!("blocked metadata / link-local host: {host}"));
    }
    if let Ok(ip) = host_for_ip.parse::<std::net::Ipv4Addr>() {
        let o = ip.octets();
        let loopback = o[0] == 127 || ip.is_unspecified();
        let rfc1918 = o[0] == 10 || (o[0] == 192 && o[1] == 168) || (o[0] == 172 && (16..=31).contains(&o[1]));
        if !loopback && !rfc1918 {
            return Err(format!("blocked non-private IP: {host}"));
        }
    }
    Ok(candidate)
}

/// True when the system browser may open this address.
pub fn is_openable_browser_url(url: &str) -> bool {
    let u = url.trim();
    let lower = u.to_ascii_lowercase();
    (lower.starts_with("http://") || lower.starts_with("https://") || lower.starts_with("about:"))
        && normalize_browser_url(u) == u
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn address_bar_searches_prose_and_keeps_https() {
        assert_eq!(
            resolve_browser_address_bar("https://github.com/AhmiDarrow/RemedyAI", None),
            "https://github.com/AhmiDarrow/RemedyAI"
        );
        let q = resolve_browser_address_bar("remedy desktop browser", None);
        assert!(q.starts_with("https://duckduckgo.com/?q=remedy%20desktop%20browser"), "{q}");
        assert!(normalize_browser_url("javascript:alert(1)").is_empty());
        assert!(normalize_browser_url("https://user:pass@example.com/").is_empty());
    }

    #[test]
    fn home_rejects_placeholders() {
        assert_eq!(resolve_browser_home(Some("https://example.com")), DEFAULT_BROWSER_HOME);
        assert_eq!(resolve_browser_home(Some("")), DEFAULT_BROWSER_HOME);
        assert_eq!(
            resolve_browser_home(Some("https://news.ycombinator.com")),
            "https://news.ycombinator.com"
        );
    }

    #[test]
    fn rust_gate_rejects_userinfo_metadata_and_public_ips() {
        assert!(normalize_url("https://user:pass@example.com/").is_err());
        assert!(normalize_url("https://@mail.google.com/").is_err());
        assert!(normalize_url("http://[::ffff:169.254.169.254]/").is_err());
        assert!(normalize_url("http://169.254.169.254/").is_err());
        assert!(normalize_url("http://metadata.nicob.net/").is_err());
        assert!(normalize_url("https://1.2.3.4/").is_err());
        assert!(normalize_url("https://mail.google.com").is_ok());
        assert!(normalize_url("http://127.0.0.1:7400/").is_ok());
        assert!(normalize_url("gmail sign in, type user@host").is_err());
    }
}
