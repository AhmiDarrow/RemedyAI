//! Shell write-root / workdir jail for authorized spawn.
//!
//! Mirrors the critical spawn-path gates from Python
//! `allowed_paths_for_shell` + `shell_write_jail` / auth refuse:
//!
//! * **Empty roots** = Full / unbound — no workdir jail (same as sandbox).
//! * **Auth secrets** (`~/.remedy/auth`, `$REMEDY_HOME/auth`) are always refused
//!   in cwd and argv, even when roots are empty.
//! * **When roots are set:** cwd must be present and under a write root;
//!   mutation-class argv may not carry absolute destinations outside roots.
//! * Relative destinations are allowed — they land in the jailed cwd.
//!
//! Lexical normalize (collapse `.` / `..`, slash unify). Symlink reparse
//! escapes remain a residual shared with the Python regex jail; file tools
//! still use resolve-beneath.

const std = @import("std");
const builtin = @import("builtin");
const host = @import("host.zig");
const core = @import("root.zig");

pub const Error = error{
    AccessDenied,
    InvalidPath,
    OutOfMemory,
};

const is_windows = builtin.os.tag == .windows;

var g_mutex: std.atomic.Mutex = .unlocked;
var g_roots: [][]u8 = &.{};
var g_owned: bool = false;

fn lock() void {
    while (!g_mutex.tryLock()) std.atomic.spinLoopHint();
}

fn unlock() void {
    g_mutex.unlock();
}

fn freeRootsLocked(allocator: std.mem.Allocator) void {
    if (!g_owned) {
        g_roots = &.{};
        return;
    }
    for (g_roots) |entry| allocator.free(entry);
    allocator.free(g_roots);
    g_roots = &.{};
    g_owned = false;
}

/// Replace the process-global write roots. Empty `roots` clears the jail
/// (Full / unbound). Each root is stored normalized (slashes, no trailing sep).
pub fn setRoots(allocator: std.mem.Allocator, roots: []const []const u8) Error!void {
    var list: std.ArrayList([]u8) = .empty;
    errdefer {
        for (list.items) |p| allocator.free(p);
        list.deinit(allocator);
    }
    for (roots) |raw| {
        const trimmed = trimSpace(raw);
        if (trimmed.len == 0) continue;
        const norm = try normalizePathAlloc(allocator, trimmed, "");
        try list.append(allocator, norm);
    }
    const owned = try list.toOwnedSlice(allocator);

    lock();
    defer unlock();
    freeRootsLocked(allocator);
    g_roots = owned;
    g_owned = true;
}

pub fn clearRoots(allocator: std.mem.Allocator) void {
    lock();
    defer unlock();
    freeRootsLocked(allocator);
}

pub fn rootsActive() bool {
    lock();
    defer unlock();
    return g_roots.len > 0;
}

/// Snapshot current roots into `allocator` (caller frees each string + slice).
pub fn copyRoots(allocator: std.mem.Allocator) Error![][]u8 {
    lock();
    defer unlock();
    const out = try allocator.alloc([]u8, g_roots.len);
    errdefer allocator.free(out);
    var i: usize = 0;
    errdefer {
        for (out[0..i]) |p| allocator.free(p);
    }
    while (i < g_roots.len) : (i += 1) {
        out[i] = try allocator.dupe(u8, g_roots[i]);
    }
    return out;
}

fn trimSpace(s: []const u8) []const u8 {
    return std.mem.trim(u8, s, " \t\r\n");
}

fn eqlIgnoreCase(a: []const u8, b: []const u8) bool {
    return std.ascii.eqlIgnoreCase(a, b);
}

fn startsWithIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (hay.len < needle.len) return false;
    return eqlIgnoreCase(hay[0..needle.len], needle);
}

fn preferBackslash(path: []const u8) bool {
    return is_windows or looksLikeWindowsAbs(path);
}

/// Slash-unify and collapse `.` / `..` lexically. Relative paths join `cwd`.
pub fn normalizePathAlloc(allocator: std.mem.Allocator, path: []const u8, cwd: []const u8) Error![]u8 {
    const cleaned = trimSpace(path);
    if (cleaned.len == 0) return error.InvalidPath;

    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(allocator);

    const abs = isAbsolutePath(cleaned);
    if (!abs) {
        const base = trimSpace(cwd);
        if (base.len == 0) {
            try appendNormalized(&buf, allocator, cleaned);
            return buf.toOwnedSlice(allocator) catch return error.OutOfMemory;
        }
        try appendNormalized(&buf, allocator, base);
        // Do not append a trailing sep here — appendNormalized inserts seps
        // before each new segment; a trailing sep would make `..` only pop
        // the separator and leave the last cwd segment in place.
        try appendNormalized(&buf, allocator, cleaned);
    } else {
        try appendNormalized(&buf, allocator, cleaned);
    }
    stripTrailingSep(&buf);
    return buf.toOwnedSlice(allocator) catch return error.OutOfMemory;
}

