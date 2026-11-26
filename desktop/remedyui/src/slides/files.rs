//! Project file list. Same actions as `FilesSlide.tsx`: open a folder, open or
//! copy a file, filter, and keyboard navigation. One click opens.

use super::api;
use super::hostcmd;
use super::nav;
use super::paths::{absolute_file_path, encode_query, up_target};
use crate::runtime::Client;
use remedy_ui::a11y::{Action, Role, Semantics};
use remedy_ui::core::Color;
use remedy_ui::input::{Key, KeyEvent};
use remedy_ui::layout::{Align, Justify, Length};
use remedy_ui::net::Request;
use remedy_ui::reactive::Signal;
use remedy_ui::style::{BorderEdge, BorderSide, StylePatch};
use remedy_ui::widgets as w;
use remedy_ui::{El, Ui, show};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SortMode {
    FoldersName,
    Name,
    NameDesc,
}

impl SortMode {
    fn next(self) -> Self {
        match self {
            Self::FoldersName => Self::Name,
            Self::Name => Self::NameDesc,
            Self::NameDesc => Self::FoldersName,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::FoldersName => "Folders",
            Self::Name => "A–Z",
            Self::NameDesc => "Z–A",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct FileEntry {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
    pub depth: u32,
}

#[derive(Clone)]
pub struct FilesModel {
    pub session_id: Signal<Option<String>>,
    pub client: Signal<Option<Client>>,
    pub preview: bool,
    pub root: Signal<String>,
    pub path: Signal<String>,
    pub files: Signal<Vec<FileEntry>>,
    pub error: Signal<String>,
    pub loading: Signal<bool>,
    pub status: Signal<String>,
    pub filter: Signal<String>,
    pub focus: Signal<i32>,
    /// Starting points the access scope allows (name, path): the project, the user's
    /// folders, drives.
    places: Signal<Vec<(String, String)>>,
    sort: Signal<SortMode>,
    expanded: Signal<Vec<String>>,
    children: Signal<Vec<(String, Vec<FileEntry>)>>,
    history: Rc<RefCell<Vec<String>>>,
    hist_at: Rc<Cell<usize>>,
    nav_gen: Signal<u64>,
    generation: Rc<Cell<u64>>,
    status_timer: Rc<Cell<u64>>,
    host: Rc<RefCell<Option<El>>>,
    list_el: Rc<RefCell<Option<El>>>,
}

impl FilesModel {
    pub fn new(
        session_id: Signal<Option<String>>,
        client: Signal<Option<Client>>,
        preview: bool,
    ) -> Self {
        let path = if preview {
            "desktop/src".to_string()
        } else {
            ".".to_string()
        };
        Self {
            session_id,
            client,
            preview,
            root: Signal::new(if preview {
                "/home/ahmi/RemedyAI".into()
            } else {
                String::new()
            }),
            path: Signal::new(path),
            files: Signal::new(if preview {
                preview_entries("desktop/src")
            } else {
                Vec::new()
            }),
            error: Signal::new(String::new()),
            loading: Signal::new(false),
            status: Signal::new(String::new()),
            filter: Signal::new(String::new()),
            focus: Signal::new(-1),
            places: Signal::new(Vec::new()),
            sort: Signal::new(SortMode::FoldersName),
            expanded: Signal::new(Vec::new()),
            children: Signal::new(Vec::new()),
            history: Rc::new(RefCell::new(Vec::new())),
            hist_at: Rc::new(Cell::new(0)),
            nav_gen: Signal::new(0),
            generation: Rc::new(Cell::new(0)),
            status_timer: Rc::new(Cell::new(0)),
            host: Rc::new(RefCell::new(None)),
            list_el: Rc::new(RefCell::new(None)),
        }
    }

    fn bump_nav(&self) {
        self.nav_gen.set(self.nav_gen.get().wrapping_add(1));
    }

    fn sync_history_tip(&self, shown: &str) {
        let mut history = self.history.borrow_mut();
        if history.is_empty() {
            history.push(shown.to_string());
            self.hist_at.set(0);
            self.bump_nav();
            return;
        }
        let at = self.hist_at.get().min(history.len().saturating_sub(1));
        if history[at] != shown {
            history[at] = shown.to_string();
            self.hist_at.set(at);
            self.bump_nav();
        }
    }

    fn can_go_back(&self) -> bool {
        self.hist_at.get() > 0 && !self.history.borrow().is_empty()
    }

    fn can_go_forward(&self) -> bool {
        let history = self.history.borrow();
        !history.is_empty() && self.hist_at.get() + 1 < history.len()
    }

    fn reset_tree(&self) {
        self.expanded.set(Vec::new());
        self.children.set(Vec::new());
    }

    fn go_to(&self, path: &str) {
        let path = if path.trim().is_empty() {
            "."
        } else {
            path.trim()
        }
        .to_string();
        {
            let mut history = self.history.borrow_mut();
            let at = self.hist_at.get();
            if history.get(at).map(String::as_str) != Some(path.as_str()) {
                if !history.is_empty() {
                    history.truncate(at + 1);
                }
                history.push(path.clone());
                self.hist_at.set(history.len() - 1);
                self.bump_nav();
            }
        }
        self.reset_tree();
        self.load(&path);
    }

    fn go_back(&self) {
        if !self.can_go_back() {
            return;
        }
        let next = self.hist_at.get() - 1;
        let path = self.history.borrow().get(next).cloned();
        let Some(path) = path else { return };
        self.hist_at.set(next);
        self.bump_nav();
        self.reset_tree();
        self.load(&path);
    }

    fn go_forward(&self) {
        if !self.can_go_forward() {
            return;
        }
        let next = self.hist_at.get() + 1;
        let path = self.history.borrow().get(next).cloned();
        let Some(path) = path else { return };
        self.hist_at.set(next);
        self.bump_nav();
        self.reset_tree();
        self.load(&path);
    }

    fn up(&self) -> Option<String> {
        up_target(&self.root.get(), &self.path.get())
    }

    fn cycle_sort(&self) {
        self.sort.set(self.sort.get().next());
        self.focus.set(-1);
    }

    fn is_expanded(&self, path: &str) -> bool {
        self.expanded.get().iter().any(|p| p == path)
    }

    fn set_children(&self, path: &str, list: Vec<FileEntry>) {
        let mut cache = self.children.get();
        cache.retain(|(p, _)| p != path);
        cache.push((path.to_string(), list));
        self.children.set(cache);
    }

    fn toggle_expand(&self, path: &str) {
        let mut expanded = self.expanded.get();
        if let Some(i) = expanded.iter().position(|p| p == path) {
            expanded.remove(i);
            self.expanded.set(expanded);
            return;
        }
        expanded.push(path.to_string());
        self.expanded.set(expanded);
        self.ensure_children(path);
    }

    fn ensure_children(&self, path: &str) {
        if self.children.get().iter().any(|(p, _)| p == path) {
            return;
        }
        if self.preview {
            self.set_children(path, preview_entries(path));
            return;
        }
        let Some(client) = self.client.get() else {
            return;
        };
        let generation = self.generation.get();
        let mut q = format!("files?path={}", encode_query(path));
        if let Some(sid) = self.session_id.get().filter(|s| !s.is_empty()) {
            q.push_str("&session_id=");
            q.push_str(&encode_query(&sid));
        }
        let model = self.clone();
        let path = path.to_string();
        api::request(&client, Request::get(&q), move |res| {
            if model.generation.get() != generation {
                return;
            }
            if let Ok(v) = res {
                let list = listing_from(v);
                model.set_children(&path, list);
            }
        });
    }

    fn flash(&self, host: &El, msg: String, ms: u64) {
        flash_status(host, &self.status, &self.status_timer, msg, ms);
    }

    fn note(&self, msg: String) {
        if let Some(host) = self.host.borrow().clone() {
            self.flash(&host, msg, 2800);
        } else {
            self.status.set(msg);
        }
    }

    pub fn load(&self, raw: &str) {
        let requested = if raw.trim().is_empty() {
            "."
        } else {
            raw.trim()
        };
        if self.preview {
            let shown = normalize_preview(requested);
            self.root.set("/home/ahmi/RemedyAI".into());
            self.path.set(shown.clone());
            self.files.set(preview_entries(&shown));
            self.error.set(String::new());
            self.loading.set(false);
            self.focus.set(-1);
            self.sync_history_tip(&shown);
            return;
        }
        let Some(client) = self.client.get() else {
            self.loading.set(false);
            self.files.set(Vec::new());
            self.error.set("not connected".into());
            self.focus.set(-1);
            return;
        };
        let generation = self.generation.get().wrapping_add(1);
        self.generation.set(generation);
        self.loading.set(true);
        self.error.set(String::new());
        self.focus.set(-1);
        let mut q = format!("files?path={}", encode_query(requested));
        if let Some(sid) = self.session_id.get().filter(|s| !s.is_empty()) {
            q.push_str("&session_id=");
            q.push_str(&encode_query(&sid));
        }
        let model = self.clone();
        let requested = requested.to_string();
        api::request(&client, Request::get(&q), move |res| {
            if model.generation.get() != generation {
                return;
            }
            model.loading.set(false);
            match res {
                Ok(v) => apply_listing(&model, &requested, v),
                Err(e) => {
                    model.error.set(if e.is_empty() {
                        "Failed to list files".into()
                    } else {
                        e
                    });
                    model.files.set(Vec::new());
                }
            }
        });
    }
}

fn listing_from(v: serde_json::Value) -> Vec<FileEntry> {
    v.get("files")
        .and_then(|f| f.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|row| {
                    let name = row.get("name")?.as_str()?.to_string();
                    let path = row
                        .get("path")
                        .and_then(|p| p.as_str())
                        .unwrap_or(&name)
                        .to_string();
                    let is_dir = row.get("is_dir").and_then(|d| d.as_bool()).unwrap_or(false);
                    Some(FileEntry {
                        name,
                        path,
                        is_dir,
                        depth: 0,
                    })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

fn apply_listing(model: &FilesModel, requested: &str, v: serde_json::Value) {
    let mut list = listing_from(v.clone());
    sort_entries(&mut list, model.sort.get());
    model.files.set(list);
    let shown = v
        .get("path")
        .and_then(|p| p.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or(requested)
        .to_string();
    model.path.set(shown.clone());
    model.sync_history_tip(&shown);
    if let Some(root) = v
        .get("root")
        .and_then(|r| r.as_str())
        .filter(|s| !s.is_empty())
    {
        model.root.set(root.to_string());
    } else if requested == "." {
        model.root.set(String::new());
    }
    if let Some(places) = v.get("places").and_then(|p| p.as_array()) {
        let places = places
            .iter()
            .filter_map(|p| {
                Some((
                    p.get("name")?.as_str()?.to_string(),
                    p.get("path")?.as_str()?.to_string(),
                ))
            })
            .collect();
        model.places.set(places);
    }
    if v.get("truncated").and_then(|t| t.as_bool()) == Some(true) {
        let shown = model.files.get().len();
        model
            .status
            .set(format!("Showing the first {shown} items. Filter or open a subfolder for the rest."));
    }
    if let Some(err) = v
        .get("error")
        .and_then(|e| e.as_str())
        .filter(|s| !s.is_empty())
    {
        model.error.set(err.to_string());
    }
}

fn sort_entries(list: &mut [FileEntry], mode: SortMode) {
    list.sort_by(|a, b| {
        let names = a.name.to_lowercase().cmp(&b.name.to_lowercase());
        match mode {
            SortMode::FoldersName => b.is_dir.cmp(&a.is_dir).then(names),
            SortMode::Name => names,
            SortMode::NameDesc => names.reverse(),
        }
    });
}

fn normalize_preview(path: &str) -> String {
    let key = path.trim().trim_matches('/');
    if key.is_empty() || key == "." {
        "desktop/src".into()
    } else {
        key.to_string()
    }
}

fn preview_entries(path: &str) -> Vec<FileEntry> {
    let mut list = match path {
        "desktop/src/components" => vec![
            dir("slides", "desktop/src/components/slides"),
            file("FilesSlide.tsx", "desktop/src/components/FilesSlide.tsx"),
            file(
                "TerminalSlide.tsx",
                "desktop/src/components/TerminalSlide.tsx",
            ),
        ],
        "desktop/src/components/slides" => vec![file(
            "FilesSlide.tsx",
            "desktop/src/components/slides/FilesSlide.tsx",
        )],
        "desktop" => vec![dir("src", "desktop/src")],
        "desktop/src" => vec![
            dir("components", "desktop/src/components"),
            file("App.tsx", "desktop/src/App.tsx"),
            file("themes.ts", "desktop/src/themes.ts"),
        ],
        _ => Vec::new(),
    };
    sort_entries(&mut list, SortMode::FoldersName);
    list
}

fn dir(name: &str, path: &str) -> FileEntry {
    FileEntry {
        name: name.into(),
        path: path.into(),
        is_dir: true,
        depth: 0,
    }
}

fn file(name: &str, path: &str) -> FileEntry {
    FileEntry {
        name: name.into(),
        path: path.into(),
        is_dir: false,
        depth: 0,
    }
}

pub(crate) fn flash_status(
    host: &El,
    status: &Signal<String>,
    timer: &Rc<Cell<u64>>,
    msg: String,
    ms: u64,
) {
    status.set(msg.clone());
    let prev = timer.get();
    if prev != 0 {
        host.ui().cancel_timer(prev);
        timer.set(0);
    }
    if msg.is_empty() {
        return;
    }
    let status = status.clone();
    let timer_done = timer.clone();
    let id = host.ui().after(Duration::from_millis(ms), move || {
        status.set(String::new());
        timer_done.set(0);
    });
    timer.set(id);
}

fn visible_of(files: &[FileEntry], filter: &str) -> Vec<FileEntry> {
    visible_rows_from(files, filter, SortMode::FoldersName, &[], &[])
}

fn visible_rows(model: &FilesModel) -> Vec<FileEntry> {
    visible_rows_from(
        &model.files.get(),
        &model.filter.get(),
        model.sort.get(),
        &model.expanded.get(),
        &model.children.get(),
    )
}

fn visible_rows_from(
    files: &[FileEntry],
    filter: &str,
    mode: SortMode,
    expanded: &[String],
    children: &[(String, Vec<FileEntry>)],
) -> Vec<FileEntry> {
    let q = filter.trim().to_lowercase();
    let mut out = Vec::new();
    fn walk(
        entries: &[FileEntry],
        depth: u32,
        q: &str,
        mode: SortMode,
        expanded: &[String],
        children: &[(String, Vec<FileEntry>)],
        out: &mut Vec<FileEntry>,
    ) {
        let mut list = entries.to_vec();
        sort_entries(&mut list, mode);
        for mut entry in list {
            let matches = q.is_empty() || entry.name.to_lowercase().contains(q);
            if !matches {
                continue;
            }
            entry.depth = depth;
            let path = entry.path.clone();
            let is_dir = entry.is_dir;
            out.push(entry);
            if q.is_empty() && is_dir && expanded.iter().any(|p| p == &path) {
                if let Some((_, kids)) = children.iter().find(|(p, _)| p == &path) {
                    walk(kids, depth + 1, q, mode, expanded, children, out);
                }
            }
        }
    }
    walk(files, 0, &q, mode, expanded, children, &mut out);
    out
}

fn breadcrumb_parts(root: &str, path: &str) -> Vec<(String, String)> {
    let mut parts = Vec::new();
    let path_norm = path
        .replace('\\', "/")
        .trim()
        .trim_end_matches('/')
        .to_string();
    let root_norm = root.replace('\\', "/").trim_end_matches('/').to_string();
    let under_root = !root_norm.is_empty()
        && (path_norm == root_norm || path_norm.starts_with(&(root_norm.clone() + "/")));
    let rel = if under_root {
        path_norm[root_norm.len()..]
            .trim_start_matches('/')
            .to_string()
    } else if path_norm.starts_with('/') || path_norm.chars().nth(1) == Some(':') {
        path_norm.trim_start_matches('/').to_string()
    } else {
        path_norm.trim_start_matches("./").to_string()
    };
    // The project root is always "." (the files API's session root). An absolute
    // root path would miss the preview fixture and surprise the live listing.
    if !root.is_empty() {
        parts.push((root_label(root), ".".into()));
    } else if rel.is_empty() || rel == "." {
        parts.push(("Project".into(), ".".into()));
    }
    let mut acc = String::new();
    if under_root {
        acc = String::new();
    } else if path_norm.starts_with('/') {
        acc = String::new();
    } else if path_norm.chars().nth(1) == Some(':') {
        // Keep "C:" as the first accumulative root for a Windows absolute path.
        acc = String::new();
    }
    let sep = if root.contains('\\') && !root.contains('/')
        || path.contains('\\') && !path.contains('/')
    {
        '\\'
    } else {
        '/'
    };
    for seg in rel
        .split(['/', '\\'])
        .filter(|s| !s.is_empty() && *s != ".")
    {
        acc = if acc.is_empty() {
            if path_norm.starts_with('/') && !under_root {
                format!("/{seg}")
            } else if !under_root && seg.len() == 2 && seg.ends_with(':') {
                // "C:" alone means the drive's current folder, not its root.
                format!("{seg}/")
            } else {
                seg.to_string()
            }
        } else {
            format!("{}{sep}{seg}", acc.trim_end_matches(['/', '\\']))
        };
        parts.push((seg.to_string(), acc.clone()));
    }
    parts
}

pub fn body(ui: &Ui, model: &FilesModel) -> El {
    let unit = ui.theme().spacing.unit;
    let colors = ui.theme().colors.clone();
    let line = colors.line.unwrap_or_else(|| colors.muted.with_alpha(0.45));
    let root = w::column(ui)
        .gap(0.0)
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.height = Length::Percent(1.0);
            l.min_height = Length::Px(0.0);
        })
        .style(
            StylePatch::new()
                .background(colors.panel)
                .foreground(colors.text),
        );
    *model.host.borrow_mut() = Some(root.clone());

    let model_load = model.clone();
    let seen: Rc<RefCell<Option<Option<String>>>> = Rc::new(RefCell::new(None));
    let root_for_load = root.clone();
    let _ = root.clone().bind(move || {
        let sid = model_load.session_id.get();
        if seen.borrow().as_ref() == Some(&sid) {
            return;
        }
        *seen.borrow_mut() = Some(sid);
        let pending = nav::take_pending_files_path();
        model_load.filter.set(String::new());
        model_load.status.set(String::new());
        model_load.focus.set(-1);
        if !model_load.preview {
            model_load.root.set(String::new());
            model_load.files.set(Vec::new());
        }
        let start = pending.unwrap_or_else(|| {
            if model_load.preview {
                "desktop/src".into()
            } else {
                ".".into()
            }
        });
        model_load.load(&start);
        let _ = &root_for_load;
    });

    let model_nav = model.clone();
    let guard = nav::on_files_path(move |folder| {
        let _ = nav::take_pending_files_path();
        model_nav.filter.set(String::new());
        model_nav.go_to(folder);
    });
    let _ = root.clone().on_removed(move || drop(guard));

    let visible: Signal<Vec<FileEntry>> = Signal::new(visible_rows(model));
    let last_visible: Rc<RefCell<Vec<FileEntry>>> = Rc::new(RefCell::new(visible.get()));
    let model_vis = model.clone();
    let visible_w = visible.clone();
    let _ = root.clone().bind(move || {
        let next = visible_rows(&model_vis);
        if *last_visible.borrow() != next {
            *last_visible.borrow_mut() = next.clone();
            visible_w.set(next);
        }
    });

    root.child(header(ui, model, unit, &colors, line))
        .child(w::divider(ui, false))
        .child(path_row(ui, model, unit, &colors, line))
        .child(w::divider(ui, false))
        .child(filter_row(ui, model, unit, &colors, line, visible.clone()))
        .child(status_line(ui, model, unit))
        .child(list_area(ui, model, &colors, visible))
        .child(w::divider(ui, false))
        .child(footer(ui, model, unit, &colors))
}

fn header(
    ui: &Ui,
    model: &FilesModel,
    unit: f32,
    colors: &remedy_ui::style::ColorRoles,
    line: Color,
) -> El {
    let m = model.clone();
    let label = w::button(
        ui,
        {
            let m = m.clone();
            move || root_label(&m.root.get())
        },
        {
            let m = m.clone();
            let ui = ui.clone();
            move || {
                let root = m.root.get();
                if root.is_empty() {
                    return;
                }
                ui.copy_text(root.clone());
                m.note("Copied project root".into());
            }
        },
    )
    .variant("quiet");
    w::tooltip(&label, {
        let m = m.clone();
        move || {
            let root = m.root.get();
            if root.is_empty() {
                "No project path on this session".into()
            } else {
                format!("{root} — click to copy")
            }
        }
    });
    let _ = label.clone().semantics(
        Semantics::new(Role::Button)
            .label("Copy project root")
            .action(Action::Click),
    );
    w::row(ui)
        .align(Align::Center)
        .gap(unit)
        .padding(pad(unit, unit * 2.0))
        .style(StylePatch::new().border_side(BorderEdge::Bottom, BorderSide::new(0.0, line)))
        .child(label.layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        }))
        .child(
            show(
                ui,
                {
                    let m = model.clone();
                    move || m.loading.get()
                },
                {
                    let ui = ui.clone();
                    let accent = colors.accent;
                    move || w::text(&ui, "…").style(StylePatch::new().foreground(accent))
                },
            )
            .el(),
        )
}

fn root_label(root: &str) -> String {
    if root.is_empty() {
        "Project files".into()
    } else {
        let trimmed = root.trim_end_matches(['/', '\\']);
        trimmed
            .rsplit(['/', '\\'])
            .next()
            .unwrap_or(root)
            .to_string()
    }
}

fn path_row(
    ui: &Ui,
    model: &FilesModel,
    unit: f32,
    colors: &remedy_ui::style::ColorRoles,
    _line: Color,
) -> El {
    let back = glyph_button(ui, "←", "Back", {
        let m = model.clone();
        move || m.go_back()
    })
    .disabled({
        let m = model.clone();
        move || {
            let _ = m.nav_gen.get();
            !m.can_go_back() || m.loading.get()
        }
    });
    w::tooltip(&back, "Back (Alt+Left)");

    let forward = glyph_button(ui, "→", "Forward", {
        let m = model.clone();
        move || m.go_forward()
    })
    .disabled({
        let m = model.clone();
        move || {
            let _ = m.nav_gen.get();
            !m.can_go_forward() || m.loading.get()
        }
    });
    w::tooltip(&forward, "Forward (Alt+Right)");

    let up = glyph_button(ui, "↑", "Parent folder", {
        let m = model.clone();
        move || {
            if let Some(parent) = m.up() {
                m.go_to(&parent);
            }
        }
    })
    .disabled({
        let m = model.clone();
        move || m.up().is_none() || m.loading.get()
    });
    w::tooltip(&up, "Parent folder (Backspace)");

    let field = w::text_input(ui, "Folder path");
    let writing = Rc::new(Cell::new(false));
    let f_change = field.clone();
    let m = model.clone();
    let field = field.on_change({
        let writing = writing.clone();
        move || {
            if writing.get() {
                return;
            }
            m.path.set(f_change.value());
        }
    });
    let f_submit = field.clone();
    let m = model.clone();
    let field = field.on_submit(move || {
        let path = f_submit.value();
        m.go_to(if path.is_empty() { "." } else { &path });
    });
    let f_el = field.el();
    let f_blur = field.clone();
    let _ = f_el.clone().on_key_capture(move |e| {
        if e.pressed && !e.composing && e.key == Key::Escape {
            f_blur.el().blur();
            return true;
        }
        false
    });
    let f_bind = field.clone();
    let m = model.clone();
    let writing2 = writing.clone();
    let _ = f_el.clone().bind(move || {
        let path = m.path.get();
        if f_bind.value() != path {
            writing2.set(true);
            f_bind.set_value(&path);
            writing2.set(false);
        }
    });

    let refresh = glyph_button(ui, "↻", "Refresh", {
        let m = model.clone();
        let f = field.clone();
        move || {
            let path = f.value();
            m.load(if path.is_empty() { "." } else { &path });
        }
    })
    .disabled({
        let m = model.clone();
        move || m.loading.get()
    });
    w::tooltip(&refresh, "Refresh");

    let crumbs = w::row(ui)
        .align(Align::Center)
        .gap(2.0)
        .scroll_x()
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        });
    let crumb_host = crumbs.clone();
    let crumb_ui = ui.clone();
    let crumb_model = model.clone();
    let crumb_colors = colors.clone();
    let crumb_bind = crumb_host.clone();
    let _ = crumbs.clone().bind(move || {
        crumb_bind.clear();
        let parts = breadcrumb_parts(&crumb_model.root.get(), &crumb_model.path.get());
        let last = parts.len().saturating_sub(1);
        for (i, (label, target)) in parts.into_iter().enumerate() {
            if i > 0 {
                let _ = crumb_bind.clone().child(
                    w::text(&crumb_ui, "/")
                        .variant("muted")
                        .style(StylePatch::new().foreground(crumb_colors.muted)),
                );
            }
            let m = crumb_model.clone();
            let dest = target.clone();
            let name = label.clone();
            let btn = w::button(&crumb_ui, label, move || m.go_to(&dest)).variant("quiet");
            let _ = btn.clone().semantics(
                Semantics::new(Role::Button)
                    .label(if i == last {
                        format!("Current folder {name}")
                    } else {
                        format!("Open {name}")
                    })
                    .action(Action::Click),
            );
            if i == last {
                let _ = btn
                    .clone()
                    .style(StylePatch::new().foreground(crumb_colors.text));
            }
            let _ = crumb_bind.clone().child(btn);
        }
    });

    let sort = w::button(
        ui,
        {
            let m = model.clone();
            move || format!("Sort: {}", m.sort.get().label())
        },
        {
            let m = model.clone();
            move || m.cycle_sort()
        },
    )
    .variant("quiet");
    w::tooltip(&sort, "Cycle sort: folders first, A–Z, Z–A");

    let crumbs = crumb_host.clip().layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_width = Length::Px(0.0);
    });
    let sort_model = model.clone();
    w::column(ui)
        .gap(0.0)
        .child(
            w::overflow_row(ui)
                .gap(unit)
                .keep(back)
                .keep(forward)
                .keep(up)
                .flex(crumbs)
                .child("Refresh", refresh)
                .child_fn(
                    move || format!("Sort: {}", sort_model.sort.get().label()),
                    sort,
                )
                .el()
                .padding(pad(unit * 0.5, unit * 2.0))
                .style(StylePatch::new().foreground(colors.text))
                .layout(|l| {
                    l.width = Length::Percent(1.0);
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child(
            w::row(ui)
                .align(Align::Center)
                .gap(unit)
                .padding(pad(0.0, unit * 2.0))
                .child(f_el.layout(|l| {
                    l.grow = 1.0;
                    l.basis = Length::Px(0.0);
                    l.min_width = Length::Px(0.0);
                })),
        )
        .child(places_row(ui, model, unit))
}

/// One button per starting point (Project, Desktop, Documents, drives …), scrolled
/// sideways when they do not fit.
fn places_row(ui: &Ui, model: &FilesModel, unit: f32) -> El {
    let row = w::row(ui)
        .align(Align::Center)
        .gap(2.0)
        .scroll_x()
        .padding(pad(0.0, unit * 2.0))
        .layout(|l| {
            l.width = Length::Percent(1.0);
            l.min_width = Length::Px(0.0);
        });
    let host = row.clone();
    let ui = ui.clone();
    let m = model.clone();
    let _ = row.clone().bind(move || {
        host.clear();
        for (name, path) in m.places.get() {
            let go = m.clone();
            let label = format!("Go to {name}");
            let btn = w::button(&ui, name, move || {
                go.filter.set(String::new());
                go.go_to(&path);
            })
            .variant("quiet");
            let _ = btn
                .clone()
                .semantics(Semantics::new(Role::Button).label(label).action(Action::Click));
            let _ = host.clone().child(btn);
        }
    });
    row
}

fn filter_row(
    ui: &Ui,
    model: &FilesModel,
    unit: f32,
    _colors: &remedy_ui::style::ColorRoles,
    _line: Color,
    visible: Signal<Vec<FileEntry>>,
) -> El {
    let field = w::text_input(ui, "Filter files").placeholder("Filter in folder…");
    let writing = Rc::new(Cell::new(false));
    let f_change = field.clone();
    let m = model.clone();
    let field = field.on_change({
        let writing = writing.clone();
        move || {
            if writing.get() {
                return;
            }
            let v = f_change.value();
            m.filter.set(v);
            m.focus.set(0);
        }
    });
    let f_bind = field.clone();
    let m = model.clone();
    let writing2 = writing.clone();
    let fel = field.el();
    let _ = fel.clone().bind(move || {
        let v = m.filter.get();
        if f_bind.value() != v {
            writing2.set(true);
            f_bind.set_value(&v);
            writing2.set(false);
        }
    });
    let m = model.clone();
    let vis = visible.clone();
    let field_for_key = field.clone();
    let _ = fel.clone().on_key_capture(move |e| {
        if !e.pressed || e.composing {
            return false;
        }
        if e.key == Key::Escape && !m.filter.get().is_empty() {
            m.filter.set(String::new());
            m.focus.set(-1);
            return true;
        }
        if e.key == Key::ArrowDown || e.key == Key::Enter {
            m.focus.set(0);
            if e.key == Key::ArrowDown {
                if let Some(list) = m.list_el.borrow().clone() {
                    list.focus();
                }
            }
            if e.key == Key::Enter {
                if let Some(first) = vis.get().first().cloned() {
                    let el = field_for_key.el();
                    let ui = el.ui().clone();
                    open_entry(&ui, &m, &first);
                }
            }
            return true;
        }
        false
    });
    w::row(ui)
        .padding(pad(unit, unit * 2.0))
        .child(fel.layout(|l| l.width = Length::Percent(1.0)))
}

fn status_line(ui: &Ui, model: &FilesModel, unit: f32) -> El {
    let m = model.clone();
    let colors = ui.theme().colors.clone();
    show(ui, move || !m.status.get().is_empty(), {
        let ui = ui.clone();
        let m = model.clone();
        move || {
            let text = w::text(&ui, "").variant("muted").text({
                let m = m.clone();
                move || m.status.get()
            });
            let _ = text.clone().semantics(Semantics::new(Role::Status).label({
                // label is static here; the text itself is the status.
                "Files status".to_string()
            }));
            w::row(&ui)
                .padding(pad(unit * 0.5, unit * 2.0))
                .child(text.style(StylePatch::new().foreground(colors.muted)))
        }
    })
    .el()
}

fn list_area(
    ui: &Ui,
    model: &FilesModel,
    colors: &remedy_ui::style::ColorRoles,
    visible: Signal<Vec<FileEntry>>,
) -> El {
    let host = w::column(ui).layout(|l| {
        l.grow = 1.0;
        l.basis = Length::Px(0.0);
        l.min_height = Length::Px(0.0);
        l.width = Length::Percent(1.0);
    });
    let list = ui
        .el("files_list")
        .column()
        .scroll()
        .focusable(true)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_height = Length::Px(0.0);
            l.width = Length::Percent(1.0);
        })
        .semantics(Semantics::new(Role::ListBox).label("Files"));
    *model.list_el.borrow_mut() = Some(list.clone());
    let m = model.clone();
    let vis = visible.clone();
    let list_keys = list.clone();
    let _ = list
        .clone()
        .on_key(move |e| on_list_key(&list_keys, &m, &vis, e));

    let rows = list.clone();
    let m = model.clone();
    let ui_rows = ui.clone();
    let key_model = m.clone();
    let _ = w::for_each(
        &rows,
        visible.clone(),
        move |f: &FileEntry| {
            let open = if key_model.is_expanded(&f.path) {
                "open"
            } else {
                "shut"
            };
            format!("{}:{open}", f.path)
        },
        move |f| file_row(&ui_rows, &m, f),
    );

    let m = model.clone();
    host.child(
        show(
            ui,
            {
                let m = m.clone();
                move || m.loading.get() && m.files.get().is_empty() && m.error.get().is_empty()
            },
            {
                let ui = ui.clone();
                let muted = colors.muted;
                move || {
                    w::text(&ui, "Loading…")
                        .variant("muted")
                        .style(StylePatch::new().foreground(muted))
                        .padding(pad(8.0, 8.0))
                }
            },
        )
        .el(),
    )
    .child(
        show(
            ui,
            {
                let m = m.clone();
                move || !m.error.get().is_empty()
            },
            {
                let ui = ui.clone();
                let m = m.clone();
                move || {
                    let err = m.error.get();
                    w::column(&ui)
                        .gap(8.0)
                        .padding(pad(8.0, 8.0))
                        .child(w::empty_state(&ui, "Could not list files", err))
                        .child(w::button(&ui, "Retry", {
                            let m = m.clone();
                            move || {
                                let path = m.path.get();
                                m.load(if path.is_empty() { "." } else { &path });
                            }
                        }))
                }
            },
        )
        .el(),
    )
    .child(
        show(
            ui,
            {
                let m = m.clone();
                move || !m.loading.get() && m.error.get().is_empty() && m.files.get().is_empty()
            },
            {
                let ui = ui.clone();
                let m = m.clone();
                move || {
                    let hint = if m.root.get().is_empty() {
                        "Attach a project to this session to browse files."
                    } else {
                        "This folder is empty."
                    };
                    w::empty_state(&ui, "No files here", hint).padding(pad(8.0, 8.0))
                }
            },
        )
        .el(),
    )
    .child(
        show(
            ui,
            {
                let m = model.clone();
                let vis = visible.clone();
                move || {
                    !m.loading.get()
                        && m.error.get().is_empty()
                        && !m.files.get().is_empty()
                        && vis.get().is_empty()
                }
            },
            {
                let ui = ui.clone();
                let m = model.clone();
                let muted = colors.muted;
                move || {
                    let filter = m.filter.get();
                    w::text(&ui, "")
                        .text(move || format!("No matches for “{filter}”"))
                        .style(StylePatch::new().foreground(muted))
                }
            },
        )
        .el(),
    )
    .child(
        show(
            ui,
            {
                let m = model.clone();
                move || m.error.get().is_empty()
            },
            move || list.clone(),
        )
        .el(),
    )
}

