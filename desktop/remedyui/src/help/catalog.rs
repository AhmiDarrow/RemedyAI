//! Owner's manual catalog. Metadata matches `desktop/src/help/catalog.ts`.
//! Bodies are the same markdown files, bundled offline next to this crate.

use std::sync::OnceLock;

/// One chapter of the offline manual.
pub struct HelpArticle {
    /// File id, such as `09-troubleshooting`.
    pub id: &'static str,
    /// Chapter title.
    pub title: &'static str,
    /// Sidebar group. First-seen order is the manual's order, not alphabetical.
    pub category: &'static str,
    /// Sort key inside the category.
    pub order: f64,
    /// Search tags.
    pub tags: &'static [&'static str],
    /// One-line description under a search hit.
    pub summary: &'static str,
    /// Markdown body.
    pub body: String,
}

struct Meta {
    id: &'static str,
    title: &'static str,
    category: &'static str,
    order: f64,
    tags: &'static [&'static str],
    summary: &'static str,
}

/// The chapter Help opens on.
pub(crate) const DEFAULT_ID: &str = "00-overview";

const META: &[Meta] = &[
    Meta {
        id: "00-overview",
        title: "Overview",
        category: "Start here",
        order: 0.0,
        tags: &["intro", "quick start", "map"],
        summary: "What Remedy is — a partner for any ability level — architecture sketch, and manual map.",
    },
    Meta {
        id: "16-continuity-philosophy",
        title: "How Remedy works (continuity)",
        category: "Start here",
        order: 0.5,
        tags: &["continuity", "philosophy", "memory", "partner", "vision"],
        summary: "Why Remedy feels like one partner on any model — silent continuity, not a bot farm.",
    },
    Meta {
        id: "17-nanoswarm",
        title: "Continuity workers (nano swarm)",
        category: "Reference",
        order: 16.5,
        tags: &["nanoswarm", "continuity", "token", "router", "pattern", "operator"],
        summary: "Operator guide: continuity workers, NanoToken BPE packs (v2 default), calibration — not chat agents.",
    },
    Meta {
        id: "18-agency",
        title: "Coding agency (Build-class power)",
        category: "Build",
        order: 7.5,
        tags: &["agency", "file_edit", "repo_search", "mission", "work alone", "tools"],
        summary: "file_edit, repo_search, missions, silent jobs — multi-hour coding with one partner.",
    },
    Meta {
        id: "19-metabolism",
        title: "Partner Metabolism (Advanced)",
        category: "How it works",
        order: 7.6,
        tags: &[
            "metabolism",
            "evidence",
            "tier",
            "governor",
            "time crystal",
            "shadow",
            "identity export",
        ],
        summary: "0.20.0+ silent partner OS: L0–L3 tiers, evidence ledger, shadow, IR, governor, portable identity.",
    },
    Meta {
        id: "computer-use-soak",
        title: "Computer-use soak notes",
        category: "Reference",
        order: 18.0,
        tags: &["computer-use", "browser", "rail", "soak", "navigate"],
        summary: "Maintainer checklist for computer use. Not an owner how-to. The agent can help_read it.",
    },
    Meta {
        id: "01-install-windows",
        title: "Install (Windows)",
        category: "Start here",
        order: 1.0,
        tags: &["install", "smartscreen", "paths"],
        summary: "Download, install paths, first launch, always-ready.",
    },
    Meta {
        id: "01-install-linux",
        title: "Install (Linux)",
        category: "Start here",
        order: 1.1,
        tags: &["install", "linux", "wslg", "paths"],
        summary: "Linux / WSLg desktop, sidecar rules, shared Windows home.",
    },
    Meta {
        id: "02-first-run",
        title: "First run & setup",
        category: "Start here",
        order: 2.0,
        tags: &["wizard", "setup", "skip"],
        summary: "Setup wizard steps, skip, Open setup, corrupt config.",
    },
    Meta {
        id: "03-providers-and-auth",
        title: "Providers & auth",
        category: "Configuration",
        order: 3.0,
        tags: &["xai", "oauth", "api key", "ollama", "provider"],
        summary: "Providers, keys, xAI OAuth, Ollama, local API token.",
    },
    Meta {
        id: "04-security-and-data",
        title: "Security & data",
        category: "Configuration",
        order: 4.0,
        tags: &["security", "privacy", "scope", "approvals", "trust", "dpapi"],
        summary: "Data map, what leaves the machine, scope, approvals, trust.",
    },
    Meta {
        id: "05-chat-and-sessions",
        title: "Chat & sessions",
        category: "Daily use",
        order: 5.0,
        tags: &["chat", "plan", "build", "proc", "sessions"],
        summary: "UI map, Plan/Build, tool process, sessions export.",
    },
    Meta {
        id: "06-memory-and-harness",
        title: "Memory & harness",
        category: "Daily use",
        order: 6.0,
        tags: &["memory", "compact", "goals", "profile"],
        summary: "Durable memory, Session Brief, harness modes.",
    },
    Meta {
        id: "07-skills",
        title: "Skills",
        category: "Daily use",
        order: 7.0,
        tags: &["skills", "quarantine", "trust", "lifecycle"],
        summary: "Skills panel, lifecycle, import safety.",
    },
    Meta {
        id: "08-updates-and-uninstall",
        title: "Updates & uninstall",
        category: "Maintenance",
        order: 8.0,
        tags: &["update", "uninstall", "wipe"],
        summary: "Check/install updates, uninstall data options.",
    },
    Meta {
        id: "09-troubleshooting",
        title: "Troubleshooting",
        category: "Maintenance",
        order: 9.0,
        tags: &["error", "server", "oauth", "defender", "fix"],
        summary: "Server, setup save, OAuth, Defender, provider errors.",
    },
    Meta {
        id: "10-cli-and-api",
        title: "CLI & API",
        category: "Reference",
        order: 10.0,
        tags: &["cli", "api", "sse", "openapi"],
        summary: "Power-user CLI and local HTTP API.",
    },
    Meta {
        id: "11-reference-commands",
        title: "Slash commands",
        category: "Reference",
        order: 11.0,
        tags: &["slash", "commands", "help"],
        summary: "Full slash command reference table.",
    },
    Meta {
        id: "12-reference-shortcuts",
        title: "Keyboard shortcuts",
        category: "Reference",
        order: 12.0,
        tags: &["hotkeys", "keyboard", "f1"],
        summary: "Composer and app-wide shortcuts.",
    },
    Meta {
        id: "13-whats-new",
        title: "What's new",
        category: "Reference",
        order: 13.0,
        tags: &["changelog", "release", "version"],
        summary: "Recent product changes for owners.",
    },
    Meta {
        id: "14-visual-decoder",
        title: "Visual decoder",
        category: "Configuration",
        order: 14.0,
        tags: &["vision", "image", "ocr", "llama", "qwen", "screenshot"],
        summary: "Local Qwen3.5-4B mind: text, vision, and image digests for muscle.",
    },
    Meta {
        id: "20-rmb-local-agent",
        title: "RMB local agent",
        category: "Configuration",
        order: 14.5,
        tags: &["rmb", "local", "llama", "coding", "tools", "gguf", "agent", "offline"],
        summary: "Built-in local agent host (llama.cpp) for coding and tool use.",
    },
    Meta {
        id: "15-free-providers",
        title: "Free providers & demo",
        category: "Configuration",
        order: 15.0,
        tags: &["free", "demo", "gemini", "groq", "ollama", "openrouter", "no signup", "rmb"],
        summary: "Demo mode, free API keys, and local Ollama — use Remedy without paying.",
    },
    Meta {
        id: "22-grove",
        title: "Grove (partner home)",
        category: "Daily use",
        order: 4.5,
        tags: &["grove", "goals", "plots", "storyline", "alongside", "surface", "studio"],
        summary: "The default surface: goals as plots, a needs-you strip, and each goal’s room with a live stage and a plain-words record.",
    },
    Meta {
        id: "23-vault",
        title: "Remedy Vault",
        category: "Configuration",
        order: 4.6,
        tags: &["vault", "card", "payment", "password", "credentials", "secret", "site binding"],
        summary: "Card details and passwords Remedy can fill without ever seeing them — handles in context, plaintext only machine-side.",
    },
    Meta {
        id: "24-telephony",
        title: "Telephony (bench)",
        category: "Reference",
        order: 17.0,
        tags: &["phone", "call", "voice", "sip", "bluetooth", "telephony", "bench"],
        summary: "Phase 0: a voice measured against a human bar on a simulated line. No hardware, no number, nobody called.",
    },
    Meta {
        id: "25-coordination",
        title: "Coordination (multi-session)",
        category: "Reference",
        order: 17.5,
        tags: &["coordination", "sessions", "claims", "beacon", "multi-session", "lock"],
        summary: "Several Remedy sessions on one repo without overwriting each other: per-path claims that lapse when a session dies.",
    },
    Meta {
        id: "26-game-dev",
        title: "Game dev (studio)",
        category: "Daily use",
        order: 6.8,
        tags: &["game", "godot", "unity", "unreal", "phaser", "bevy", "pygame", "love2d", "playtest", "mcp"],
        summary: "Godot, web, Bevy, Pygame and Love2D projects: engine detection, headless verification, playtesting, export, and the optional live-editor MCP bridge.",
    },
    Meta {
        id: "27-research",
        title: "Research (papers & analysis)",
        category: "Daily use",
        order: 6.85,
        tags: &[
            "research",
            "paper",
            "citation",
            "notebook",
            "statistics",
            "power",
            "manuscript",
            "literature",
            "reproducibility",
        ],
        summary: "Notebooks, R, Julia, manuscripts: literature search, a citation library that has to resolve, analysis in your environment with a run ledger, power and effect sizes, reporting checklists.",
    },
    Meta {
        id: "28-hive",
        title: "Hive (daughters)",
        category: "How it works",
        order: 7.7,
        tags: &["hive", "subagent", "forager", "post", "mother", "diagnostics"],
        summary: "Silent daughters Remedy hires for independent work. They report packets back to Remedy, never as extra chats. Standing posts survive Stop and restart.",
    },
    Meta {
        id: "29-remedy-connect",
        title: "RemedyConnect (phone remote)",
        category: "Daily use",
        order: 4.55,
        tags: &["connect", "phone", "remote", "qr", "tailscale", "lan"],
        summary: "Phone remote for this PC. Default off. Pair with a 60-second QR; Tailscale makes mobile data work anywhere.",
    },
    Meta {
        id: "21-personal-assistant",
        title: "Personal assistant",
        category: "Daily use",
        order: 6.5,
        tags: &[
            "reminders",
            "mail",
            "imap",
            "app password",
            "calendar",
            "caldav",
            "budget",
            "bills",
            "debt",
            "documents",
        ],
        summary: "Reminders that fire, mail and calendar with an app password (no cloud project), money organization, and paperwork intake.",
    },
];