fn appendNormalized(buf: *std.ArrayList(u8), allocator: std.mem.Allocator, path: []const u8) Error!void {
    var rest = path;
    if (is_windows or looksLikeWindowsAbs(path)) {
        if (path.len >= 2 and path[1] == ':' and std.ascii.isAlphabetic(path[0])) {
            try buf.append(allocator, std.ascii.toUpper(path[0]));
            try buf.appendSlice(allocator, ":\\");
            rest = path[2..];
            if (rest.len > 0 and (rest[0] == '/' or rest[0] == '\\')) rest = rest[1..];
        } else if (path.len >= 2 and path[0] == '\\' and path[1] == '\\') {
            try buf.appendSlice(allocator, "\\\\");
            rest = path[2..];
        } else if (path.len >= 1 and (path[0] == '\\' or path[0] == '/')) {
            if (buf.items.len == 0) try buf.append(allocator, '\\');
            rest = path[1..];
        }
    } else if (path.len >= 1 and path[0] == '/') {
        try buf.append(allocator, '/');
        rest = path[1..];
    }

    var segments = std.mem.splitAny(u8, rest, "/\\");
    while (segments.next()) |seg| {
        if (seg.len == 0 or std.mem.eql(u8, seg, ".")) continue;
        if (std.mem.eql(u8, seg, "..")) {
            popSegment(buf);
            continue;
        }
        if (buf.items.len > 0) {
            const last = buf.items[buf.items.len - 1];
            if (last != '/' and last != '\\') {
                try buf.append(allocator, if (preferBackslash(buf.items)) '\\' else '/');
            }
        }
        try buf.appendSlice(allocator, seg);
    }
}

fn popSegment(buf: *std.ArrayList(u8)) void {
    if (buf.items.len == 0) return;
    // Drop a trailing separator so `..` removes the real last segment.
    while (buf.items.len > 0) {
        const last = buf.items[buf.items.len - 1];
        if (last != '/' and last != '\\') break;
        if (isDriveRoot(buf.items) or (buf.items.len == 1 and buf.items[0] == '/')) break;
        _ = buf.pop();
    }
    var i = buf.items.len;
    while (i > 0) {
        i -= 1;
        const c = buf.items[i];
        if (c == '/' or c == '\\') {
            if (isDriveRoot(buf.items[0 .. i + 1]) or isUncRoot(buf.items[0 .. i + 1]) or (i == 0 and c == '/')) {
                buf.shrinkRetainingCapacity(i + 1);
                return;
            }
            buf.shrinkRetainingCapacity(i);
            return;
        }
    }
    buf.clearRetainingCapacity();
}

fn isDriveRoot(path: []const u8) bool {
    return path.len == 3 and path[1] == ':' and (path[2] == '\\' or path[2] == '/');
}

fn isUncRoot(path: []const u8) bool {
    if (path.len < 3) return false;
    if (!(path[0] == '\\' and path[1] == '\\')) return false;
    return std.mem.indexOfScalar(u8, path[2..], '\\') == null and std.mem.indexOfScalar(u8, path[2..], '/') == null;
}

fn stripTrailingSep(buf: *std.ArrayList(u8)) void {
    while (buf.items.len > 1) {
        const last = buf.items[buf.items.len - 1];
        if (last != '/' and last != '\\') break;
        if (isDriveRoot(buf.items) or (buf.items.len == 1 and buf.items[0] == '/')) break;
        _ = buf.pop();
    }
}

pub fn looksLikeWindowsAbs(path: []const u8) bool {
    if (path.len >= 2 and path[1] == ':' and std.ascii.isAlphabetic(path[0])) return true;
    if (path.len >= 2 and path[0] == '\\' and path[1] == '\\') return true;
    return false;
}

pub fn isAbsolutePath(path: []const u8) bool {
    if (path.len == 0) return false;
    if (looksLikeWindowsAbs(path)) return true;
    if (is_windows) {
        if (path[0] == '\\' or path[0] == '/') return true;
        return false;
    }
    return path[0] == '/';
}

fn slashFoldCopy(dst: []u8, src: []const u8) []const u8 {
    const n = @min(dst.len, src.len);
    for (src[0..n], 0..) |c, i| {
        const low = std.ascii.toLower(c);
        dst[i] = if (low == '/') '\\' else low;
    }
    return dst[0..n];
}

/// Files under an auth folder that are secrets wherever that folder lives.
const auth_secret_names = [_][]const u8{
    "auth\\local_api_token",
    "auth\\provider_keys",
    "auth\\oauth",
    "auth\\host_signing_key",
};

/// True when *path* names Remedy auth secrets (read or write), as spelled or
/// lexically normalized (`//`, `.` and `..` collapsed), at any length. A
/// relative `.remedy/auth` counts too. Fails closed when out of memory.
pub fn isAuthSecretPath(path: []const u8) bool {
    if (path.len == 0) return false;
    const gpa = std.heap.smp_allocator;
    if (authSecretSpelling(gpa, path)) return true;
    const norm = normalizePathAlloc(gpa, path, "") catch |err| switch (err) {
        error.OutOfMemory => return true,
        else => return false,
    };
    defer gpa.free(norm);
    return authSecretSpelling(gpa, norm);
}

fn authSecretSpelling(gpa: std.mem.Allocator, path: []const u8) bool {
    const low = gpa.alloc(u8, path.len) catch return true;
    defer gpa.free(low);
    _ = slashFoldCopy(low, path);
    if (containsAtNameStart(low, ".remedy\\auth")) return true;
    for (auth_secret_names) |name| {
        if (containsAtNameStart(low, name)) return true;
    }

    // Custom portable home: only worth a REMEDY_HOME lookup when the path
    // already looks auth-related. Mapping the full Windows environ block just
    // to read one variable has OOMed under Zig unit-test load.
    if (std.mem.indexOf(u8, low, "auth") == null) return false;

    if (builtin.os.tag == .windows) {
        const environ: std.process.Environ = .{ .block = .global };
        if (std.process.Environ.getAlloc(environ, gpa, "REMEDY_HOME") catch null) |home| {
            defer gpa.free(home);
            if (authUnderNormalizedHome(gpa, path, home)) return true;
        }
    } else {
        const home_z = std.c.getenv("REMEDY_HOME") orelse return false;
        if (authUnderNormalizedHome(gpa, path, std.mem.span(home_z))) return true;
    }
    return false;
}