fn on_list_key(
    list: &El,
    model: &FilesModel,
    visible: &Signal<Vec<FileEntry>>,
    e: &KeyEvent,
) -> bool {
    if !e.pressed || e.composing || model.loading.get() {
        return false;
    }
    let rows = visible.get();
    if rows.is_empty() {
        return false;
    }
    let n = rows.len() as i32;
    if e.key == Key::ArrowDown {
        let i = model.focus.get();
        let next = if i < 0 { 0 } else { (i + 1).min(n - 1) };
        model.focus.set(next);
        return true;
    }
    if e.key == Key::ArrowUp {
        let i = model.focus.get();
        let next = if i < 0 { n - 1 } else { (i - 1).max(0) };
        model.focus.set(next);
        return true;
    }
    if e.key == Key::Enter {
        let i = model.focus.get();
        if i >= 0 && (i as usize) < rows.len() {
            open_entry(&list.ui(), model, &rows[i as usize]);
            return true;
        }
    }
    if e.key == Key::Backspace && model.filter.get().is_empty() {
        if let Some(parent) = model.up() {
            model.go_to(&parent);
            return true;
        }
    }
    if e.key == Key::ArrowLeft {
        let i = model.focus.get();
        if i >= 0 {
            if let Some(f) = rows.get(i as usize) {
                if f.is_dir && model.is_expanded(&f.path) {
                    model.toggle_expand(&f.path);
                    return true;
                }
            }
        }
        if let Some(parent) = model.up() {
            model.go_to(&parent);
            return true;
        }
    }
    if e.key == Key::ArrowRight {
        let i = model.focus.get();
        if i >= 0 {
            if let Some(f) = rows.get(i as usize) {
                if f.is_dir && !model.is_expanded(&f.path) {
                    model.toggle_expand(&f.path);
                    return true;
                }
            }
        }
    }
    if e.key == Key::Home {
        model.focus.set(0);
        return true;
    }
    if e.key == Key::End {
        model.focus.set(n - 1);
        return true;
    }
    if e.key == Key::F(5) {
        let path = model.path.get();
        model.load(if path.is_empty() { "." } else { &path });
        return true;
    }
    if e.modifiers.alt && e.key == Key::ArrowLeft {
        model.go_back();
        return true;
    }
    if e.modifiers.alt && e.key == Key::ArrowRight {
        model.go_forward();
        return true;
    }
    if let Key::Character(ch) = &e.key {
        if (e.modifiers.ctrl || e.modifiers.meta)
            && !e.modifiers.shift
            && ch.eq_ignore_ascii_case("c")
        {
            let i = model.focus.get();
            if i >= 0 {
                if let Some(f) = rows.get(i as usize) {
                    if !f.is_dir {
                        copy_path(
                            &list.ui(),
                            model,
                            &absolute_file_path(&model.root.get(), &f.path),
                            "Copied path",
                        );
                        return true;
                    }
                }
            }
        }
    }
    false
}

