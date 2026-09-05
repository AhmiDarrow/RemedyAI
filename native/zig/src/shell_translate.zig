//! POSIX → Windows-cmd rewrite. Zig mirror of `remedy.core.computer.host_binding.translate`.
//! ABI 4: `remedy_core_translate_posix_to_host`. No soft Python fallback.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");

const Status = root.Status;
const Error = host.Error;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);

const chain_ops = [_][]const u8{ "&&", "||", ">>", "2>&1", "2>", "1>", "&>", ">&", "|", ";", "&" };
const redirect_keep = [_][]const u8{ "2>&1", "2>", "1>", ">>", "&>", ">&" };

const read_not_open = [_][]const u8{
    ".md", ".markdown", ".txt", ".rst", ".toml", ".yml", ".yaml", ".log",
    ".ini", ".cfg", ".env", ".json", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".css", ".html", ".htm",
};

const ps_filename_nouns = [_][]const u8{
    "server", "dev", "app", "all", "here", "now", "script", "build", "web", "api",
};

const ps_verbs = [_][]const u8{
    "Get", "Set", "New", "Remove", "Invoke", "Write", "Select", "Where", "ForEach", "Out",
    "Add", "Clear", "ConvertTo", "ConvertFrom", "Import", "Export", "Start", "Stop", "Test", "Measure",
};

const no_py_note = "needs Python — install Python 3 or set REMEDY_PYTHON to python.exe";
const ps_leave_note = "powershell payload — not posix-rewritten";
const untranslatable_note = "untranslatable substitution $(…) / backticks — use host_script";

pub const TranslateResult = struct {
    text: []const u8 = "",
    changed: bool = false,
    notes: []const []const u8 = &.{},
    untranslatable: bool = false,
    noop: bool = false,
};

pub const TranslateOptions = struct {
    host: []const u8 = "",
    rg_path: []const u8 = "",
    python_exe: []const u8 = "",
    pwsh_exe: []const u8 = "",
};

fn defaultHostName() []const u8 {
    return if (builtin.os.tag == .windows) "cmd" else "posix";
}

fn testingIo() std.Io {
    return std.testing.io;
}

fn trimSpace(s: []const u8) []const u8 {
    return std.mem.trim(u8, s, " \t\r\n");
}

fn startsWithOp(command: []const u8, i: usize, op: []const u8) bool {
    return std.mem.startsWith(u8, command[i..], op);
}

fn isRedirectKeep(op: []const u8) bool {
    for (redirect_keep) |k| {
        if (std.mem.eql(u8, k, op)) return true;
    }
    return false;
}

fn isWordBoundaryAfter(s: []const u8, end: usize) bool {
    if (end >= s.len) return true;
    const c = s[end];
    return !(std.ascii.isAlphanumeric(c) or c == '_');
}

fn eqlIgnoreCase(a: []const u8, b: []const u8) bool {
    return std.ascii.eqlIgnoreCase(a, b);
}

fn containsIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (needle.len == 0) return true;
    if (hay.len < needle.len) return false;
    var i: usize = 0;
    while (i + needle.len <= hay.len) : (i += 1) {
        if (eqlIgnoreCase(hay[i .. i + needle.len], needle)) return true;
    }
    return false;
}

fn endsWithIgnoreCase(hay: []const u8, suffix: []const u8) bool {
    if (hay.len < suffix.len) return false;
    return eqlIgnoreCase(hay[hay.len - suffix.len ..], suffix);
}

fn toLowerAlloc(arena: std.mem.Allocator, s: []const u8) error{OutOfMemory}![]u8 {
    const out = try arena.alloc(u8, s.len);
    for (s, 0..) |c, i| out[i] = std.ascii.toLower(c);
    return out;
}

/// True when the string is PowerShell, not POSIX/cmd or a script name.
pub fn looksLikePowershell(command: []const u8) bool {
    const cmd = trimSpace(command);
    if (cmd.len == 0) return false;
    if (matchPsHead(cmd)) return true;
    if (hasPsStrong(cmd)) return true;
    return hasPsCmdlet(cmd);
}

fn matchPsHead(cmd: []const u8) bool {
    var i: usize = 0;
    while (i < cmd.len and (cmd[i] == ' ' or cmd[i] == '\t')) : (i += 1) {}
    // Optional drive: [A-Za-z]:\
    if (i + 2 < cmd.len and std.ascii.isAlphabetic(cmd[i]) and cmd[i + 1] == ':' and cmd[i + 2] == '\\') {
        i += 3;
    }
    // Optional path prefix ending in \ or /
    var j = i;
    while (j < cmd.len) : (j += 1) {
        const c = cmd[j];
        if (c == ' ' or c == '\t' or c == '"' or c == '\'') break;
        if (c == '\\' or c == '/') {
            i = j + 1;
        }
    }
    const rest = cmd[i..];
    const names = [_][]const u8{ "powershell.exe", "powershell", "pwsh.exe", "pwsh" };
    for (names) |name| {
        if (rest.len >= name.len and eqlIgnoreCase(rest[0..name.len], name) and isWordBoundaryAfter(rest, name.len)) {
            return true;
        }
    }
    return false;
}

fn hasPsStrong(cmd: []const u8) bool {
    // $_ word
    var i: usize = 0;
    while (i < cmd.len) : (i += 1) {
        if (cmd[i] == '$' and i + 1 < cmd.len and cmd[i + 1] == '_' and isWordBoundaryAfter(cmd, i + 2)) {
            return true;
        }
    }
    if (containsIgnoreCase(cmd, "$PSVersionTable")) return true;
    // $env:[A-Za-z]
    i = 0;
    while (i + 5 < cmd.len) : (i += 1) {
        if (eqlIgnoreCase(cmd[i .. i + 5], "$env:") and std.ascii.isAlphabetic(cmd[i + 5])) return true;
    }
    // \bparam\s*(
    i = 0;
    while (i < cmd.len) : (i += 1) {
        if (i + 5 <= cmd.len and eqlIgnoreCase(cmd[i .. i + 5], "param")) {
            const before_ok = i == 0 or !(std.ascii.isAlphanumeric(cmd[i - 1]) or cmd[i - 1] == '_');
            if (!before_ok) continue;
            var k = i + 5;
            while (k < cmd.len and (cmd[k] == ' ' or cmd[k] == '\t' or cmd[k] == '\r' or cmd[k] == '\n')) : (k += 1) {}
            if (k < cmd.len and cmd[k] == '(') return true;
        }
    }
    // @' or @"
    i = 0;
    while (i + 1 < cmd.len) : (i += 1) {
        if (cmd[i] == '@' and (cmd[i + 1] == '\'' or cmd[i + 1] == '"')) return true;
    }
    return false;
}

fn isPsFilenameNoun(noun: []const u8) bool {
    for (ps_filename_nouns) |n| {
        if (eqlIgnoreCase(noun, n)) return true;
    }
    return false;
}