/// *needle* occurs in *hay* where a path step starts: at the beginning or
/// after a byte that cannot be part of a file name (a separator, a space or a
/// quote in a command line), so `x.remedy\auth` is not `.remedy\auth`.
fn containsAtNameStart(hay: []const u8, needle: []const u8) bool {
    var from: usize = 0;
    while (std.mem.indexOfPos(u8, hay, from, needle)) |i| {
        if (i == 0 or !isNameByte(hay[i - 1])) return true;
        from = i + 1;
    }
    return false;
}

fn isNameByte(c: u8) bool {
    return std.ascii.isAlphanumeric(c) or c == '.' or c == '_' or c == '-' or c == '~' or c >= 0x80;
}

fn authUnderNormalizedHome(gpa: std.mem.Allocator, path: []const u8, home: []const u8) bool {
    if (authUnderHome(path, home)) return true;
    const norm = normalizePathAlloc(gpa, home, "") catch return false;
    defer gpa.free(norm);
    return authUnderHome(path, norm);
}

fn authUnderHome(path: []const u8, home: []const u8) bool {
    if (home.len == 0 or path.len < home.len + 5) return false;
    if (!startsWithIgnoreCase(path, home)) return false;
    const rest = path[home.len..];
    if (rest.len < 5) return false;
    if (!(rest[0] == '/' or rest[0] == '\\')) return false;
    if (!startsWithIgnoreCase(rest[1..], "auth")) return false;
    if (rest.len == 5) return true;
    const c = rest[5];
    return c == '/' or c == '\\';
}

fn underAny(path: []const u8, roots: []const []const u8) bool {
    for (roots) |entry| {
        if (pathEqualsOrUnder(path, entry)) return true;
    }
    return false;
}

fn pathEqualsOrUnder(path: []const u8, base: []const u8) bool {
    if (path.len == 0 or base.len == 0) return false;
    if (eqlIgnoreCase(path, base)) return true;
    if (path.len < base.len + 1) return false;
    if (!startsWithIgnoreCase(path, base)) return false;
    const c = path[base.len];
    return c == '/' or c == '\\';
}

/// Return true when *path* resolves outside every write root.
pub fn pathOutsideWriteRoots(path: []const u8, roots: []const []const u8, cwd: []const u8, allocator: std.mem.Allocator) Error!bool {
    if (roots.len == 0) return false;
    const raw = trimSpace(path);
    if (raw.len == 0) return false;
    if (isAuthSecretPath(raw)) return true;
    // Unexpanded variables — cannot prove under roots → outside (fail closed).
    if (std.mem.indexOfScalar(u8, raw, '$') != null or std.mem.indexOfScalar(u8, raw, '%') != null) {
        return true;
    }
    if (isDevNull(raw)) return false;
    const norm = try normalizePathAlloc(allocator, raw, cwd);
    defer allocator.free(norm);
    if (isAuthSecretPath(norm)) return true;
    return !underAny(norm, roots);
}

fn isDevNull(raw: []const u8) bool {
    var buf: [32]u8 = undefined;
    if (raw.len > buf.len) return false;
    const t = slashFoldCopy(&buf, raw);
    return std.mem.eql(u8, t, "nul") or std.mem.eql(u8, t, "null") or
        std.mem.eql(u8, t, "con") or std.mem.eql(u8, t, "/dev/null") or
        std.mem.eql(u8, t, "\\dev\\null") or std.mem.eql(u8, t, "\\\\.\\nul");
}

const mutation_heads = [_][]const u8{
    "copy",     "xcopy",       "robocopy",    "cp",          "move",        "mv",
    "del",      "erase",       "rm",          "rd",          "rmdir",       "ren",
    "mkdir",    "md",          "set-content", "out-file",    "add-content", "tee-object",
    "new-item", "copy-item",   "move-item",   "remove-item", "rename-item", "ni",
    "sc",       "tee",         "ac",          "ri",          "mi",          "cpi",
    "si",       "fsutil",      "mklink",      "touch",
};

fn executableBasename(path: []const u8) []const u8 {
    const base = std.fs.path.basename(path);
    if (base.len >= 4 and eqlIgnoreCase(base[base.len - 4 ..], ".exe")) {
        return base[0 .. base.len - 4];
    }
    return base;
}

fn looksLikeMutationArgv(argv: []const []const u8) bool {
    if (argv.len == 0) return false;
    const head = executableBasename(argv[0]);
    for (mutation_heads) |name| {
        if (eqlIgnoreCase(head, name)) return true;
    }
    var stack: [4096]u8 = undefined;
    var len: usize = 0;
    for (argv[1..]) |arg| {
        if (len >= stack.len) break;
        if (len > 0 and len < stack.len) {
            stack[len] = ' ';
            len += 1;
        }
        const take = @min(arg.len, stack.len - len);
        @memcpy(stack[len..][0..take], arg[0..take]);
        len += take;
    }
    const text = stack[0..len];
    if (containsIgnoreCase(text, "set-content") or containsIgnoreCase(text, "out-file") or
        containsIgnoreCase(text, "add-content") or containsIgnoreCase(text, "copy-item") or
        containsIgnoreCase(text, "move-item") or containsIgnoreCase(text, "remove-item") or
        containsIgnoreCase(text, "new-item") or containsIgnoreCase(text, "rename-item") or
        containsIgnoreCase(text, "tee-object") or containsIgnoreCase(text, " copy ") or
        containsIgnoreCase(text, " del ") or containsIgnoreCase(text, " mkdir "))
        return true;
    if (hasRedirectWrite(text)) return true;
    return false;
}

