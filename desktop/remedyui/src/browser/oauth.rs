//! Same-window OAuth rewrites from the Tauri browser host.
//!
//! RemedyUI does not open a second native web view. Popup OAuth is rewritten
//! to a redirect, and a GIS `/gsi/transform` document bounces back to the site
//! that started the login. The page script in `scripts::SAME_WINDOW_OAUTH_JS`
//! does the same work inside the page.

use url::Url;

const SSO_PLATFORM_SUFFIXES: &[&str] = &[
    ".auth0.com",
    ".okta.com",
    ".oktacdn.com",
    ".onelogin.com",
    ".pingidentity.com",
    ".duosecurity.com",
    ".microsoftonline.com",
    ".windows.net",
];

/// Percent-decode `+` as space. Used for Android intent fallback URLs.
pub fn urlencoding_minimal(enc: &str) -> Result<String, ()> {
    let bytes = enc.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let h = |c: u8| -> Option<u8> {
                    match c {
                        b'0'..=b'9' => Some(c - b'0'),
                        b'a'..=b'f' => Some(c - b'a' + 10),
                        b'A'..=b'F' => Some(c - b'A' + 10),
                        _ => None,
                    }
                };
                if let (Some(a), Some(b)) = (h(bytes[i + 1]), h(bytes[i + 2])) {
                    out.push((a << 4) | b);
                    i += 3;
                    continue;
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8(out).map_err(|_| ())
}

/// Rewrite auth navigations that cannot finish inside one web view.
/// `None` means "load this URL as given".
pub fn rewrite_oauth_navigation(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let lower = s.to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        if let Ok(mut u) = Url::parse(s) {
            let pairs: Vec<(String, String)> = u.query_pairs().map(|(k, v)| (k.into_owned(), v.into_owned())).collect();
            let needs = pairs.iter().any(|(k, v)| {
                (k == "ux_mode" && v == "popup")
                    || (k == "display" && v == "popup")
                    || (k == "ui_mode" && v == "card")
            });
            if needs {
                let mut ser = url::form_urlencoded::Serializer::new(String::new());
                let mut changed = false;
                for (k, v) in &pairs {
                    if k == "ux_mode" && v == "popup" {
                        ser.append_pair("ux_mode", "redirect");
                        changed = true;
                    } else if k == "display" && v == "popup" {
                        ser.append_pair("display", "page");
                        changed = true;
                    } else if k == "ui_mode" && v == "card" {
                        changed = true;
                    } else {
                        ser.append_pair(k, v);
                    }
                }
                if changed {
                    u.set_query(Some(&ser.finish()));
                    let out = u.to_string();
                    if out != s {
                        return Some(out);
                    }
                }
            }
        }
    }
    if lower.starts_with("storagerelay://") {
        let rest = s.get("storagerelay://".len()..)?;
        let scheme_host = rest.split('?').next().unwrap_or(rest);
        let mut parts = scheme_host.splitn(2, '/');
        let scheme = parts.next().unwrap_or("https");
        let hostpath = parts.next().unwrap_or("");
        if hostpath.is_empty() {
            return None;
        }
        let host = hostpath.split('/').next().unwrap_or(hostpath);
        if host.is_empty() {
            return None;
        }
        let scheme = if scheme.eq_ignore_ascii_case("http") { "http" } else { "https" };
        return Some(format!("{scheme}://{host}/"));
    }
    if lower.starts_with("intent:") {
        if let Some(idx) = lower.find("s.browser_fallback_url=") {
            let start = idx + "s.browser_fallback_url=".len();
            let rest = s.get(start..)?;
            let end = rest.find(';').unwrap_or(rest.len());
            let enc = rest.get(..end)?.trim();
            if let Ok(dec) = urlencoding_minimal(enc) {
                if dec.starts_with("http://") || dec.starts_with("https://") {
                    return Some(dec);
                }
            }
        }
    }
    None
}