fn hasPsCmdlet(cmd: []const u8) bool {
    var i: usize = 0;
    while (i < cmd.len) : (i += 1) {
        for (ps_verbs) |verb| {
            if (i + verb.len + 2 > cmd.len) continue;
            if (!eqlIgnoreCase(cmd[i .. i + verb.len], verb)) continue;
            const before_ok = i == 0 or !(std.ascii.isAlphanumeric(cmd[i - 1]) or cmd[i - 1] == '_');
            if (!before_ok) continue;
            if (cmd[i + verb.len] != '-') continue;
            const noun_start = i + verb.len + 1;
            if (noun_start >= cmd.len or !std.ascii.isAlphabetic(cmd[noun_start])) continue;
            var noun_end = noun_start + 1;
            while (noun_end < cmd.len and std.ascii.isAlphanumeric(cmd[noun_end])) : (noun_end += 1) {}
            const noun = cmd[noun_start..noun_end];
            if (isPsFilenameNoun(noun)) continue;
            // Skip Verb-Noun.sh / .py etc.
            if (noun_end < cmd.len and cmd[noun_end] == '.') {
                const trail = cmd[noun_end..];
                const skip_ext = [_][]const u8{ ".sh", ".bash", ".zsh", ".py", ".js", ".ts", ".exe", ".bat", ".cmd" };
                var skip = false;
                for (skip_ext) |ext| {
                    if (trail.len >= ext.len and eqlIgnoreCase(trail[0..ext.len], ext) and isWordBoundaryAfter(trail, ext.len)) {
                        skip = true;
                        break;
                    }
                }
                if (skip) continue;
            }
            return true;
        }
    }
    return false;
}

fn hasUntranslatable(s: []const u8) bool {
    var i: usize = 0;
    while (i < s.len) : (i += 1) {
        // $(...) not preceded by $
        if (s[i] == '$' and i + 1 < s.len and s[i + 1] == '(') {
            const preceded_by_dollar = i > 0 and s[i - 1] == '$';
            if (!preceded_by_dollar) {
                if (std.mem.indexOfScalar(u8, s[i + 2 ..], ')')) |_| return true;
            }
        }
        // ${...}
        if (s[i] == '$' and i + 1 < s.len and s[i + 1] == '{') {
            if (std.mem.indexOfScalar(u8, s[i + 2 ..], '}')) |_| return true;
        }
        // `...`
        if (s[i] == '`') {
            if (std.mem.indexOfScalar(u8, s[i + 1 ..], '`')) |_| return true;
        }
    }
    return false;
}

fn rewriteRedirections(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}![]const u8 {
    var s = try arena.dupe(u8, text);
    s = try replaceAllRegexish(arena, s, "&>", "/dev/null", ">NUL 2>&1");
    s = try replaceAllRegexish(arena, s, "2>", "/dev/null", "2>NUL");
    s = try replaceAllRegexish(arena, s, ">", "/dev/null", ">NUL");
    if (std.mem.indexOf(u8, s, "/dev/null")) |_| {
        s = try replaceLiteral(arena, s, "/dev/null", "NUL");
    }
    return s;
}

/// Replace `prefix\s*/dev/null` with `replacement` (Python re.sub equivalent for these forms).
fn replaceAllRegexish(
    arena: std.mem.Allocator,
    text: []const u8,
    prefix: []const u8,
    path: []const u8,
    replacement: []const u8,
) error{OutOfMemory}![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(arena);
    var i: usize = 0;
    while (i < text.len) {
        if (std.mem.startsWith(u8, text[i..], prefix)) {
            var j = i + prefix.len;
            while (j < text.len and (text[j] == ' ' or text[j] == '\t')) : (j += 1) {}
            if (std.mem.startsWith(u8, text[j..], path)) {
                try out.appendSlice(arena, replacement);
                i = j + path.len;
                continue;
            }
        }
        try out.append(arena, text[i]);
        i += 1;
    }
    return try out.toOwnedSlice(arena);
}

fn replaceLiteral(arena: std.mem.Allocator, text: []const u8, old: []const u8, new: []const u8) error{OutOfMemory}![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(arena);
    var i: usize = 0;
    while (i < text.len) {
        if (std.mem.startsWith(u8, text[i..], old)) {
            try out.appendSlice(arena, new);
            i += old.len;
        } else {
            try out.append(arena, text[i]);
            i += 1;
        }
    }
    return try out.toOwnedSlice(arena);
}

const Segment = struct { text: []const u8, op_after: []const u8 };

fn splitTopLevel(arena: std.mem.Allocator, command: []const u8) error{OutOfMemory}![]Segment {
    var parts: std.ArrayList(Segment) = .empty;
    errdefer parts.deinit(arena);
    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(arena);
    var i: usize = 0;
    var quote: u8 = 0;
    while (i < command.len) {
        const ch = command[i];
        if (quote != 0) {
            try buf.append(arena, ch);
            if (ch == quote and (i == 0 or command[i - 1] != '\\')) {
                quote = 0;
            }
            i += 1;
            continue;
        }
        if (ch == '"' or ch == '\'') {
            quote = ch;
            try buf.append(arena, ch);
            i += 1;
            continue;
        }
        var matched: []const u8 = "";
        for (chain_ops) |op| {
            if (startsWithOp(command, i, op)) {
                matched = op;
                break;
            }
        }
        if (matched.len != 0) {
            if (isRedirectKeep(matched)) {
                try buf.appendSlice(arena, matched);
                i += matched.len;
                continue;
            }
            const seg = try arena.dupe(u8, trimSpace(buf.items));
            try parts.append(arena, .{ .text = seg, .op_after = matched });
            buf.clearRetainingCapacity();
            i += matched.len;
            continue;
        }
        try buf.append(arena, ch);
        i += 1;
    }
    const last = try arena.dupe(u8, trimSpace(buf.items));
    try parts.append(arena, .{ .text = last, .op_after = "" });

    // Drop empty segments without an op (Python: if s or op).
    var kept: std.ArrayList(Segment) = .empty;
    errdefer kept.deinit(arena);
    for (parts.items) |p| {
        if (p.text.len != 0 or p.op_after.len != 0) {
            try kept.append(arena, p);
        }
    }
    return try kept.toOwnedSlice(arena);
}

fn tokenize(arena: std.mem.Allocator, segment: []const u8) error{OutOfMemory}![]const []const u8 {
    const s = trimSpace(segment);
    var out: std.ArrayList([]const u8) = .empty;
    errdefer out.deinit(arena);
    var i: usize = 0;
    while (i < s.len) {
        while (i < s.len and (s[i] == ' ' or s[i] == '\t')) : (i += 1) {}
        if (i >= s.len) break;
        if (s[i] == '"') {
            var j = i + 1;
            while (j < s.len and s[j] != '"') : (j += 1) {}
            if (j < s.len) j += 1;
            try out.append(arena, try arena.dupe(u8, s[i..j]));
            i = j;
            continue;
        }
        if (s[i] == '\'') {
            var j = i + 1;
            while (j < s.len and s[j] != '\'') : (j += 1) {}
            if (j < s.len) j += 1;
            try out.append(arena, try arena.dupe(u8, s[i..j]));
            i = j;
            continue;
        }
        var j = i;
        while (j < s.len and s[j] != ' ' and s[j] != '\t') : (j += 1) {}
        try out.append(arena, try arena.dupe(u8, s[i..j]));
        i = j;
    }
    return try out.toOwnedSlice(arena);
}

fn unquote(tok: []const u8) []const u8 {
    const t = trimSpace(tok);
    if (t.len >= 2 and t[0] == t[t.len - 1] and (t[0] == '"' or t[0] == '\'')) {
        return t[1 .. t.len - 1];
    }
    return t;
}

fn winPath(arena: std.mem.Allocator, path: []const u8) error{OutOfMemory}![]const u8 {
    if (std.mem.indexOfScalar(u8, path, '/') == null) return path;
    const out = try arena.dupe(u8, path);
    for (out) |*c| {
        if (c.* == '/') c.* = '\\';
    }
    return out;
}