fn containsIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (needle.len == 0 or hay.len < needle.len) return false;
    var i: usize = 0;
    while (i + needle.len <= hay.len) : (i += 1) {
        if (eqlIgnoreCase(hay[i .. i + needle.len], needle)) return true;
    }
    return false;
}

fn hasRedirectWrite(text: []const u8) bool {
    var i: usize = 0;
    while (i < text.len) : (i += 1) {
        if (text[i] != '>') continue;
        if (i > 0 and text[i - 1] == '&') continue;
        if (i + 1 < text.len and text[i + 1] == '&') continue;
        return true;
    }
    return false;
}

/// `posix`: only `-` starts a switch. A `/x` switch is Windows syntax; on a
/// POSIX command line `/etc` or `/tmp` is a path.
fn isFlagArg(arg: []const u8, posix: bool) bool {
    if (arg.len == 0) return false;
    if (arg[0] == '-') return true;
    if (posix) return false;
    if (arg[0] == '/' or arg[0] == '\\') {
        if (arg.len >= 2 and looksLikeWindowsAbs(arg)) return false;
        // `/c`, `/K`, `/S` — short alnum switch bodies, not `/Users/...`
        var i: usize = 1;
        while (i < arg.len and i <= 6) : (i += 1) {
            if (!std.ascii.isAlphanumeric(arg[i])) break;
        }
        const body_len = i - 1;
        if (body_len >= 1 and body_len <= 5) {
            if (i == arg.len) return true;
            if (arg[i] == ':') return true;
        }
    }
    return false;
}

/// *path* names an auth secret as given, or once a relative path is joined to
/// *cwd* (`auth/local_api_token` run from the Remedy home).
fn authSecretFrom(allocator: std.mem.Allocator, path: []const u8, cwd: []const u8) Error!bool {
    if (isAuthSecretPath(path)) return true;
    const raw = trimSpace(path);
    if (raw.len == 0 or isAbsolutePath(raw) or trimSpace(cwd).len == 0) return false;
    const joined = normalizePathAlloc(allocator, raw, cwd) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return false,
    };
    defer allocator.free(joined);
    return isAuthSecretPath(joined);
}

/// Enforce workdir + auth + mutation dest constraints for an authorized spawn.
pub fn checkSpawn(allocator: std.mem.Allocator, argv: []const []const u8, cwd: []const u8) Error!void {
    if (isAuthSecretPath(cwd)) return error.AccessDenied;
    for (argv) |arg| {
        if (try authSecretFrom(allocator, arg, cwd)) return error.AccessDenied;
    }

    const roots = try copyRoots(allocator);
    defer {
        for (roots) |r| allocator.free(r);
        allocator.free(roots);
    }
    if (roots.len == 0) return;

    const cwd_trim = trimSpace(cwd);
    if (cwd_trim.len == 0) return error.AccessDenied;
    if (try pathOutsideWriteRoots(cwd_trim, roots, "", allocator)) return error.AccessDenied;

    if (try scriptWritesOutside(allocator, argv, roots, cwd_trim)) return error.AccessDenied;

    if (!looksLikeMutationArgv(argv)) return;
    if (argv.len < 2) return;
    for (argv[1..]) |arg| {
        const target = if (isFlagArg(arg, !is_windows)) flagValue(arg, !is_windows) else arg;
        if (try destinationOutside(target, roots, cwd_trim, allocator)) return error.AccessDenied;
    }
}

/// The path a switch carries in itself: `--target-directory=/x`, `-o=/x`,
/// and on Windows `-Path:C:\x` or `/LOG:C:\x`.
fn flagValue(arg: []const u8, posix: bool) []const u8 {
    if (arg.len == 0) return "";
    if (std.mem.indexOfScalar(u8, arg, '=')) |i| return arg[i + 1 ..];
    if (!posix) {
        if (std.mem.indexOfScalar(u8, arg, ':')) |i| return arg[i + 1 ..];
    }
    return "";
}

fn destinationOutside(arg: []const u8, roots: []const []const u8, cwd: []const u8, allocator: std.mem.Allocator) Error!bool {
    const escapeish = isAbsolutePath(arg) or
        std.mem.indexOf(u8, arg, "..") != null or
        (arg.len > 0 and arg[0] == '~');
    if (!escapeish) return false;
    return pathOutsideWriteRoots(arg, roots, cwd, allocator);
}

// ---- a script handed to a shell ----------------------------------------------
//
// `bash -lc "cp a /etc/x"` is one argv element to the checks above, so they
// never saw its words. The script is split the way the shell would (quotes,
// `;` `&&` `|` and friends, redirects) and each command in it is held to the
// same rule: a mutation command, or a `>` redirect, may not name a
// destination outside the roots, and after a `cd` out of them it may not name
// any. Words that use a variable are left to the Auto-mode shell guard, which
// asks for any of them; `~` is the home folder.