/// Path and query heuristics for an auth hop. No product host list.
pub fn looks_like_auth_url_str(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    if lower.contains("ux_mode=")
        || lower.contains("response_type=")
        || lower.contains("client_id=")
        || lower.contains("redirect_uri=")
    {
        return true;
    }
    for token in [
        "/oauth",
        "/oauth2",
        "/oidc",
        "/saml",
        "/sso/",
        "/authorize",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/login",
        "/log-in",
        "/log_in",
        "/auth/",
        "/session",
        "/connect/",
        "/gsi/",
    ] {
        if lower.contains(token) {
            return true;
        }
    }
    false
}

/// Identity-provider document. Filter lists must not cancel these hops.
pub fn is_identity_provider_url(url: &str) -> bool {
    let Ok(parsed) = Url::parse(url) else {
        return false;
    };
    let host = parsed.host_str().unwrap_or("").to_ascii_lowercase();
    if host.is_empty() {
        return false;
    }
    let path = parsed.path().to_ascii_lowercase();
    let query = parsed.query().unwrap_or("").to_ascii_lowercase();
    for key in [
        "ux_mode=",
        "response_type=",
        "client_id=",
        "redirect_uri=",
        "response_mode=",
        "code_challenge=",
        "samlrequest=",
        "samlresponse=",
    ] {
        if query.contains(key) {
            return true;
        }
    }
    for token in [
        "/oauth",
        "/oauth2",
        "/oidc",
        "/saml",
        "/sso/",
        "/authorize",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/login",
        "/log-in",
        "/log_in",
        "/auth/",
        "/session",
        "/connect/",
        "/users/sign_in",
        "/dialog/oauth",
        "/i/flow/login",
        "/gsi/",
    ] {
        if path.contains(token) {
            return true;
        }
    }
    let first = host.split('.').next().unwrap_or("");
    if matches!(
        first,
        "login" | "accounts" | "account" | "auth" | "sso" | "id" | "identity" | "signin" | "ssoauth"
    ) {
        return true;
    }
    if SSO_PLATFORM_SUFFIXES.iter().any(|suf| host.ends_with(suf) || host == suf.trim_start_matches('.')) {
        return true;
    }
    host.split('.').any(|lab| matches!(lab, "auth" | "login" | "sso" | "idp" | "oauth" | "oidc" | "saml"))
}

pub fn is_gsi_transformer_url(url: &str) -> bool {
    url.to_ascii_lowercase().contains("/gsi/transform")
}

/// A new window the Tauri host would allow. RemedyUI never opens one; the
/// same-window script and this predicate decide when to follow the URL here.
pub fn should_allow_oauth_popup(url: &str) -> bool {
    let s = url.trim();
    let lower = s.to_ascii_lowercase();
    if s.is_empty() || lower == "about:blank" || lower.starts_with("about:blank") || lower.starts_with("about:") {
        return true;
    }
    if lower.contains("/gsi/") {
        return true;
    }
    is_identity_provider_url(s) || looks_like_auth_url_str(s)
}

pub fn should_remember_oauth_return(prev: &str, next: &str) -> bool {
    if prev.is_empty() || prev.starts_with("about:") {
        return false;
    }
    let next_auth = is_identity_provider_url(next) || next.to_ascii_lowercase().contains("/gsi/");
    if !next_auth {
        return false;
    }
    if let (Ok(p), Ok(n)) = (Url::parse(prev), Url::parse(next)) {
        if p.host_str().is_some() && p.host_str() == n.host_str() {
            return false;
        }
    }
    true
}

pub fn bounce_target_for_gsi_transformer(current: &str, return_url: Option<&str>) -> Option<String> {
    if !is_gsi_transformer_url(current) {
        return None;
    }
    let ret = return_url.map(str::trim).filter(|r| !r.is_empty() && !r.starts_with("about:") && *r != current)?;
    Some(ret.to_string())
}

/// Hostname with a leading `www.` dropped.
pub fn nav_host(u: &str) -> String {
    Url::parse(u)
        .ok()
        .and_then(|p| p.host_str().map(|h| h.to_ascii_lowercase()))
        .map(|h| h.trim_start_matches("www.").to_string())
        .unwrap_or_default()
}

