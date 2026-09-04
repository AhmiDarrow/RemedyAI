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

/// True when *path* names Remedy auth secrets (read or write).
pub fn isAuthSecretPath(path: []const u8) bool {
    if (path.len == 0) return false;
    var lower_buf: [1024]u8 = undefined;
    const low = slashFoldCopy(&lower_buf, path);
    if (std.mem.indexOf(u8, low, "\\.remedy\\auth") != null) return true;
    if (std.mem.indexOf(u8, low, "\\auth\\local_api_token") != null) return true;
    if (std.mem.indexOf(u8, low, "\\auth\\provider_keys") != null) return true;
    if (std.mem.indexOf(u8, low, "\\auth\\oauth") != null) return true;

    // Custom portable home: only worth a REMEDY_HOME lookup when the path
    // already looks auth-related. Mapping the full Windows environ block just
    // to read one variable has OOMed under Zig unit-test load.
    if (std.mem.indexOf(u8, low, "auth") == null) return false;

    if (builtin.os.tag == .windows) {
        const environ: std.process.Environ = .{ .block = .global };
        if (std.process.Environ.getAlloc(environ, std.heap.smp_allocator, "REMEDY_HOME") catch null) |home| {
            defer std.heap.smp_allocator.free(home);
            if (authUnderHome(path, home)) return true;
        }
    } else {
        const home_z = std.c.getenv("REMEDY_HOME") orelse return false;
        if (authUnderHome(path, std.mem.span(home_z))) return true;
    }
    return false;
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

fn isFlagArg(arg: []const u8) bool {
    if (arg.len == 0) return false;
    if (arg[0] == '-') return true;
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

/// Enforce workdir + auth + mutation dest constraints for an authorized spawn.
pub fn checkSpawn(allocator: std.mem.Allocator, argv: []const []const u8, cwd: []const u8) Error!void {
    if (isAuthSecretPath(cwd)) return error.AccessDenied;
    for (argv) |arg| {
        if (isAuthSecretPath(arg)) return error.AccessDenied;
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

    if (!looksLikeMutationArgv(argv)) return;
    if (argv.len < 2) return;
    for (argv[1..]) |arg| {
        if (isFlagArg(arg)) continue;
        const escapeish = isAbsolutePath(arg) or
            std.mem.indexOf(u8, arg, "..") != null or
            (arg.len > 0 and arg[0] == '~');
        if (!escapeish) continue;
        if (try pathOutsideWriteRoots(arg, roots, cwd_trim, allocator)) return error.AccessDenied;
    }
}

/// Check a single path against the installed roots (and auth). For ABI tests.
pub fn checkPath(allocator: std.mem.Allocator, path: []const u8, cwd: []const u8) Error!void {
    if (isAuthSecretPath(path)) return error.AccessDenied;
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