const posix_shells = [_][]const u8{ "sh", "bash", "zsh", "dash", "ksh", "ash", "mksh" };

/// Words that run the command after them.
const command_prefixes = [_][]const u8{
    "sudo",  "doas",    "env",  "command", "exec", "nohup", "nice", "time",
    "xargs", "builtin", "then", "do",      "else", "elif",  "!",    "{",
};

const cd_heads = [_][]const u8{ "cd", "chdir", "pushd", "set-location", "sl", "push-location" };

fn nameIn(name: []const u8, list: []const []const u8) bool {
    for (list) |entry| {
        if (eqlIgnoreCase(name, entry)) return true;
    }
    return false;
}

const max_script_depth: u8 = 3;

const Script = struct {
    text: []const u8,
    /// POSIX shell rules: `-` switches, backslash escapes.
    posix: bool,
    /// `'` quotes (sh, PowerShell) — not in cmd.exe.
    single_quotes: bool,
};

/// The script a shell wrapper argv runs, or null. `owned` tells the caller
/// whether `text` was joined (and must be freed).
fn shellScript(allocator: std.mem.Allocator, argv: []const []const u8, owned: *bool) Error!?Script {
    owned.* = false;
    if (argv.len < 2) return null;
    const head = executableBasename(argv[0]);
    if (nameIn(head, &posix_shells)) {
        var saw_c = false;
        for (argv[1..]) |arg| {
            if (arg.len > 1 and arg[0] == '-' and arg[1] != '-') {
                if (std.mem.indexOfScalar(u8, arg, 'c') != null) saw_c = true;
                continue;
            }
            if (saw_c) return .{ .text = arg, .posix = true, .single_quotes = true };
        }
        return null;
    }
    const is_cmd = eqlIgnoreCase(head, "cmd");
    const is_ps = eqlIgnoreCase(head, "powershell") or eqlIgnoreCase(head, "pwsh");
    if (!is_cmd and !is_ps) return null;
    for (argv[1..], 1..) |arg, i| {
        const starts = if (is_cmd)
            eqlIgnoreCase(arg, "/c") or eqlIgnoreCase(arg, "/k")
        else
            eqlIgnoreCase(arg, "-c") or eqlIgnoreCase(arg, "-command");
        if (!starts or i + 1 >= argv.len) continue;
        var buf: std.ArrayList(u8) = .empty;
        errdefer buf.deinit(allocator);
        for (argv[i + 1 ..], 0..) |part, n| {
            if (n > 0) try buf.append(allocator, ' ');
            try buf.appendSlice(allocator, part);
        }
        owned.* = true;
        return .{ .text = try buf.toOwnedSlice(allocator), .posix = false, .single_quotes = is_ps };
    }
    return null;
}

fn scriptWritesOutside(allocator: std.mem.Allocator, argv: []const []const u8, roots: []const []const u8, cwd: []const u8) Error!bool {
    var owned = false;
    const script = (try shellScript(allocator, argv, &owned)) orelse return false;
    defer if (owned) allocator.free(script.text);
    return scriptTextWritesOutside(allocator, script, roots, cwd, 0);
}

const ScriptState = struct {
    allocator: std.mem.Allocator,
    roots: []const []const u8,
    cwd: []const u8,
    posix: bool,
    depth: u8,
    command_start: bool = true,
    mutating: bool = false,
    shell_head: bool = false,
    shell_c: bool = false,
    cd_pending: bool = false,
    /// A `cd` left the roots (or went somewhere unknown): relative names no
    /// longer land inside them.
    cwd_escaped: bool = false,
    redirect_next: bool = false,
    outside: bool = false,

    fn endCommand(self: *ScriptState) void {
        // A bare `cd` goes home.
        if (self.cd_pending) self.cwd_escaped = true;
        self.command_start = true;
        self.mutating = false;
        self.shell_head = false;
        self.shell_c = false;
        self.cd_pending = false;
        self.redirect_next = false;
    }

    fn word(self: *ScriptState, raw: []const u8, has_var: bool) Error!void {
        if (self.outside) return;
        if (self.redirect_next) {
            self.redirect_next = false;
            try self.destination(raw, has_var);
            return;
        }
        if (self.command_start) {
            if (nameIn(executableBasename(raw), &command_prefixes) or isAssignment(raw)) return;
            // a switch of a prefix word (`nice -n 5`, `env -i`)
            if (raw.len > 0 and raw[0] == '-') return;
            self.command_start = false;
            const head = executableBasename(raw);
            self.mutating = nameIn(head, &mutation_heads);
            self.shell_head = nameIn(head, &posix_shells);
            self.cd_pending = nameIn(head, &cd_heads);
            return;
        }
        if (self.cd_pending) {
            if (raw.len > 1 and raw[0] == '-') return; // `cd -P dir`
            self.cd_pending = false;
            if (has_var or std.mem.eql(u8, raw, "-") or (raw.len > 0 and raw[0] == '~') or
                self.cwd_escaped or try destinationOutside(raw, self.roots, self.cwd, self.allocator))
            {
                self.cwd_escaped = true;
            }
            return;
        }
        if (self.shell_head) {
            if (raw.len > 1 and raw[0] == '-' and raw[1] != '-') {
                if (std.mem.indexOfScalar(u8, raw, 'c') != null) self.shell_c = true;
                return;
            }
            if (self.shell_c and !has_var) {
                self.shell_c = false;
                if (self.depth + 1 >= max_script_depth) {
                    self.outside = true;
                    return;
                }
                const nested: Script = .{ .text = raw, .posix = true, .single_quotes = true };
                if (try scriptTextWritesOutside(self.allocator, nested, self.roots, self.cwd, self.depth + 1)) self.outside = true;
                return;
            }
        }
        if (!self.mutating) return;
        const target = if (isFlagArg(raw, self.posix)) flagValue(raw, self.posix) else raw;
        try self.destination(target, has_var);
    }

    fn destination(self: *ScriptState, raw: []const u8, has_var: bool) Error!void {
        if (has_var or raw.len == 0) return;
        if (isStdStream(raw)) return;
        if (self.cwd_escaped and !isAbsolutePath(raw)) {
            if (!isDevNull(raw)) self.outside = true;
            return;
        }
        if (raw[0] == '~') {
            if (raw.len > 1 and raw[1] != '/' and raw[1] != '\\') {
                self.outside = true; // ~user: someone else's home
                return;
            }
            const home = host.parentEnvGet(self.allocator, if (is_windows) "USERPROFILE" else "HOME") orelse {
                self.outside = true;
                return;
            };
            defer self.allocator.free(home);
            const full = try std.mem.concat(self.allocator, u8, &.{ home, raw[1..] });
            defer self.allocator.free(full);
            if (try pathOutsideWriteRoots(full, self.roots, self.cwd, self.allocator)) self.outside = true;
            return;
        }
        if (try destinationOutside(raw, self.roots, self.cwd, self.allocator)) self.outside = true;
    }
};