fn body_for(id: &str) -> &'static str {
    match id {
        "00-overview" => include_str!("../../help/articles/00-overview.md"),
        "01-install-linux" => include_str!("../../help/articles/01-install-linux.md"),
        "01-install-windows" => include_str!("../../help/articles/01-install-windows.md"),
        "02-first-run" => include_str!("../../help/articles/02-first-run.md"),
        "03-providers-and-auth" => include_str!("../../help/articles/03-providers-and-auth.md"),
        "04-security-and-data" => include_str!("../../help/articles/04-security-and-data.md"),
        "05-chat-and-sessions" => include_str!("../../help/articles/05-chat-and-sessions.md"),
        "06-memory-and-harness" => include_str!("../../help/articles/06-memory-and-harness.md"),
        "07-skills" => include_str!("../../help/articles/07-skills.md"),
        "08-updates-and-uninstall" => include_str!("../../help/articles/08-updates-and-uninstall.md"),
        "09-troubleshooting" => include_str!("../../help/articles/09-troubleshooting.md"),
        "10-cli-and-api" => include_str!("../../help/articles/10-cli-and-api.md"),
        "11-reference-commands" => include_str!("../../help/articles/11-reference-commands.md"),
        "12-reference-shortcuts" => include_str!("../../help/articles/12-reference-shortcuts.md"),
        "13-whats-new" => include_str!("../../help/articles/13-whats-new.md"),
        "14-visual-decoder" => include_str!("../../help/articles/14-visual-decoder.md"),
        "15-free-providers" => include_str!("../../help/articles/15-free-providers.md"),
        "16-continuity-philosophy" => include_str!("../../help/articles/16-continuity-philosophy.md"),
        "17-nanoswarm" => include_str!("../../help/articles/17-nanoswarm.md"),
        "18-agency" => include_str!("../../help/articles/18-agency.md"),
        "19-metabolism" => include_str!("../../help/articles/19-metabolism.md"),
        "20-rmb-local-agent" => include_str!("../../help/articles/20-rmb-local-agent.md"),
        "21-personal-assistant" => include_str!("../../help/articles/21-personal-assistant.md"),
        "22-grove" => include_str!("../../help/articles/22-grove.md"),
        "23-vault" => include_str!("../../help/articles/23-vault.md"),
        "24-telephony" => include_str!("../../help/articles/24-telephony.md"),
        "25-coordination" => include_str!("../../help/articles/25-coordination.md"),
        "26-game-dev" => include_str!("../../help/articles/26-game-dev.md"),
        "27-research" => include_str!("../../help/articles/27-research.md"),
        "28-hive" => include_str!("../../help/articles/28-hive.md"),
        "29-remedy-connect" => include_str!("../../help/articles/29-remedy-connect.md"),
        "computer-use-soak" => include_str!("../../help/articles/computer-use-soak.md"),
        _ => "",
    }
}