fn file_row(ui: &Ui, model: &FilesModel, f: &FileEntry) -> El {
    let colors = ui.theme().colors.clone();
    let path = f.path.clone();
    let name = f.name.clone();
    let is_dir = f.is_dir;
    let depth = f.depth;
    let fg = if is_dir { colors.accent } else { colors.muted };
    let chevron = if is_dir {
        let expanded = model.is_expanded(&path);
        let m = model.clone();
        let dest = path.clone();
        let label = if expanded {
            format!("Collapse {name}")
        } else {
            format!("Expand {name}")
        };
        let icon = if expanded {
            w::Icon::ChevronDown
        } else {
            w::Icon::ChevronRight
        };
        let btn = ui
            .el("button")
            .child(w::icon(ui, icon, 12.0))
            .row()
            .align(Align::Center)
            .justify(Justify::Center)
            .layout(|l| {
                l.width = Length::Px(18.0);
                l.min_width = Length::Px(18.0);
            })
            .on_click(move || m.toggle_expand(&dest))
            .semantics(
                Semantics::new(Role::Button)
                    .label(label)
                    .action(Action::Click),
            );
        w::tooltip(
            &btn,
            if expanded {
                "Collapse folder"
            } else {
                "Expand folder"
            },
        );
        btn
    } else {
        ui.el("files-leaf").layout(|l| {
            l.width = Length::Px(18.0);
            l.min_width = Length::Px(18.0);
        })
    };
    let kind = if is_dir {
        w::Icon::Folder
    } else {
        w::Icon::File
    };
    let open = w::row(ui)
        .align(Align::Center)
        .gap(4.0)
        .layout(|l| {
            l.grow = 1.0;
            l.basis = Length::Px(0.0);
            l.min_width = Length::Px(0.0);
        })
        .child(w::icon(ui, kind, 14.0).style(StylePatch::new().foreground(fg)))
        .child(
            w::text(ui, name.clone())
                .style(StylePatch::new().foreground(fg))
                .layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                }),
        );
    let m = model.clone();
    let entry = f.clone();
    let ui_open = ui.clone();
    let open = open.on_click(move || {
        let rows = visible_rows(&m);
        if let Some(i) = rows.iter().position(|r| r.path == entry.path) {
            m.focus.set(i as i32);
        }
        open_entry(&ui_open, &m, &entry);
    });
    let open_name = name.clone();
    let _ = open.clone().semantics(
        Semantics::new(Role::Button)
            .label(open_name)
            .action(Action::Click),
    );
    let focused_path = path.clone();
    let m = model.clone();
    let open2 = open.clone();
    let accent = colors.accent;
    let _ = open.clone().bind(move || {
        let rows = visible_rows(&m);
        let on = m
            .focus
            .get()
            .try_into()
            .ok()
            .and_then(|i: usize| rows.get(i))
            .is_some_and(|r| r.path == focused_path);
        open2.state(remedy_ui::style::States::SELECTED, on);
        let _ = accent;
    });
    let open = open.style({
        let m = model.clone();
        let path = path.clone();
        let accent = colors.accent;
        move || {
            let rows = visible_rows(&m);
            let on = m
                .focus
                .get()
                .try_into()
                .ok()
                .and_then(|i: usize| rows.get(i))
                .is_some_and(|r| r.path == path);
            StylePatch::new().background(if on {
                accent.with_alpha(0.16)
            } else {
                Color::TRANSPARENT
            })
        }
    });

    let copy = glyph_button(ui, "path", format!("Copy path for {name}"), {
        let m = model.clone();
        let ui = ui.clone();
        let full_path = path.clone();
        move || {
            let full = absolute_file_path(&m.root.get(), &full_path);
            copy_path(&ui, &m, &full, "Copied path");
        }
    });
    let reveal = glyph_button(ui, "show", format!("Reveal {name}"), {
        let m = model.clone();
        let ui = ui.clone();
        let entry = f.clone();
        move || reveal_entry(&ui, &m, &entry)
    });
    w::tooltip(&reveal, "Reveal in file manager");
    w::row(ui)
        .align(Align::Center)
        .gap(2.0)
        .padding(remedy_ui::core::Insets {
            top: 2.0,
            bottom: 2.0,
            start: 4.0 + depth as f32 * 12.0,
            end: 4.0,
        })
        .child(chevron)
        .child(open)
        .child(reveal)
        .child(copy)
}