fn isAssignment(text: []const u8) bool {
    const eq = std.mem.indexOfScalar(u8, text, '=') orelse return false;
    if (eq == 0) return false;
    for (text[0..eq]) |c| {
        if (!(std.ascii.isAlphanumeric(c) or c == '_')) return false;
    }
    return true;
}

fn isStdStream(raw: []const u8) bool {
    return std.mem.eql(u8, raw, "/dev/stdout") or std.mem.eql(u8, raw, "/dev/stderr");
}

fn scriptTextWritesOutside(
    allocator: std.mem.Allocator,
    script: Script,
    roots: []const []const u8,
    cwd: []const u8,
    depth: u8,
) Error!bool {
    const text = script.text;
    const posix = script.posix;
    var state: ScriptState = .{ .allocator = allocator, .roots = roots, .cwd = cwd, .posix = posix, .depth = depth };
    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(allocator);
    var in_word = false;
    var has_var = false;
    var quote: u8 = 0;
    var i: usize = 0;
    while (i < text.len) : (i += 1) {
        const c = text[i];
        if (quote != 0) {
            if (c == quote) {
                quote = 0;
            } else if (posix and quote == '"' and c == '\\' and i + 1 < text.len) {
                i += 1;
                try buf.append(allocator, text[i]);
            } else {
                if ((c == '$' and quote == '"') or (!posix and c == '%')) has_var = true;
                try buf.append(allocator, c);
            }
            continue;
        }
        if (c == '"' or (c == '\'' and script.single_quotes)) {
            quote = c;
            in_word = true;
            continue;
        }
        switch (c) {
            ' ', '\t', '\r', '\n', ';', '&', '|', '(', ')', '`', '<', '>' => {
                if (in_word) {
                    try state.word(buf.items, has_var);
                    buf.clearRetainingCapacity();
                    in_word = false;
                    has_var = false;
                }
                if (c == '>') {
                    // `>&2` / `2>&1` duplicate a stream; anything else writes a file.
                    while (i + 1 < text.len and text[i + 1] == '>') i += 1;
                    if (i + 1 < text.len and text[i + 1] == '&') {
                        i += 1;
                    } else {
                        state.redirect_next = true;
                    }
                } else if (c != ' ' and c != '\t' and c != '\r' and c != '<') {
                    state.endCommand();
                }
            },
            else => {
                if (posix and c == '\\' and i + 1 < text.len) {
                    i += 1;
                    try buf.append(allocator, text[i]);
                } else if (!posix and !script.single_quotes and c == '^') {
                    // cmd.exe's escape: `c^opy` is `copy`
                    if (i + 1 < text.len) {
                        i += 1;
                        try buf.append(allocator, text[i]);
                    }
                } else {
                    if (c == '$' or (!posix and c == '%')) has_var = true;
                    try buf.append(allocator, c);
                }
                in_word = true;
            },
        }
        if (state.outside) return true;
    }
    if (in_word) try state.word(buf.items, has_var);
    // An unfinished quote or a dangling `>` cannot be read: fail closed.
    if (quote != 0 or state.redirect_next) return true;
    state.endCommand();
    return state.outside;
}

/// Check a single path against the installed roots (and auth). For ABI tests.
pub fn checkPath(allocator: std.mem.Allocator, path: []const u8, cwd: []const u8) Error!void {
    if (try authSecretFrom(allocator, path, cwd)) return error.AccessDenied;
    const roots = try copyRoots(allocator);
    defer {
        for (roots) |r| allocator.free(r);
        allocator.free(roots);
    }
    if (roots.len == 0) return;
    if (try pathOutsideWriteRoots(path, roots, cwd, allocator)) return error.AccessDenied;
}