fn q(arena: std.mem.Allocator, path: []const u8) error{OutOfMemory}![]const u8 {
    var p = try winPath(arena, path);
    if (p.len == 0) return try arena.dupe(u8, "\"\"");
    if (p.len >= 2 and p[0] == '"' and p[p.len - 1] == '"') {
        p = p[1 .. p.len - 1];
    }
    // cmd treats "" as a literal quote inside a quoted string.
    var escaped: std.ArrayList(u8) = .empty;
    errdefer escaped.deinit(arena);
    try escaped.append(arena, '"');
    for (p) |c| {
        if (c == '"') {
            try escaped.appendSlice(arena, "\"\"");
        } else {
            try escaped.append(arena, c);
        }
    }
    try escaped.append(arena, '"');
    return try escaped.toOwnedSlice(arena);
}

fn cmdExistDir(arena: std.mem.Allocator, win_p: []const u8) error{OutOfMemory}![]const u8 {
    const trimmed = std.mem.trimEnd(u8, win_p, "\\");
    const with_dot = try std.fmt.allocPrint(arena, "{s}\\.", .{trimmed});
    return q(arena, with_dot);
}

fn rstripSlash(p: []const u8) []const u8 {
    return std.mem.trimEnd(u8, p, "\\");
}

fn isReadNotOpenDoc(path: []const u8) bool {
    for (read_not_open) |ext| {
        if (endsWithIgnoreCase(path, ext)) return true;
    }
    return false;
}

fn noteStartsWith(notes: []const []const u8, prefix: []const u8) bool {
    for (notes) |n| {
        if (std.mem.startsWith(u8, n, prefix)) return true;
    }
    return false;
}

fn appendNote(arena: std.mem.Allocator, notes: *std.ArrayList([]const u8), note: []const u8) error{OutOfMemory}!void {
    try notes.append(arena, try arena.dupe(u8, note));
}

fn joinWith(arena: std.mem.Allocator, parts: []const []const u8, sep: []const u8) error{OutOfMemory}![]const u8 {
    if (parts.len == 0) return "";
    var total: usize = 0;
    for (parts, 0..) |p, i| {
        total += p.len;
        if (i + 1 < parts.len) total += sep.len;
    }
    var out = try arena.alloc(u8, total);
    var o: usize = 0;
    for (parts, 0..) |p, i| {
        @memcpy(out[o .. o + p.len], p);
        o += p.len;
        if (i + 1 < parts.len) {
            @memcpy(out[o .. o + sep.len], sep);
            o += sep.len;
        }
    }
    return out;
}

fn collapseSpaces(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}![]const u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(arena);
    var i: usize = 0;
    while (i < text.len) {
        if (text[i] == ' ' or text[i] == '\t') {
            try out.append(arena, ' ');
            while (i < text.len and (text[i] == ' ' or text[i] == '\t')) : (i += 1) {}
            continue;
        }
        try out.append(arena, text[i]);
        i += 1;
    }
    return try out.toOwnedSlice(arena);
}

fn psQ(arena: std.mem.Allocator, path: []const u8) error{OutOfMemory}![]const u8 {
    const win_p = try winPath(arena, path);
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(arena);
    try out.append(arena, '\'');
    for (win_p) |c| {
        if (c == '\'') {
            try out.appendSlice(arena, "''");
        } else {
            try out.append(arena, c);
        }
    }
    try out.append(arena, '\'');
    return try out.toOwnedSlice(arena);
}

fn pythonLineSlice(arena: std.mem.Allocator, exe: []const u8, path: []const u8, n: usize, tail: bool) error{OutOfMemory}![]const u8 {
    var win_p = try winPath(arena, path);
    // Strip ''' like Python replace("'''", "")
    if (std.mem.indexOf(u8, win_p, "'''")) |_| {
        win_p = try replaceLiteral(arena, win_p, "'''", "");
    }
    const op = if (tail)
        try std.fmt.allocPrint(arena, "p[-{d}:]", .{n})
    else
        try std.fmt.allocPrint(arena, "p[:{d}]", .{n});
    const code = try std.fmt.allocPrint(
        arena,
        "p=open(r'''{s}''',encoding='utf-8',errors='replace').read().splitlines(True);print(''.join({s}),end='')",
        .{ win_p, op },
    );
    return std.fmt.allocPrint(arena, "{s} -c {s}", .{ try q(arena, exe), try q(arena, code) });
}

fn pythonLineCount(arena: std.mem.Allocator, exe: []const u8, path: []const u8) error{OutOfMemory}![]const u8 {
    var win_p = try winPath(arena, path);
    if (std.mem.indexOf(u8, win_p, "'''")) |_| {
        win_p = try replaceLiteral(arena, win_p, "'''", "");
    }
    const code = try std.fmt.allocPrint(
        arena,
        "p=open(r'''{s}''',encoding='utf-8',errors='replace').read().splitlines();print(len(p))",
        .{win_p},
    );
    return std.fmt.allocPrint(arena, "{s} -c {s}", .{ try q(arena, exe), try q(arena, code) });
}

fn pwshLineSlice(arena: std.mem.Allocator, pw: []const u8, path: []const u8, n: usize, tail: bool) error{OutOfMemory}![]const u8 {
    const op = if (tail)
        try std.fmt.allocPrint(arena, "-Tail {d}", .{n})
    else
        try std.fmt.allocPrint(arena, "-TotalCount {d}", .{n});
    return std.fmt.allocPrint(
        arena,
        "{s} -NoProfile -Command \"Get-Content -LiteralPath {s} {s}\"",
        .{ try q(arena, pw), try psQ(arena, path), op },
    );
}

fn pwshLineCount(arena: std.mem.Allocator, pw: []const u8, path: []const u8) error{OutOfMemory}![]const u8 {
    return std.fmt.allocPrint(
        arena,
        "{s} -NoProfile -Command \"(Get-Content -LiteralPath {s} | Measure-Object -Line).Lines\"",
        .{ try q(arena, pw), try psQ(arena, path) },
    );
}

fn flagHasRecursive(flags: []const []const u8) bool {
    for (flags) |f| {
        if (std.mem.eql(u8, f, "--recursive")) return true;
        // -r / -rf / -fr / -R etc.
        var i: usize = 0;
        if (f.len > 0 and f[0] == '-') i = 1;
        while (i < f.len and f[i] == '-') : (i += 1) {}
        while (i < f.len) : (i += 1) {
            if (f[i] == 'r' or f[i] == 'R') return true;
        }
    }
    return false;
}

const SegRewrite = struct {
    text: []const u8,
    notes: []const []const u8,
};