fn build() -> Vec<HelpArticle> {
    META.iter()
        .map(|meta| {
            let raw = body_for(meta.id);
            let body = if raw.is_empty() {
                format!(
                    "# {id}\n\n_Article body missing from bundle. Reinstall or rebuild the desktop app._\n",
                    id = meta.id
                )
            } else {
                raw.to_string()
            };
            HelpArticle {
                id: meta.id,
                title: meta.title,
                category: meta.category,
                order: meta.order,
                tags: meta.tags,
                summary: meta.summary,
                body,
            }
        })
        .collect()
}

/// Every chapter, catalog order, bodies included.
pub(crate) fn articles() -> &'static [HelpArticle] {
    static ARTICLES: OnceLock<Vec<HelpArticle>> = OnceLock::new();
    ARTICLES.get_or_init(build).as_slice()
}

/// The chapter `id` names, or one whose id contains `id`.
///
/// Strips a leading slash and a trailing `.md`, matching the Tauri catalog.
pub(crate) fn get_article(id: &str) -> Option<&'static HelpArticle> {
    if id.is_empty() {
        return None;
    }
    let normalized = id.trim().trim_start_matches('/').trim_end_matches(".md");
    let normalized = normalized.strip_suffix(".MD").unwrap_or(normalized);
    articles()
        .iter()
        .find(|article| article.id == normalized)
        .or_else(|| articles().iter().find(|article| article.id.ends_with(normalized)))
        .or_else(|| articles().iter().find(|article| normalized.ends_with(article.id)))
}