test "normalize collapses dotdot and joins cwd" {
    const allocator = std.testing.allocator;
    if (is_windows) {
        const p = try normalizePathAlloc(allocator, "..\\sibling\\a.txt", "C:\\proj\\sub");
        defer allocator.free(p);
        try std.testing.expectEqualStrings("C:\\proj\\sibling\\a.txt", p);
        const abs = try normalizePathAlloc(allocator, "C:/Users/Public/x.txt", "");
        defer allocator.free(abs);
        try std.testing.expectEqualStrings("C:\\Users\\Public\\x.txt", abs);
    } else {
        const p = try normalizePathAlloc(allocator, "../sibling/a.txt", "/proj/sub");
        defer allocator.free(p);
        try std.testing.expectEqualStrings("/proj/sibling/a.txt", p);
    }
}

test "auth secret paths are detected" {
    try std.testing.expect(isAuthSecretPath("C:\\Users\\Ada\\.remedy\\auth\\local_api_token"));
    try std.testing.expect(isAuthSecretPath("/home/ada/.remedy/auth/provider_keys"));
    try std.testing.expect(!isAuthSecretPath("C:\\Users\\Ada\\.remedy\\config.json"));
    try std.testing.expect(!isAuthSecretPath("C:\\proj\\auth\\notes.txt"));
}