fn rewriteSegment(
    arena: std.mem.Allocator,
    segment: []const u8,
    opts: TranslateOptions,
) error{OutOfMemory}!SegRewrite {
    const s = trimSpace(segment);
    if (s.len == 0) return .{ .text = s, .notes = &.{} };

    var notes: std.ArrayList([]const u8) = .empty;
    errdefer notes.deinit(arena);

    const toks = try tokenize(arena, s);
    if (toks.len == 0) return .{ .text = s, .notes = &.{} };

    const head = try toLowerAlloc(arena, unquote(toks[0]));

    // cmd /c start|explorer text → type
    if (std.mem.eql(u8, head, "cmd") or std.mem.eql(u8, head, "cmd.exe")) {
        var has_start = false;
        for (toks[1..]) |t| {
            const u = try toLowerAlloc(arena, unquote(t));
            if (std.mem.eql(u8, u, "start") or std.mem.eql(u8, u, "explorer") or std.mem.eql(u8, u, "explorer.exe")) {
                has_start = true;
                break;
            }
        }
        if (has_start) {
            var paths: std.ArrayList([]const u8) = .empty;
            defer paths.deinit(arena);
            for (toks[1..]) |t| {
                const u = unquote(t);
                const ul = try toLowerAlloc(arena, u);
                if (std.mem.eql(u8, ul, "/c") or std.mem.eql(u8, ul, "/k") or
                    std.mem.eql(u8, ul, "start") or std.mem.eql(u8, ul, "explorer") or
                    std.mem.eql(u8, ul, "explorer.exe") or u.len == 0)
                {
                    continue;
                }
                if (u[0] == '/') continue;
                try paths.append(arena, u);
            }
            if (paths.items.len > 0 and isReadNotOpenDoc(paths.items[0])) {
                try appendNote(arena, &notes, "cmd start text → type (file_read, no OS window)");
                const text = try std.fmt.allocPrint(arena, "type {s}", .{try q(arena, paths.items[0])});
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
        }
    }

    if (std.mem.eql(u8, head, "start") or std.mem.eql(u8, head, "explorer") or std.mem.eql(u8, head, "explorer.exe")) {
        if (toks.len >= 2) {
            var rest: std.ArrayList([]const u8) = .empty;
            defer rest.deinit(arena);
            for (toks[1..]) |t| try rest.append(arena, unquote(t));
            var paths: std.ArrayList([]const u8) = .empty;
            defer paths.deinit(arena);
            var idx: usize = 0;
            if (rest.items.len > 0 and rest.items[0].len == 0) idx = 1;
            while (idx < rest.items.len) : (idx += 1) {
                const t = rest.items[idx];
                if (t.len == 0) continue;
                if (t[0] == '/') continue;
                try paths.append(arena, t);
            }
            if (paths.items.len > 0 and isReadNotOpenDoc(paths.items[0])) {
                try appendNote(arena, &notes, "start/explorer text → type (file_read, no OS window)");
                const text = try std.fmt.allocPrint(arena, "type {s}", .{try q(arena, paths.items[0])});
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
        }
    }

    // mkdir -p
    if (std.mem.eql(u8, head, "mkdir") and toks.len >= 2 and
        (std.mem.eql(u8, toks[1], "-p") or std.mem.eql(u8, toks[1], "--parents")))
    {
        var paths: std.ArrayList([]const u8) = .empty;
        defer paths.deinit(arena);
        for (toks[2..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            try paths.append(arena, unquote(t));
        }
        if (paths.items.len == 0) {
            try appendNote(arena, &notes, "mkdir -p (empty)");
            return .{ .text = "echo no_paths", .notes = try notes.toOwnedSlice(arena) };
        }
        var parts: std.ArrayList([]const u8) = .empty;
        defer parts.deinit(arena);
        for (paths.items) |p| {
            const win_p = rstripSlash(try winPath(arena, p));
            const bit = try std.fmt.allocPrint(
                arena,
                "(if not exist {s} mkdir {s})",
                .{ try cmdExistDir(arena, win_p), try q(arena, win_p) },
            );
            try parts.append(arena, bit);
        }
        try appendNote(arena, &notes, "mkdir -p → if not exist mkdir");
        return .{ .text = try joinWith(arena, parts.items, " & "), .notes = try notes.toOwnedSlice(arena) };
    }

    // rm
    if (std.mem.eql(u8, head, "rm") and toks.len >= 2) {
        var flags: std.ArrayList([]const u8) = .empty;
        defer flags.deinit(arena);
        var paths: std.ArrayList([]const u8) = .empty;
        defer paths.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) {
                try flags.append(arena, t);
            } else {
                try paths.append(arena, unquote(t));
            }
        }
        if (paths.items.len == 0) {
            return .{ .text = s, .notes = try notes.toOwnedSlice(arena) };
        }
        const recursive = flagHasRecursive(flags.items);
        var bits: std.ArrayList([]const u8) = .empty;
        defer bits.deinit(arena);
        for (paths.items) |p| {
            const win_p = rstripSlash(try winPath(arena, p));
            if (recursive) {
                const bit = try std.fmt.allocPrint(
                    arena,
                    "(if exist {s} (rmdir /s /q {s}) else if exist {s} del /f /q {s})",
                    .{ try cmdExistDir(arena, win_p), try q(arena, win_p), try q(arena, win_p), try q(arena, win_p) },
                );
                try bits.append(arena, bit);
            } else {
                try bits.append(arena, try std.fmt.allocPrint(arena, "del /f /q {s}", .{try q(arena, win_p)}));
            }
        }
        try appendNote(arena, &notes, "rm → del/rmdir");
        return .{ .text = try joinWith(arena, bits.items, " & "), .notes = try notes.toOwnedSlice(arena) };
    }

    // cp / copy
    if ((std.mem.eql(u8, head, "cp") or std.mem.eql(u8, head, "copy")) and toks.len >= 3) {
        var rec = false;
        for (toks[1..]) |t| {
            if (std.mem.eql(u8, t, "-r") or std.mem.eql(u8, t, "-R") or
                std.mem.eql(u8, t, "-a") or std.mem.eql(u8, t, "--recursive"))
            {
                rec = true;
            }
        }
        var paths: std.ArrayList([]const u8) = .empty;
        defer paths.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            try paths.append(arena, unquote(t));
        }
        if (paths.items.len >= 2) {
            const src = paths.items[0];
            const dst = paths.items[paths.items.len - 1];
            if (rec) {
                try appendNote(arena, &notes, "cp -r → xcopy");
                const text = try std.fmt.allocPrint(arena, "xcopy /e /i /y {s} {s}", .{ try q(arena, src), try q(arena, dst) });
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
            try appendNote(arena, &notes, "cp → copy");
            const text = try std.fmt.allocPrint(arena, "copy /y {s} {s}", .{ try q(arena, src), try q(arena, dst) });
            return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // mv / move
    if ((std.mem.eql(u8, head, "mv") or std.mem.eql(u8, head, "move")) and toks.len >= 3) {
        var paths: std.ArrayList([]const u8) = .empty;
        defer paths.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            try paths.append(arena, unquote(t));
        }
        if (paths.items.len >= 2) {
            try appendNote(arena, &notes, "mv → move");
            const text = try std.fmt.allocPrint(
                arena,
                "move /y {s} {s}",
                .{ try q(arena, paths.items[0]), try q(arena, paths.items[paths.items.len - 1]) },
            );
            return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // cat
    if (std.mem.eql(u8, head, "cat") and toks.len >= 2) {
        var has_flag = false;
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) has_flag = true;
        }
        if (!has_flag) {
            var parts: std.ArrayList([]const u8) = .empty;
            defer parts.deinit(arena);
            for (toks[1..]) |t| {
                try parts.append(arena, try std.fmt.allocPrint(arena, "type {s}", .{try q(arena, unquote(t))}));
            }
            try appendNote(arena, &notes, "cat → type");
            return .{ .text = try joinWith(arena, parts.items, " & "), .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // ls
    if (std.mem.eql(u8, head, "ls")) {
        var paths: std.ArrayList([]const u8) = .empty;
        defer paths.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            try paths.append(arena, unquote(t));
        }
        try appendNote(arena, &notes, "ls → dir");
        if (paths.items.len == 0) {
            return .{ .text = "dir", .notes = try notes.toOwnedSlice(arena) };
        }
        var parts: std.ArrayList([]const u8) = .empty;
        defer parts.deinit(arena);
        for (paths.items) |p| {
            try parts.append(arena, try std.fmt.allocPrint(arena, "dir {s}", .{try q(arena, p)}));
        }
        return .{ .text = try joinWith(arena, parts.items, " & "), .notes = try notes.toOwnedSlice(arena) };
    }

    // pwd
    if (std.mem.eql(u8, head, "pwd") and toks.len == 1) {
        try appendNote(arena, &notes, "pwd → cd");
        return .{ .text = "cd", .notes = try notes.toOwnedSlice(arena) };
    }

    // export
    if (std.mem.eql(u8, head, "export") and toks.len >= 2) {
        var assign_buf: std.ArrayList(u8) = .empty;
        defer assign_buf.deinit(arena);
        for (toks[1..], 0..) |t, ti| {
            if (ti != 0) try assign_buf.append(arena, ' ');
            try assign_buf.appendSlice(arena, t);
        }
        const assign = unquote(assign_buf.items);
        try appendNote(arena, &notes, "export → set");
        const text = try std.fmt.allocPrint(arena, "set \"{s}\"", .{assign});
        return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
    }

    // touch
    if (std.mem.eql(u8, head, "touch") and toks.len >= 2) {
        var bits: std.ArrayList([]const u8) = .empty;
        defer bits.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            const p = unquote(t);
            const bit = try std.fmt.allocPrint(
                arena,
                "(if not exist {s} type nul > {s})",
                .{ try q(arena, p), try q(arena, p) },
            );
            try bits.append(arena, bit);
        }
        try appendNote(arena, &notes, "touch → type nul");
        if (bits.items.len == 0) {
            return .{ .text = s, .notes = try notes.toOwnedSlice(arena) };
        }
        return .{ .text = try joinWith(arena, bits.items, " & "), .notes = try notes.toOwnedSlice(arena) };
    }

    // true / false
    if (std.mem.eql(u8, head, "true") and toks.len == 1) {
        try appendNote(arena, &notes, "true → cd .");
        return .{ .text = "cd .", .notes = try notes.toOwnedSlice(arena) };
    }
    if (std.mem.eql(u8, head, "false") and toks.len == 1) {
        try appendNote(arena, &notes, "false → cmd /c exit 1");
        return .{ .text = "cmd /c exit 1", .notes = try notes.toOwnedSlice(arena) };
    }

    // which / command -v
    if (std.mem.eql(u8, head, "which") or
        (std.mem.eql(u8, head, "command") and toks.len >= 3 and std.mem.eql(u8, toks[1], "-v")))
    {
        const name = unquote(toks[toks.len - 1]);
        try appendNote(arena, &notes, "which → where");
        const text = try std.fmt.allocPrint(arena, "where {s}", .{try q(arena, name)});
        return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
    }

    // chmod → drop
    if (std.mem.eql(u8, head, "chmod")) {
        try appendNote(arena, &notes, "chmod ignored on Windows host");
        return .{ .text = "", .notes = try notes.toOwnedSlice(arena) };
    }

    // grep
    if (std.mem.eql(u8, head, "grep")) {
        var cleaned: std.ArrayList([]const u8) = .empty;
        defer cleaned.deinit(arena);
        for (toks[1..]) |t| {
            const u = unquote(t);
            if (std.mem.startsWith(u8, u, "-") and !std.mem.eql(u8, u, "-e")) continue;
            if (std.mem.eql(u8, u, "-e")) continue;
            try cleaned.append(arena, u);
        }
        var pattern: []const u8 = "";
        var grep_files: []const []const u8 = &.{};
        if (cleaned.items.len > 0) {
            pattern = cleaned.items[0];
            grep_files = cleaned.items[1..];
        }
        if (pattern.len != 0 and opts.rg_path.len != 0) {
            try appendNote(arena, &notes, "grep → rg");
            var file_bits: std.ArrayList([]const u8) = .empty;
            defer file_bits.deinit(arena);
            for (grep_files) |f| try file_bits.append(arena, try q(arena, f));
            const files_joined = try joinWith(arena, file_bits.items, " ");
            const text = if (files_joined.len == 0)
                try std.fmt.allocPrint(arena, "\"{s}\" -n {s}", .{ opts.rg_path, try q(arena, pattern) })
            else
                try std.fmt.allocPrint(arena, "\"{s}\" -n {s} {s}", .{ opts.rg_path, try q(arena, pattern), files_joined });
            return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
        }
        if (pattern.len != 0) {
            try appendNote(arena, &notes, "grep → findstr (literal)");
            var file_bits: std.ArrayList([]const u8) = .empty;
            defer file_bits.deinit(arena);
            for (grep_files) |f| try file_bits.append(arena, try q(arena, f));
            const files_joined = try joinWith(arena, file_bits.items, " ");
            const text = if (files_joined.len == 0)
                try std.fmt.allocPrint(arena, "findstr /n /c:{s}", .{try q(arena, pattern)})
            else
                try std.fmt.allocPrint(arena, "findstr /n /c:{s} {s}", .{ try q(arena, pattern), files_joined });
            return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // head / tail
    if (std.mem.eql(u8, head, "head") or std.mem.eql(u8, head, "tail")) {
        var n: usize = 10;
        var slice_files: std.ArrayList([]const u8) = .empty;
        defer slice_files.deinit(arena);
        var i: usize = 1;
        while (i < toks.len) {
            const t = toks[i];
            if ((std.mem.eql(u8, t, "-n") or std.mem.eql(u8, t, "--lines")) and i + 1 < toks.len) {
                const parsed = std.fmt.parseInt(usize, unquote(toks[i + 1]), 10) catch 0;
                if (parsed >= 1) n = parsed;
                i += 2;
                continue;
            }
            if (t.len >= 2 and t[0] == '-' and std.ascii.isDigit(t[1])) {
                const parsed = std.fmt.parseInt(usize, t[1..], 10) catch 0;
                if (parsed >= 1) n = parsed;
                i += 1;
                continue;
            }
            if (!std.mem.startsWith(u8, t, "-")) {
                try slice_files.append(arena, unquote(t));
            }
            i += 1;
        }
        if (slice_files.items.len > 0) {
            const is_tail = std.mem.eql(u8, head, "tail");
            if (opts.python_exe.len != 0) {
                try appendNote(arena, &notes, try std.fmt.allocPrint(arena, "{s} → python slice", .{head}));
                const text = try pythonLineSlice(arena, opts.python_exe, slice_files.items[0], n, is_tail);
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
            if (opts.pwsh_exe.len != 0) {
                try appendNote(arena, &notes, try std.fmt.allocPrint(arena, "{s} → pwsh Get-Content", .{head}));
                const text = try pwshLineSlice(arena, opts.pwsh_exe, slice_files.items[0], n, is_tail);
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
            try appendNote(arena, &notes, try std.fmt.allocPrint(arena, "{s} {s}", .{ head, no_py_note }));
            return .{ .text = s, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // wc -l
    if (std.mem.eql(u8, head, "wc")) {
        var has_lines = false;
        for (toks[1..]) |t| {
            if (std.mem.eql(u8, t, "-l") or std.mem.eql(u8, t, "--lines")) has_lines = true;
        }
        var wc_files: std.ArrayList([]const u8) = .empty;
        defer wc_files.deinit(arena);
        for (toks[1..]) |t| {
            if (std.mem.startsWith(u8, t, "-")) continue;
            try wc_files.append(arena, unquote(t));
        }
        if (wc_files.items.len > 0 and has_lines) {
            if (opts.python_exe.len != 0) {
                try appendNote(arena, &notes, "wc -l → python line count");
                const text = try pythonLineCount(arena, opts.python_exe, wc_files.items[0]);
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
            if (opts.pwsh_exe.len != 0) {
                try appendNote(arena, &notes, "wc -l → pwsh Measure-Object");
                const text = try pwshLineCount(arena, opts.pwsh_exe, wc_files.items[0]);
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
            try appendNote(arena, &notes, try std.fmt.allocPrint(arena, "wc -l {s}", .{no_py_note}));
            return .{ .text = s, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // find -name
    if (std.mem.eql(u8, head, "find")) {
        var name_pat: []const u8 = "";
        var start: []const u8 = ".";
        var rest: std.ArrayList([]const u8) = .empty;
        defer rest.deinit(arena);
        for (toks[1..]) |t| try rest.append(arena, unquote(t));
        var i: usize = 0;
        while (i < rest.items.len) {
            if (std.mem.eql(u8, rest.items[i], "-name") and i + 1 < rest.items.len) {
                name_pat = rest.items[i + 1];
                i += 2;
                continue;
            }
            if (!std.mem.startsWith(u8, rest.items[i], "-") and std.mem.eql(u8, start, ".")) {
                start = rest.items[i];
            }
            i += 1;
        }
        if (name_pat.len != 0) {
            try appendNote(arena, &notes, "find -name → dir /s /b");
            var win_start = rstripSlash(try winPath(arena, start));
            if (win_start.len == 0) win_start = ".";
            const combined = try std.fmt.allocPrint(arena, "{s}\\{s}", .{ win_start, name_pat });
            const text = try std.fmt.allocPrint(arena, "dir /s /b {s}", .{try q(arena, combined)});
            return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
        }
    }

    // test -f / test -e
    if (std.mem.eql(u8, head, "test") and toks.len >= 3 and
        (std.mem.eql(u8, toks[1], "-f") or std.mem.eql(u8, toks[1], "-e")))
    {
        try appendNote(arena, &notes, "test -f → if exist");
        const text = try std.fmt.allocPrint(
            arena,
            "if exist {s} (echo exists) else (exit /b 1)",
            .{try q(arena, unquote(toks[2]))},
        );
        return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
    }

    // [ -f path ]
    if (std.mem.eql(u8, head, "[")) {
        var has_f = false;
        for (toks) |t| {
            if (std.mem.eql(u8, t, "-f")) has_f = true;
        }
        if (has_f) {
            var path_tok: []const u8 = "";
            for (toks, 0..) |t, ti| {
                if (std.mem.eql(u8, t, "-f") and ti + 1 < toks.len) {
                    path_tok = unquote(toks[ti + 1]);
                    if (std.mem.endsWith(u8, path_tok, "]")) {
                        path_tok = path_tok[0 .. path_tok.len - 1];
                    }
                    break;
                }
            }
            if (path_tok.len != 0) {
                try appendNote(arena, &notes, "[ -f → if exist");
                const text = try std.fmt.allocPrint(
                    arena,
                    "if exist {s} (echo exists) else (exit /b 1)",
                    .{try q(arena, path_tok)},
                );
                return .{ .text = text, .notes = try notes.toOwnedSlice(arena) };
            }
        }
    }

    return .{ .text = s, .notes = try notes.toOwnedSlice(arena) };
}

/// Rewrite POSIX-ish `command` for the host shell. Arena owns the result.
pub fn translatePosixToHost(
    arena: std.mem.Allocator,
    command: []const u8,
    opts: TranslateOptions,
) error{OutOfMemory}!TranslateResult {
    const raw = trimSpace(command);
    if (raw.len == 0) return .{ .text = raw };

    const resolved = if (opts.host.len != 0) opts.host else defaultHostName();
    if (!std.mem.eql(u8, resolved, "cmd")) {
        return .{ .text = try arena.dupe(u8, raw) };
    }
    if (looksLikePowershell(raw)) {
        var notes = try arena.alloc([]const u8, 1);
        notes[0] = try arena.dupe(u8, ps_leave_note);
        return .{ .text = try arena.dupe(u8, raw), .notes = notes };
    }

    var notes: std.ArrayList([]const u8) = .empty;
    errdefer notes.deinit(arena);

    const rewritten = try rewriteRedirections(arena, raw);
    if (!std.mem.eql(u8, rewritten, raw)) {
        try appendNote(arena, &notes, "redirect /dev/null → NUL");
    }

    if (hasUntranslatable(rewritten)) {
        try appendNote(arena, &notes, untranslatable_note);
        return .{
            .text = rewritten,
            .changed = !std.mem.eql(u8, rewritten, raw),
            .notes = try notes.toOwnedSlice(arena),
            .untranslatable = true,
        };
    }

    const parts = try splitTopLevel(arena, rewritten);
    var kept: std.ArrayList(Segment) = .empty;
    defer kept.deinit(arena);
    var changed = !std.mem.eql(u8, rewritten, raw);

    for (parts) |part| {
        const rw = try rewriteSegment(arena, part.text, opts);
        for (rw.notes) |n| try notes.append(arena, n);
        const dropped_chmod = rw.text.len == 0 and noteStartsWith(rw.notes, "chmod ignored");
        if (dropped_chmod) {
            changed = true;
            continue;
        }
        if (!std.mem.eql(u8, rw.text, part.text)) changed = true;
        try kept.append(arena, .{ .text = rw.text, .op_after = part.op_after });
    }

    if (kept.items.len == 0) {
        if (noteStartsWith(notes.items, "chmod ignored")) {
            return .{
                .text = try arena.dupe(u8, raw),
                .changed = true,
                .notes = try notes.toOwnedSlice(arena),
                .noop = true,
            };
        }
        return .{
            .text = "",
            .changed = changed,
            .notes = try notes.toOwnedSlice(arena),
        };
    }

    var out_segs: std.ArrayList(u8) = .empty;
    errdefer out_segs.deinit(arena);
    for (kept.items, 0..) |seg, i| {
        try out_segs.appendSlice(arena, seg.text);
        if (seg.op_after.len != 0 and i + 1 < kept.items.len) {
            if (std.mem.eql(u8, seg.op_after, ";")) {
                try out_segs.appendSlice(arena, " & ");
            } else {
                try out_segs.append(arena, ' ');
                try out_segs.appendSlice(arena, seg.op_after);
                try out_segs.append(arena, ' ');
            }
        }
    }
    var text = trimSpace(out_segs.items);
    text = try collapseSpaces(arena, text);
    return .{
        .text = text,
        .changed = changed,
        .notes = try notes.toOwnedSlice(arena),
        .untranslatable = false,
        .noop = false,
    };
}

fn writeKey(list: *std.ArrayList(u8), gpa: std.mem.Allocator, first: *bool, key: []const u8) error{OutOfMemory}!void {
    if (!first.*) try list.append(gpa, ',');
    first.* = false;
    try writeString(list, gpa, key);
    try list.append(gpa, ':');
}

fn writeString(list: *std.ArrayList(u8), gpa: std.mem.Allocator, s: []const u8) error{OutOfMemory}!void {
    try list.append(gpa, '"');
    for (s) |c| {
        switch (c) {
            '"' => try list.appendSlice(gpa, "\\\""),
            '\\' => try list.appendSlice(gpa, "\\\\"),
            '\n' => try list.appendSlice(gpa, "\\n"),
            '\r' => try list.appendSlice(gpa, "\\r"),
            '\t' => try list.appendSlice(gpa, "\\t"),
            else => try list.append(gpa, c),
        }
    }
    try list.append(gpa, '"');
}

fn writeStringArray(list: *std.ArrayList(u8), gpa: std.mem.Allocator, items: []const []const u8) error{OutOfMemory}!void {
    try list.append(gpa, '[');
    for (items, 0..) |item, i| {
        if (i != 0) try list.append(gpa, ',');
        try writeString(list, gpa, item);
    }
    try list.append(gpa, ']');
}

fn writeBool(list: *std.ArrayList(u8), gpa: std.mem.Allocator, v: bool) error{OutOfMemory}!void {
    try list.appendSlice(gpa, if (v) "true" else "false");
}

pub fn resultToJson(gpa: std.mem.Allocator, result: TranslateResult) error{OutOfMemory}![]u8 {
    var list: std.ArrayList(u8) = .empty;
    errdefer list.deinit(gpa);
    try list.append(gpa, '{');
    var first = true;
    try writeKey(&list, gpa, &first, "text");
    try writeString(&list, gpa, result.text);
    try writeKey(&list, gpa, &first, "changed");
    try writeBool(&list, gpa, result.changed);
    try writeKey(&list, gpa, &first, "notes");
    try writeStringArray(&list, gpa, result.notes);
    try writeKey(&list, gpa, &first, "untranslatable");
    try writeBool(&list, gpa, result.untranslatable);
    try writeKey(&list, gpa, &first, "noop");
    try writeBool(&list, gpa, result.noop);
    try list.append(gpa, '}');
    return try list.toOwnedSlice(gpa);
}

fn jsonStringField(obj: std.json.ObjectMap, key: []const u8) ?[]const u8 {
    const v = obj.get(key) orelse return null;
    return switch (v) {
        .string => |s| s,
        else => null,
    };
}

fn jsonBoolField(obj: std.json.ObjectMap, key: []const u8) ?bool {
    const v = obj.get(key) orelse return null;
    return switch (v) {
        .bool => |b| b,
        else => null,
    };
}

fn parseTranslateRequest(arena: std.mem.Allocator, json_in: []const u8) error{ OutOfMemory, InvalidArgument }!struct {
    command: []const u8,
    opts: TranslateOptions,
} {
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json_in, .{}) catch return error.InvalidArgument;
    const object = switch (parsed) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };
    const command = jsonStringField(object, "command") orelse "";
    var opts: TranslateOptions = .{};
    if (jsonStringField(object, "host")) |h| opts.host = h;
    if (jsonStringField(object, "rg_path")) |r| opts.rg_path = r;
    if (jsonStringField(object, "python_exe")) |p| opts.python_exe = p;
    if (jsonStringField(object, "pwsh_exe")) |p| opts.pwsh_exe = p;
    return .{ .command = command, .opts = opts };
}

fn translateToOwnedJson(json_in: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(host.allocator);
    errdefer arena_state.deinit();
    const arena = arena_state.allocator();

    const req = parseTranslateRequest(arena, json_in) catch return error.InvalidArgument;
    const result = translatePosixToHost(arena, req.command, req.opts) catch return error.OutOfMemory;
    const encoded = resultToJson(host.allocator, result) catch return error.OutOfMemory;
    arena_state.deinit();
    return encoded;
}

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn deliverBytes(result: Error![]u8, out_ptr: ?*?[*]u8, out_len: ?*usize) i32 {
    const ptr_slot = out_ptr orelse return invalid_status;
    const len_slot = out_len orelse return invalid_status;
    const bytes = result catch |err| {
        ptr_slot.* = null;
        len_slot.* = 0;
        return host.statusOf(err);
    };
    ptr_slot.* = bytes.ptr;
    len_slot.* = bytes.len;
    return ok_status;
}

/// Translate a POSIX-ish command for the host shell (ABI 4).
/// Input: `{command, host?, rg_path?, python_exe?, pwsh_exe?}`.
/// Output: `{text, changed, notes, untranslatable, noop}` — caller frees.
export fn remedy_core_translate_posix_to_host(
    json_in: ?[*]const u8,
    json_in_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(translateToOwnedJson(input), out_json, out_len);
}

/// 1 when *command* looks like PowerShell; 0 otherwise (empty → 0).
export fn remedy_core_looks_like_powershell(
    command_ptr: ?[*]const u8,
    command_len: usize,
    out_flag: ?*u8,
) callconv(.c) i32 {
    const out = out_flag orelse return invalid_status;
    const cmd = slice(command_ptr, command_len);
    out.* = if (looksLikePowershell(cmd)) 1 else 0;
    return ok_status;
}

const ArgvRewrite = struct {
    argv: []const []const u8,
    notes: []const []const u8,
};

fn argvStem(name: []const u8) []const u8 {
    var s = name;
    if (std.mem.lastIndexOfScalar(u8, s, '/')) |i| s = s[i + 1 ..];
    if (std.mem.lastIndexOfScalar(u8, s, '\\')) |i| s = s[i + 1 ..];
    if (endsWithIgnoreCase(s, ".exe")) s = s[0 .. s.len - 4];
    return s;
}

/// Rewrite a few POSIX argv heads (`wc -l`) the host_run argv path never
/// sent through the command-string translator.
pub fn rewritePosixArgv(
    arena: std.mem.Allocator,
    argv: []const []const u8,
    opts: TranslateOptions,
) error{OutOfMemory}!ArgvRewrite {
    if (argv.len == 0) return .{ .argv = argv, .notes = &.{} };
    const head = argvStem(argv[0]);
    const rest = argv[1..];
    if (!eqlIgnoreCase(head, "wc")) {
        return .{ .argv = argv, .notes = &.{} };
    }
    var has_lines = false;
    for (rest) |t| {
        if (std.mem.eql(u8, t, "-l") or std.mem.eql(u8, t, "--lines")) has_lines = true;
    }
    if (!has_lines) return .{ .argv = argv, .notes = &.{} };
    var files: std.ArrayList([]const u8) = .empty;
    defer files.deinit(arena);
    for (rest) |t| {
        if (std.mem.startsWith(u8, t, "-")) continue;
        try files.append(arena, t);
    }
    if (files.items.len == 0) return .{ .argv = argv, .notes = &.{} };

    const path = files.items[0];
    var notes: std.ArrayList([]const u8) = .empty;
    errdefer notes.deinit(arena);

    if (opts.python_exe.len != 0) {
        try appendNote(arena, &notes, "wc -l → python line count");
        var win_p = try winPath(arena, path);
        if (std.mem.indexOf(u8, win_p, "'''")) |_| {
            win_p = try replaceLiteral(arena, win_p, "'''", "");
        }
        const code = try std.fmt.allocPrint(
            arena,
            "p=open(r'''{s}''',encoding='utf-8',errors='replace').read().splitlines();print(len(p))",
            .{win_p},
        );
        const out = try arena.alloc([]const u8, 3);
        out[0] = opts.python_exe;
        out[1] = "-c";
        out[2] = code;
        return .{ .argv = out, .notes = try notes.toOwnedSlice(arena) };
    }
    if (opts.pwsh_exe.len != 0) {
        try appendNote(arena, &notes, "wc -l → pwsh Measure-Object");
        const escaped = try replaceLiteral(arena, path, "'", "''");
        const ps = try std.fmt.allocPrint(
            arena,
            "(Get-Content -LiteralPath '{s}' | Measure-Object -Line).Lines",
            .{escaped},
        );
        const out = try arena.alloc([]const u8, 4);
        out[0] = opts.pwsh_exe;
        out[1] = "-NoProfile";
        out[2] = "-Command";
        out[3] = ps;
        return .{ .argv = out, .notes = try notes.toOwnedSlice(arena) };
    }
    try appendNote(
        arena,
        &notes,
        "wc -l needs Python — install Python 3 or set REMEDY_PYTHON to python.exe",
    );
    return .{ .argv = argv, .notes = try notes.toOwnedSlice(arena) };
}

fn rewriteArgvToOwnedJson(json_in: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(host.allocator);
    errdefer arena_state.deinit();
    const arena = arena_state.allocator();

    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json_in, .{}) catch return error.InvalidArgument;
    const object = switch (parsed) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };
    const argv_v = object.get("argv") orelse return error.InvalidArgument;
    const argv_arr = switch (argv_v) {
        .array => |a| a.items,
        else => return error.InvalidArgument,
    };
    var argv_list: std.ArrayList([]const u8) = .empty;
    for (argv_arr) |item| {
        const s = switch (item) {
            .string => |str| str,
            else => return error.InvalidArgument,
        };
        try argv_list.append(arena, s);
    }
    var opts: TranslateOptions = .{};
    if (jsonStringField(object, "python_exe")) |p| opts.python_exe = p;
    if (jsonStringField(object, "pwsh_exe")) |p| opts.pwsh_exe = p;

    const result = rewritePosixArgv(arena, argv_list.items, opts) catch return error.OutOfMemory;
    const encoded = host.jsonAlloc(.{
        .argv = result.argv,
        .notes = result.notes,
    }) catch return error.OutOfMemory;
    arena_state.deinit();
    return encoded;
}

/// Rewrite POSIX argv heads for host_run. Input JSON:
/// `{argv:[...], python_exe?: "...", pwsh_exe?: "..."}`.
/// Output: `{argv:[...], notes:[...]}` — caller frees.
export fn remedy_core_rewrite_posix_argv(
    json_in: ?[*]const u8,
    json_in_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(rewriteArgvToOwnedJson(input), out_json, out_len);
}

fn readFileAlloc(gpa: std.mem.Allocator, io: std.Io, path: []const u8) ![]u8 {
    var file = if (std.fs.path.isAbsolute(path))
        try std.Io.Dir.openFileAbsolute(io, path, .{})
    else
        try std.Io.Dir.cwd().openFile(io, path, .{});
    defer file.close(io);
    var reader = file.reader(io, &.{});
    return reader.interface.allocRemaining(gpa, .limited(2 << 20));
}

fn loadTranslateFixture(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    const candidates = [_][]const u8{
        "../../tests/fixtures/host_ir/translate_cmd.json",
        "tests/fixtures/host_ir/translate_cmd.json",
        "../tests/fixtures/host_ir/translate_cmd.json",
    };
    for (candidates) |rel| {
        if (readFileAlloc(gpa, io, rel)) |bytes| return bytes else |_| {}
    }
    return error.FileNotFound;
}

fn replacePlaceholder(arena: std.mem.Allocator, text: []const u8, old: []const u8, new: []const u8) ![]const u8 {
    return replaceLiteral(arena, text, old, new);
}

test "shell_translate matches translate_cmd fixtures" {
    const io = testingIo();
    const fixture_json = try loadTranslateFixture(std.testing.allocator, io);
    defer std.testing.allocator.free(fixture_json);

    var parsed = try std.json.parseFromSlice(std.json.Value, std.testing.allocator, fixture_json, .{});
    defer parsed.deinit();
    const cases = parsed.value.object.get("cases").?.array.items;

    const rg_fixed = "C:\\tools\\rg.exe";
    var passed: usize = 0;
    for (cases) |case_v| {
        const case = case_v.object;
        const id = jsonStringField(case, "id") orelse "(no id)";
        const input = case.get("input").?.object;
        const expected = case.get("expected").?.object;

        var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
        defer arena_state.deinit();
        const arena = arena_state.allocator();

        const command = jsonStringField(input, "command") orelse "";
        var opts: TranslateOptions = .{};
        if (jsonStringField(input, "host")) |h| opts.host = h;
        const use_rg = jsonBoolField(expected, "rg_placeholder") orelse false;
        if (use_rg) opts.rg_path = rg_fixed;

        const got = try translatePosixToHost(arena, command, opts);

        var exp_text = jsonStringField(expected, "text").?;
        if (use_rg) {
            exp_text = try replacePlaceholder(arena, exp_text, "<RG>", rg_fixed);
        }
        std.testing.expectEqualStrings(exp_text, got.text) catch |err| {
            std.debug.print("\nFAIL {s}\n  want: {s}\n  got:  {s}\n", .{ id, exp_text, got.text });
            return err;
        };
        const exp_changed = jsonBoolField(expected, "changed").?;
        try std.testing.expectEqual(exp_changed, got.changed);
        const exp_un = jsonBoolField(expected, "untranslatable").?;
        try std.testing.expectEqual(exp_un, got.untranslatable);
        const exp_noop = jsonBoolField(expected, "noop").?;
        try std.testing.expectEqual(exp_noop, got.noop);
        passed += 1;
    }
    try std.testing.expectEqual(@as(usize, 36), passed);
}

test "shell_translate looksLikePowershell basics" {
    try std.testing.expect(looksLikePowershell("Get-ChildItem -Recurse"));
    try std.testing.expect(looksLikePowershell("pwsh -File x.ps1"));
    try std.testing.expect(looksLikePowershell("$_"));
    try std.testing.expect(looksLikePowershell("Get-Service"));
    try std.testing.expect(!looksLikePowershell("start-server"));
    try std.testing.expect(!looksLikePowershell("start-dev"));
    try std.testing.expect(!looksLikePowershell("where powershell"));
    try std.testing.expect(!looksLikePowershell("mkdir -p docs && echo use powershell"));
    var flag: u8 = 0;
    try std.testing.expectEqual(ok_status, remedy_core_looks_like_powershell("Get-Service".ptr, "Get-Service".len, &flag));
    try std.testing.expectEqual(@as(u8, 1), flag);
    try std.testing.expectEqual(ok_status, remedy_core_looks_like_powershell("start-server".ptr, "start-server".len, &flag));
    try std.testing.expectEqual(@as(u8, 0), flag);
}

test "shell_translate rewritePosixArgv wc -l" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const argv = [_][]const u8{ "wc", "-l", "src/data/file.ts" };
    const got = try rewritePosixArgv(arena, &argv, .{ .python_exe = "C:\\Python\\python.exe" });
    try std.testing.expectEqual(@as(usize, 3), got.argv.len);
    try std.testing.expectEqualStrings("C:\\Python\\python.exe", got.argv[0]);
    try std.testing.expectEqualStrings("-c", got.argv[1]);
    try std.testing.expect(std.mem.indexOf(u8, got.argv[2], "len(p)") != null);
    try std.testing.expect(got.notes.len >= 1);

    const input =
        \\{"argv":["wc","-l","file.txt"],"python_exe":"py.exe"}
    ;
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const status = remedy_core_rewrite_posix_argv(input.ptr, input.len, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, status);
    defer host.allocator.free(out_ptr.?[0..out_len]);
    const raw = out_ptr.?[0..out_len];
    try std.testing.expect(std.mem.indexOf(u8, raw, "py.exe") != null);
    try std.testing.expect(std.mem.indexOf(u8, raw, "len(p)") != null);
}

test "shell_translate C ABI roundtrip mkdir" {
    const input =
        \\{"command":"mkdir -p a","host":"cmd"}
    ;
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const status = remedy_core_translate_posix_to_host(input.ptr, input.len, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, status);
    defer host.allocator.free(out_ptr.?[0..out_len]);
    const raw = out_ptr.?[0..out_len];
    try std.testing.expect(std.mem.indexOf(u8, raw, "if not exist") != null);
}
