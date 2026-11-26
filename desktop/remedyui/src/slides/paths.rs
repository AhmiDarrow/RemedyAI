//! Join and walk project paths the way `desktop/src/utils/filePaths.ts` does.
//! Windows drive roots and UNC shares stay put.

/// Join an API-relative file path without damaging Windows or POSIX absolute paths.
pub fn absolute_file_path(root: &str, path: &str) -> String {
    let drive = path.len() >= 2
        && path.as_bytes()[0].is_ascii_alphabetic()
        && path.as_bytes()[1] == b':'
        && path.as_bytes().get(2).is_some_and(|c| *c == b'\\' || *c == b'/');
    let rooted = path.starts_with('/') || path.starts_with('\\');
    if root.is_empty() || drive || rooted {
        return path.to_string();
    }
    let separator = if root.contains('\\') { '\\' } else { '/' };
    let base = root.trim_end_matches(['/', '\\']);
    let mut tail = path.to_string();
    if tail == "." || tail.starts_with("./") || tail.starts_with(".\\") {
        tail = tail
            .trim_start_matches('.')
            .trim_start_matches(['/', '\\'])
            .to_string();
    }
    let tail = tail.replace(['/', '\\'], &separator.to_string());
    if tail.is_empty() {
        root.to_string()
    } else {
        format!("{base}{separator}{tail}")
    }
}

/// Parent of `path`. A UNC `//server/share` and a Windows drive root are not walked up.
pub fn parent_file_path(path: &str) -> String {
    let normalized = path.replace('\\', "/").trim_end_matches('/').to_string();
    if normalized.is_empty() || normalized == "." {
        return if path.starts_with('/') {
            "/".into()
        } else {
            ".".into()
        };
    }
    if is_drive_root(&normalized) {
        return format!("{normalized}/");
    }
    if is_unc_root(&normalized) {
        return normalized;
    }
    let Some(index) = normalized.rfind('/') else {
        return ".".into();
    };
    let parent = &normalized[..index];
    if is_drive_root(parent) {
        return format!("{parent}/");
    }
    if parent.is_empty() {
        "/".into()
    } else {
        parent.to_string()
    }
}

/// True when Backspace / ↑ can leave this folder.
pub fn can_go_up(path: &str) -> bool {
    !path.is_empty() && path != "." && parent_file_path(path) != path.replace('\\', "/")
}

/// Where "parent folder" leads from `path`, shown under project `root`. At the project
/// root (".") that is the root's own parent, so the browser is not shut inside the project.
pub fn up_target(root: &str, path: &str) -> Option<String> {
    if can_go_up(path) {
        return Some(parent_file_path(path));
    }
    let at_root = path.is_empty() || path == ".";
    (at_root && can_go_up(root)).then(|| parent_file_path(root))
}

fn is_drive_root(s: &str) -> bool {
    s.len() == 2 && s.as_bytes()[0].is_ascii_alphabetic() && s.as_bytes()[1] == b':'
}

fn is_unc_root(s: &str) -> bool {
    let rest = s.trim_start_matches('/');
    if s.len() - rest.len() < 2 {
        return false;
    }
    let mut parts = rest.split('/');
    let server = parts.next().unwrap_or("");
    let share = parts.next().unwrap_or("");
    !server.is_empty() && !share.is_empty() && parts.next().is_none()
}

/// Percent-encode a query value (no `url` crate on this pin).
pub fn encode_query(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn up_leaves_the_project_root_and_stops_at_a_drive() {
        assert_eq!(up_target("C:/work/app", "src/ui").as_deref(), Some("src"));
        assert_eq!(up_target("C:/work/app", ".").as_deref(), Some("C:/work"));
        assert_eq!(up_target("C:/work/app", "C:/work").as_deref(), Some("C:/"));
        assert_eq!(up_target("C:/work/app", "C:/"), None);
        assert_eq!(up_target("", "."), None);
    }

    #[test]
    fn joins_relative_and_keeps_absolute() {
        assert_eq!(
            absolute_file_path("/home/ahmi/RemedyAI", "desktop/src/App.tsx"),
            "/home/ahmi/RemedyAI/desktop/src/App.tsx"
        );
        assert_eq!(
            absolute_file_path(r"C:\work", r"desktop\src"),
            r"C:\work\desktop\src"
        );
        assert_eq!(absolute_file_path("/home/ahmi", "/etc/hosts"), "/etc/hosts");
        assert_eq!(absolute_file_path("/home/ahmi", r"C:\Windows"), r"C:\Windows");
        assert_eq!(absolute_file_path("", "notes.md"), "notes.md");
        assert_eq!(absolute_file_path("/home/ahmi/", "."), "/home/ahmi/");
    }

    #[test]
    fn parents_stop_at_roots() {
        assert_eq!(parent_file_path("desktop/src"), "desktop");
        assert_eq!(parent_file_path("."), ".");
        assert_eq!(parent_file_path("/"), "/");
        assert_eq!(parent_file_path("C:/"), "C:/");
        assert_eq!(parent_file_path("C:/Users"), "C:/");
        assert_eq!(parent_file_path("//server/share"), "//server/share");
        assert_eq!(parent_file_path("//server/share/dir"), "//server/share");
        assert!(can_go_up("desktop/src"));
        assert!(!can_go_up("."));
        assert!(!can_go_up("C:/"));
        assert!(!can_go_up("//server/share"));
    }
}