/// Wiki hrefs (`02-first-run`, `./09-troubleshooting.md`, `article:04-security-and-data`).
///
/// External URLs, mailto, and in-page `#anchors` return none so the caller can
/// handle them itself.
pub(crate) fn resolve_wiki_href(href: &str) -> Option<&'static str> {
    let mut h = href.trim();
    if h.is_empty() {
        return None;
    }
    if let Some(rest) = h.strip_prefix("article:") {
        h = rest;
    }
    let lower = h.to_ascii_lowercase();
    if lower.starts_with("http://")
        || lower.starts_with("https://")
        || lower.starts_with("mailto:")
        || h.starts_with('#')
    {
        return None;
    }
    h = h.strip_prefix("./").unwrap_or(h);
    h = h.split(['?', '#']).next().unwrap_or(h);
    if let Some(stripped) = h.strip_suffix(".md").or_else(|| h.strip_suffix(".MD")) {
        h = stripped;
    }
    get_article(h).map(|article| article.id)
}

const MANUAL_BASE: &str = "https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/manual/";

/// Where a non-wiki link goes.
///
/// `http`, `https`, and `mailto` pass through. Any other scheme (`javascript:`,
/// `file:`) is dropped. A repository-relative path is resolved against the
/// manual on GitHub so it does not become a localhost route.
pub(crate) fn resolve_external_help_href(href: Option<&str>) -> Option<String> {
    let href = href?.trim();
    if href.is_empty() {
        return None;
    }
    let lower = href.to_ascii_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") || lower.starts_with("mailto:") {
        return Some(href.to_string());
    }
    if has_scheme(href) || href.starts_with("//") {
        return None;
    }
    Some(resolve_against(MANUAL_BASE, href))
}

fn has_scheme(href: &str) -> bool {
    let Some(colon) = href.find(':') else {
        return false;
    };
    let scheme = &href[..colon];
    let mut chars = scheme.chars();
    let Some(first) = chars.next() else {
        return false;
    };
    first.is_ascii_alphabetic()
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '+' || c == '.' || c == '-')
}