fn open_entry(ui: &Ui, model: &FilesModel, f: &FileEntry) {
    if f.is_dir {
        model.filter.set(String::new());
        model.go_to(&f.path);
        return;
    }
    let full = absolute_file_path(&model.root.get(), &f.path);
    match hostcmd::launch_path(full.clone()) {
        Ok(_) => model.note(format!("Opened {}", f.name)),
        Err(e) => {
            ui.copy_text(full);
            model.note(e);
        }
    }
}

fn reveal_entry(ui: &Ui, model: &FilesModel, f: &FileEntry) {
    let full = absolute_file_path(&model.root.get(), &f.path);
    match hostcmd::open_path(full.clone()) {
        Ok(_) => model.note(format!("Revealed {}", f.name)),
        Err(e) => {
            ui.copy_text(full);
            model.note(e);
        }
    }
}

fn copy_path(ui: &Ui, model: &FilesModel, full: &str, label: &str) {
    ui.copy_text(full.to_string());
    model.note(label.to_string());
}

fn footer(ui: &Ui, model: &FilesModel, unit: f32, colors: &remedy_ui::style::ColorRoles) -> El {
    let m = model.clone();
    w::row(ui)
        .align(Align::Center)
        .justify(Justify::SpaceBetween)
        .gap(unit)
        .padding(pad(unit * 0.5, unit * 2.0))
        .style(StylePatch::new().foreground(colors.muted))
        .child(
            w::text(ui, "↑↓ move · ←→ expand · Enter open · Backspace up")
                .variant("muted")
                .layout(|l| {
                    l.grow = 1.0;
                    l.min_width = Length::Px(0.0);
                }),
        )
        .child(w::text(ui, "").variant("muted").text(move || {
            if m.loading.get() || !m.error.get().is_empty() {
                return String::new();
            }
            let all = m.files.get();
            let vis = visible_of(&all, &m.filter.get());
            if vis.is_empty() {
                return String::new();
            }
            let dirs = vis.iter().filter(|f| f.is_dir).count();
            let files = vis.len() - dirs;
            let mut s = String::new();
            if dirs > 0 {
                s.push_str(&format!("{dirs}d"));
            }
            if dirs > 0 && files > 0 {
                s.push_str(" · ");
            }
            if files > 0 {
                s.push_str(&format!("{files}f"));
            }
            if !m.filter.get().trim().is_empty() && all.len() != vis.len() {
                s.push_str(&format!(" / {}", all.len()));
            }
            s
        }))
}

fn glyph_button(
    ui: &Ui,
    glyph: &str,
    label: impl Into<String>,
    on_press: impl Fn() + 'static,
) -> El {
    let label = label.into();
    let colors = ui.theme().colors.clone();
    ui.el("button")
        .child(w::text(ui, glyph).style(StylePatch::new().foreground(colors.muted)))
        .row()
        .align(Align::Center)
        .justify(Justify::Center)
        .padding(pad(2.0, 6.0))
        .style(
            StylePatch::new()
                .background(colors.bg)
                .radius(ui.theme().radii.small)
                .foreground(colors.muted),
        )
        .on_click(on_press)
        .semantics(
            Semantics::new(Role::Button)
                .label(label)
                .action(Action::Click),
        )
}

fn pad(y: f32, x: f32) -> remedy_ui::core::Insets {
    remedy_ui::core::Insets {
        top: y,
        bottom: y,
        start: x,
        end: x,
    }
}