test "auth secret paths are detected in every spelling" {
    try std.testing.expect(isAuthSecretPath("/home/ada/.remedy//auth/local_api_token"));
    try std.testing.expect(isAuthSecretPath("/home/ada/.remedy/./auth/local_api_token"));
    try std.testing.expect(isAuthSecretPath("/home/ada/.remedy/x/../auth/local_api_token"));
    try std.testing.expect(isAuthSecretPath("C:\\Users\\Ada\\.remedy\\.\\auth\\x"));
    try std.testing.expect(isAuthSecretPath(".remedy/auth/local_api_token"));
    try std.testing.expect(isAuthSecretPath("cat .remedy/auth/provider_keys"));
    try std.testing.expect(isAuthSecretPath("/srv/portable-home/auth/host_signing_key"));
    try std.testing.expect(!isAuthSecretPath("/home/ada/x.remedy/auth/notes"));

    // No length cap: padding past any fixed buffer still names the secret.
    var long: [3000]u8 = undefined;
    @memset(long[0..2000], '/');
    const tail = "home/ada/.remedy/auth/local_api_token";
    @memcpy(long[2000..][0..tail.len], tail);
    try std.testing.expect(isAuthSecretPath(long[0 .. 2000 + tail.len]));

    // Relative to the cwd the command runs in.
    const allocator = std.testing.allocator;
    clearRoots(allocator);
    const cmd = if (is_windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/cat";
    const home = if (is_windows) "C:\\Users\\Ada\\.remedy" else "/home/ada/.remedy";
    try std.testing.expectError(error.AccessDenied, checkSpawn(allocator, &.{ cmd, "auth/local_api_token" }, home));
    try std.testing.expectError(error.AccessDenied, checkPath(allocator, "auth", home));
    try checkSpawn(allocator, &.{ cmd, "config.json" }, home);
}

test "workdir jail denies outside cwd and allows project writes" {
    const allocator = std.testing.allocator;
    const project = if (is_windows) "C:\\focus\\proj" else "/focus/proj";
    const outside = if (is_windows) "C:\\Users\\Public" else "/tmp/outside";
    const inside = if (is_windows) "C:\\focus\\proj\\src" else "/focus/proj/src";
    try setRoots(allocator, &.{project});
    defer clearRoots(allocator);

    const cmd = if (is_windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/sh";
    try checkSpawn(allocator, &.{ cmd, "/c", "echo", "ok" }, inside);
    try std.testing.expectError(error.AccessDenied, checkSpawn(allocator, &.{ cmd, "/c", "echo", "ok" }, outside));
    try std.testing.expectError(error.AccessDenied, checkSpawn(allocator, &.{ cmd, "/c", "echo", "ok" }, ""));

    if (is_windows) {
        try std.testing.expectError(
            error.AccessDenied,
            checkSpawn(allocator, &.{ cmd, "/c", "copy", "a.txt", "C:\\Users\\Public\\pwn.txt" }, inside),
        );
        try checkSpawn(allocator, &.{ cmd, "/c", "copy", "a.txt", "out.txt" }, inside);
    } else {
        const cp = "/bin/cp";
        try std.testing.expectError(
            error.AccessDenied,
            checkSpawn(allocator, &.{ cp, "a.txt", "/tmp/outside/pwn.txt" }, inside),
        );
        try checkSpawn(allocator, &.{ cp, "a.txt", "out.txt" }, inside);
    }

    clearRoots(allocator);
    const auth = if (is_windows) "C:\\Users\\Ada\\.remedy\\auth\\local_api_token" else "/home/ada/.remedy/auth/local_api_token";
    try std.testing.expectError(
        error.AccessDenied,
        checkSpawn(allocator, &.{ cmd, "/c", "type", auth }, if (is_windows) "C:\\Windows" else "/tmp"),
    );
}

test "workdir jail reads scripts handed to a shell, POSIX paths and --opt=value" {
    if (is_windows) return error.SkipZigTest;
    const allocator = std.testing.allocator;
    try setRoots(allocator, &.{"/focus/proj"});
    defer clearRoots(allocator);
    const cwd = "/focus/proj/src";

    const denied = [_][]const []const u8{
        &.{ "/bin/bash", "-lc", "cp a /etc/x" },
        &.{ "/bin/sh", "-c", "cp a /etc/x" },
        &.{ "/bin/sh", "-c", "echo hi > /etc/x" },
        &.{ "/bin/sh", "-c", "echo hi>>/tmp/outside/x" },
        &.{ "/bin/sh", "-c", "ls | tee /etc/x" },
        &.{ "/bin/sh", "-c", "cd src && mv a /tmp/outside/b" },
        &.{ "/bin/sh", "-c", "FOO=1 sudo cp a /etc/x" },
        &.{ "/bin/sh", "-c", "c\\p a '/etc/x'" },
        &.{ "/bin/bash", "-c", "bash -c 'cp a /etc/x'" },
        &.{ "/bin/sh", "-c", "cd /tmp && touch x" },
        &.{ "/bin/sh", "-c", "cp a ~/x" },
        &.{ "/bin/sh", "-c", "cp --target-directory=/etc a" },
        // a POSIX path is not a Windows `/x` switch
        &.{ "/bin/cp", "a", "/etc" },
        &.{ "/bin/mv", "a", "/tmp" },
        &.{ "/bin/cp", "--target-directory=/tmp/outside", "a" },
    };
    for (denied) |argv| {
        checkSpawn(allocator, argv, cwd) catch |err| {
            try std.testing.expectEqual(error.AccessDenied, err);
            continue;
        };
        std.debug.print("not denied: {s}\n", .{argv[argv.len - 1]});
        return error.TestExpectedError;
    }

    const allowed = [_][]const []const u8{
        &.{ "/bin/bash", "-lc", "cat /etc/hosts > out.txt" },
        &.{ "/bin/sh", "-c", "go test ./... 2>/dev/null" },
        &.{ "/bin/sh", "-c", "make 2>&1 | tee build.log" },
        &.{ "/bin/sh", "-c", "cp a b && echo '>' /etc/x" },
        &.{ "/bin/sh", "-c", "cp a \"$REMEDY_SCRATCH/x\"" },
        &.{ "/bin/sh", "-c", "cd ../src && cp a b" },
        &.{ "/bin/sh", "-c", "ls /etc; mkdir -p out/dir" },
        &.{ "/bin/cp", "a", "out.txt" },
        &.{ "/bin/cp", "--target-directory=/focus/proj/out", "a" },
        &.{ "/bin/ls", "/etc" },
    };
    for (allowed) |argv| {
        checkSpawn(allocator, argv, cwd) catch |err| {
            std.debug.print("denied: {s}\n", .{argv[argv.len - 1]});
            return err;
        };
    }
}

test "empty roots skip workdir jail" {
    const allocator = std.testing.allocator;
    clearRoots(allocator);
    const cmd = if (is_windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/sh";
    const anywhere = if (is_windows) "C:\\Users\\Public" else "/tmp";
    try checkSpawn(allocator, &.{ cmd, "/c", "echo", "ok" }, anywhere);
}

// ---- C ABI (additive on ABI 5) ---------------------------------------------

const ok_status: i32 = @intFromEnum(core.Status.ok);
const invalid_status: i32 = @intFromEnum(core.Status.invalid_argument);
const denied_status: i32 = @intFromEnum(core.Status.access_denied);
const failed_status: i32 = @intFromEnum(core.Status.operation_failed);

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn jailStatus(err: Error) i32 {
    return switch (err) {
        error.AccessDenied => denied_status,
        error.InvalidPath => invalid_status,
        error.OutOfMemory => failed_status,
    };
}

/// Install write roots from a JSON string array. Empty array / empty input clears.
export fn remedy_core_write_jail_set_roots(
    roots_json: ?[*]const u8,
    roots_len: usize,
) callconv(.c) i32 {
    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const raw = std.mem.trim(u8, slice(roots_json, roots_len), " \t\r\n");
    if (raw.len == 0 or std.mem.eql(u8, raw, "[]") or std.mem.eql(u8, raw, "null")) {
        clearRoots(host.allocator);
        return ok_status;
    }
    const parsed = std.json.parseFromSliceLeaky([]const []const u8, arena.allocator(), raw, .{}) catch return invalid_status;
    setRoots(host.allocator, parsed) catch |err| return jailStatus(err);
    return ok_status;
}

/// Clear write roots (Full / unbound workdir jail).
export fn remedy_core_write_jail_clear() callconv(.c) i32 {
    clearRoots(host.allocator);
    return ok_status;
}

/// Check *path* (optional *cwd* for relatives) against installed roots + auth.
/// Returns OK, ACCESS_DENIED, or INVALID_ARGUMENT.
export fn remedy_core_write_jail_check_path(
    path_ptr: ?[*]const u8,
    path_len: usize,
    cwd_ptr: ?[*]const u8,
    cwd_len: usize,
) callconv(.c) i32 {
    const path = slice(path_ptr, path_len);
    if (path.len == 0) return invalid_status;
    checkPath(host.allocator, path, slice(cwd_ptr, cwd_len)) catch |err| return jailStatus(err);
    return ok_status;
}

/// Check argv_json + cwd against the installed write jail (same gate as spawn).
export fn remedy_core_write_jail_check_spawn(
    argv_json: ?[*]const u8,
    argv_len: usize,
    cwd_ptr: ?[*]const u8,
    cwd_len: usize,
) callconv(.c) i32 {
    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;
    checkSpawn(host.allocator, argv, slice(cwd_ptr, cwd_len)) catch |err| return jailStatus(err);
    return ok_status;
}