fn resolve_against(base: &str, href: &str) -> String {
    let cut = href.find(['?', '#']).unwrap_or(href.len());
    let path_part = &href[..cut];
    let suffix = &href[cut..];
    let scheme_end = base.find("://").map(|i| i + 3).unwrap_or(0);
    let path_start = base[scheme_end..]
        .find('/')
        .map(|i| i + scheme_end)
        .unwrap_or(base.len());
    let origin = &base[..path_start];
    let base_path = &base[path_start..];
    let mut segs: Vec<&str> = if path_part.starts_with('/') {
        Vec::new()
    } else {
        base_path.split('/').filter(|s| !s.is_empty()).collect()
    };
    for seg in path_part.split('/') {
        if seg.is_empty() || seg == "." {
            continue;
        }
        if seg == ".." {
            segs.pop();
            continue;
        }
        segs.push(seg);
    }
    let mut out = format!("{origin}/{}", segs.join("/"));
    out.push_str(suffix);
    out
}

/// Sidebar groups in the order the catalog first mentions them.
pub(crate) struct CategoryGroup {
    pub category: &'static str,
    pub articles: Vec<&'static HelpArticle>,
}

pub(crate) fn articles_by_category() -> Vec<CategoryGroup> {
    let mut order: Vec<&str> = Vec::new();
    let mut groups: Vec<CategoryGroup> = Vec::new();
    for article in articles() {
        if let Some(group) = groups.iter_mut().find(|group| group.category == article.category) {
            group.articles.push(article);
        } else {
            order.push(article.category);
            groups.push(CategoryGroup {
                category: article.category,
                articles: vec![article],
            });
        }
    }
    let _ = order;
    for group in &mut groups {
        group.articles.sort_by(|a, b| a.order.total_cmp(&b.order));
    }
    groups
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundles_every_chapter() {
        assert!(articles().len() >= 16);
        assert_eq!(articles().len(), META.len());
        for article in articles() {
            assert!(article.body.len() > 80, "{}", article.id);
            assert!(article.body.starts_with("# "), "{}", article.id);
        }
    }

    #[test]
    fn resolves_wiki_hrefs() {
        assert_eq!(resolve_wiki_href("02-first-run"), Some("02-first-run"));
        assert_eq!(resolve_wiki_href("./09-troubleshooting.md"), Some("09-troubleshooting"));
        assert_eq!(resolve_wiki_href("article:04-security-and-data"), Some("04-security-and-data"));
        assert_eq!(resolve_wiki_href("https://example.com"), None);
        assert_eq!(resolve_wiki_href("#about"), None);
        assert!(get_article("11-reference-commands").unwrap().title.to_lowercase().contains("command"));
    }

    #[test]
    fn resolves_repository_links_away_from_the_local_api() {
        assert_eq!(
            resolve_external_help_href(Some("../DESKTOP.md")).as_deref(),
            Some("https://github.com/AhmiDarrow/RemedyAI/blob/master/docs/DESKTOP.md")
        );
        assert_eq!(
            resolve_external_help_href(Some("../../README.md#usage")).as_deref(),
            Some("https://github.com/AhmiDarrow/RemedyAI/blob/master/README.md#usage")
        );
        assert_eq!(
            resolve_external_help_href(Some("https://example.com/help")).as_deref(),
            Some("https://example.com/help")
        );
        assert_eq!(resolve_external_help_href(Some("javascript:alert(1)")), None);
        assert_eq!(resolve_external_help_href(Some("file:///private")), None);
    }

    #[test]
    fn groups_in_first_seen_order() {
        let groups = articles_by_category();
        let names: Vec<&str> = groups.iter().map(|g| g.category).collect();
        assert_eq!(
            names,
            vec![
                "Start here",
                "Reference",
                "Build",
                "How it works",
                "Configuration",
                "Daily use",
                "Maintenance",
            ]
        );
        let start = &groups[0].articles;
        assert_eq!(start[0].id, "00-overview");
        assert_eq!(start[1].id, "16-continuity-philosophy");
        let daily = groups.iter().find(|g| g.category == "Daily use").unwrap();
        assert_eq!(daily.articles.first().unwrap().id, "22-grove");
        assert_eq!(daily.articles.last().unwrap().id, "07-skills");
    }
}