pub fn same_page_url(a: &str, b: &str) -> bool {
    a.trim_end_matches('/') == b.trim_end_matches('/')
}

/// A load event settles a pending navigate when it is the requested URL, or a
/// new page on the same host (a redirect). A late event for the previous page
/// does not.
pub fn navigate_load_matches(loaded_url: &str, dest: &str, prev_url: &str) -> bool {
    if same_page_url(loaded_url, dest) {
        return true;
    }
    let want = nav_host(dest);
    let got = nav_host(loaded_url);
    let same_host = want.is_empty() || got.is_empty() || want == got;
    same_host && !same_page_url(loaded_url, prev_url)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn popup_oauth_becomes_a_redirect() {
        let out = rewrite_oauth_navigation("https://idp.example/authorize?ux_mode=popup&client_id=abc").unwrap();
        assert!(out.contains("ux_mode=redirect"), "{out}");
        assert!(!out.contains("ux_mode=popup"), "{out}");
        assert!(rewrite_oauth_navigation("https://example.com/shop").is_none());
    }

    #[test]
    fn storagerelay_and_intent_fallbacks() {
        assert_eq!(
            rewrite_oauth_navigation("storagerelay://https/accounts.example/?x=1").as_deref(),
            Some("https://accounts.example/")
        );
        let intent = "intent://scan/#Intent;scheme=zxing;S.browser_fallback_url=https%3A%2F%2Fexample.com%2Fback;end";
        assert_eq!(
            rewrite_oauth_navigation(intent).as_deref(),
            Some("https://example.com/back")
        );
    }

    #[test]
    fn gsi_transform_is_the_stuck_helper() {
        assert!(is_gsi_transformer_url("https://accounts.google.com/gsi/transform"));
        assert!(!is_gsi_transformer_url("https://accounts.google.com/gsi/select"));
    }

    #[test]
    fn native_popup_allowed_for_gis_and_blank() {
        assert!(should_allow_oauth_popup("about:blank"));
        assert!(should_allow_oauth_popup(""));
        assert!(should_allow_oauth_popup("https://accounts.google.com/gsi/select?client_id=x"));
        assert!(should_allow_oauth_popup("https://accounts.google.com/gsi/transform"));
        assert!(!should_allow_oauth_popup("https://example.com/shop"));
    }

    #[test]
    fn remembers_site_when_entering_idp() {
        assert!(should_remember_oauth_return(
            "https://github.com/login",
            "https://accounts.google.com/gsi/select"
        ));
        assert!(!should_remember_oauth_return(
            "https://accounts.google.com/gsi/select",
            "https://accounts.google.com/gsi/transform"
        ));
        assert!(!should_remember_oauth_return(
            "https://mail.example.com/inbox",
            "https://mail.example.com/inbox?tab=1"
        ));
    }

    #[test]
    fn bounces_transform_to_saved_site() {
        assert_eq!(
            bounce_target_for_gsi_transformer(
                "https://accounts.google.com/gsi/transform",
                Some("https://mail.example.com/inbox")
            )
            .as_deref(),
            Some("https://mail.example.com/inbox")
        );
        assert!(
            bounce_target_for_gsi_transformer("https://accounts.google.com/gsi/select", Some("https://mail.example.com/inbox"))
                .is_none()
        );
    }

    #[test]
    fn gsi_path_looks_like_auth() {
        assert!(looks_like_auth_url_str("https://accounts.google.com/gsi/transform"));
        assert!(is_identity_provider_url("https://login.example.com/start"));
    }

    #[test]
    fn same_host_redirect_settles_a_navigate() {
        assert!(navigate_load_matches(
            "https://www.walmart.com/search?q=milk",
            "https://walmart.com/search?q=milk",
            "https://www.walmart.com/"
        ));
        assert!(!navigate_load_matches(
            "https://www.walmart.com/",
            "https://www.walmart.com/search?q=milk",
            "https://www.walmart.com/"
        ));
    }
}
