//! Persistent HostSession orchestration (Phase 3).
//!
//! Owns the shell-host protocol that Python `execution/host/session.py` used:
//! session argv + boot, sentinel wrap/split, VT/echo strip, interactive-prompt
//! kill, cwd probe, and ConPTY or piped I/O. Spawns go through the same
//! ConPTY / piped primitives as ABI 5; production callers use the authorized
//! open export (policy + HMAC token + write-jail).

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const shell_ir = @import("shell_ir.zig");
const spawn_auth = @import("spawn_auth.zig");

pub const is_windows = builtin.os.tag == .windows;
const windows_host = if (is_windows) @import("host_windows.zig") else struct {};
const windows_conpty = if (is_windows) @import("conpty_windows.zig") else struct {};

const Status = root.Status;
const Error = host.Error;
const allocator = host.allocator;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const denied_status: i32 = @intFromEnum(Status.access_denied);
const failed_status: i32 = @intFromEnum(Status.operation_failed);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

pub const sentinel_prefix = "REMEDY_HOST_DONE_";

const prompt_markers = [_][]const u8{
    "password:",
    "[y/n]",
    "(y/n)",
    "are you sure",
    "press any key",
    "enter passphrase",
};

const default_subject = "agent:remedy";
const default_scope = "workspace:local";

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn statusOf(err: Error) i32 {
    return host.statusOf(err);
}

fn authStatus(err: anyerror) i32 {
    return switch (err) {
        error.PolicyDenied,
        error.OwnerConfirmationRequired,
        error.AccessDenied,
        error.InvalidToken,
        error.Expired,
        error.NotYetValid,
        error.Replayed,
        error.SubjectMismatch,
        error.ScopeMismatch,
        error.OperationMismatch,
        => denied_status,
        error.InvalidArgument,
        error.InvalidArguments,
        error.InvalidGrant,
        error.InvalidPath,
        => invalid_status,
        error.Unsupported => unsupported_status,
        error.OutOfMemory => failed_status,
        else => failed_status,
    };
}

fn deliverBytes(result: Error![]u8, out_ptr: ?*?[*]u8, out_len: ?*usize) i32 {
    const ptr_slot = out_ptr orelse return invalid_status;
    const len_slot = out_len orelse return invalid_status;
    const bytes = result catch |err| {
        ptr_slot.* = null;
        len_slot.* = 0;
        return statusOf(err);
    };
    ptr_slot.* = bytes.ptr;
    len_slot.* = bytes.len;
    return ok_status;
}

// ---------------------------------------------------------------------------
// Protocol (portable; byte-parity with Python session.py)
// ---------------------------------------------------------------------------

pub fn defaultHost() []const u8 {
    return if (is_windows) "cmd" else "posix";
}

pub fn cwdCommand(host_name: []const u8) []const u8 {
    if (std.mem.eql(u8, host_name, "cmd")) return "cd";
    if (std.mem.eql(u8, host_name, "pwsh")) return "(Get-Location).Path";
    return "pwd";
}

pub fn bootCommands(host_name: []const u8) []const u8 {
    if (std.mem.eql(u8, host_name, "cmd") and is_windows) {
        return "chcp 65001 >NUL & @echo off";
    }
    if (std.mem.eql(u8, host_name, "pwsh")) {
        return "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()";
    }
    return "";
}

pub fn wrapWithSentinel(arena: std.mem.Allocator, host_name: []const u8, command: []const u8, sentinel: []const u8) Error![]u8 {
    if (std.mem.eql(u8, host_name, "pwsh")) {
        return std.fmt.allocPrint(arena, "{s}\nWrite-Output \"{s}:$LASTEXITCODE\"\n", .{ command, sentinel }) catch return error.OutOfMemory;
    }
    if (std.mem.eql(u8, host_name, "cmd") or is_windows) {
        return std.fmt.allocPrint(arena, "{s}\necho {s}:%ERRORLEVEL%\n", .{ command, sentinel }) catch return error.OutOfMemory;
    }
    return std.fmt.allocPrint(arena, "{s}\necho {s}:$?\n", .{ command, sentinel }) catch return error.OutOfMemory;
}

fn isDigit(c: u8) bool {
    return c >= '0' and c <= '9';
}

/// Expanded sentinel only: `SENTINEL:<digits>` ended by CR/LF/ESC. Rejects the
/// ConPTY echo of the unexpanded `%ERRORLEVEL%` / `$LASTEXITCODE` / `$?` form.
pub fn findSentinel(haystack: []const u8, sentinel: []const u8) ?struct { start: usize, end: usize, code_start: usize, code_end: usize } {
    var search_from: usize = 0;
    while (search_from < haystack.len) {
        const rel = std.mem.indexOf(u8, haystack[search_from..], sentinel) orelse return null;
        const start = search_from + rel;
        const after = start + sentinel.len;
        if (after >= haystack.len or haystack[after] != ':') {
            search_from = start + 1;
            continue;
        }
        const code_start = after + 1;
        var i = code_start;
        if (i < haystack.len and haystack[i] == '-') i += 1;
        while (i < haystack.len and isDigit(haystack[i])) : (i += 1) {}
        if (i < haystack.len) {
            const ch = haystack[i];
            if (ch == '\r' or ch == '\n' or ch == 0x1b) {
                return .{ .start = start, .end = i, .code_start = code_start, .code_end = i };
            }
        }
        search_from = start + 1;
    }
    return null;
}

pub fn sentinelDone(buf: []const u8, sentinel: []const u8) bool {
    return findSentinel(buf, sentinel) != null;
}

/// Strip OSC / CSI / ESC sequences and BEL/BS (Python `_VT_RE`).
pub fn stripVt(gpa: std.mem.Allocator, text: []const u8) Error![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    var i: usize = 0;
    while (i < text.len) {
        const c = text[i];
        if (c == 0x1b) {
            if (i + 1 >= text.len) break;
            const n = text[i + 1];
            if (n == ']') {
                // OSC ... BEL | ST
                i += 2;
                while (i < text.len) {
                    if (text[i] == 0x07) {
                        i += 1;
                        break;
                    }
                    if (text[i] == 0x1b and i + 1 < text.len and text[i + 1] == '\\') {
                        i += 2;
                        break;
                    }
                    i += 1;
                }
                continue;
            }
            if (n == '[') {
                // CSI: intermediates then final
                i += 2;
                while (i < text.len) {
                    const ch = text[i];
                    i += 1;
                    if (ch >= 0x40 and ch <= 0x7e) break;
                }
                continue;
            }
            // ESC + intermediate* + final
            i += 2;
            while (i < text.len) {
                const ch = text[i];
                i += 1;
                if (ch >= 0x40 and ch <= 0x7e) break;
            }
            continue;
        }
        if (c == 0x07 or c == 0x08) {
            i += 1;
            continue;
        }
        try out.append(gpa, c);
        i += 1;
    }
    return out.toOwnedSlice(gpa) catch return error.OutOfMemory;
}

fn isSpace(c: u8) bool {
    return c == ' ' or c == '\t' or c == '\r' or c == '\n' or c == 0x0b or c == 0x0c;
}

fn findIgnoringWhitespace(haystack: []const u8, needle: []const u8, start: usize) struct { s: isize, e: isize } {
    var want: std.ArrayList(u8) = .empty;
    defer want.deinit(allocator);
    for (needle) |ch| {
        if (!isSpace(ch)) want.append(allocator, ch) catch return .{ .s = -1, .e = -1 };
    }
    if (want.items.len == 0) return .{ .s = -1, .e = -1 };

    var chars: std.ArrayList(u8) = .empty;
    defer chars.deinit(allocator);
    var index: std.ArrayList(usize) = .empty;
    defer index.deinit(allocator);
    var i = start;
    while (i < haystack.len) : (i += 1) {
        if (!isSpace(haystack[i])) {
            chars.append(allocator, haystack[i]) catch return .{ .s = -1, .e = -1 };
            index.append(allocator, i) catch return .{ .s = -1, .e = -1 };
        }
    }
    const pos = std.mem.indexOf(u8, chars.items, want.items) orelse return .{ .s = -1, .e = -1 };
    const end_idx = pos + want.items.len - 1;
    return .{ .s = @intCast(index.items[pos]), .e = @intCast(index.items[end_idx] + 1) };
}

pub fn stripEcho(gpa: std.mem.Allocator, body_in: []const u8, command: []const u8) Error![]u8 {
    var body = try gpa.dupe(u8, body_in);
    errdefer gpa.free(body);

    var lines: std.ArrayList([]const u8) = .empty;
    defer lines.deinit(gpa);
    var it = std.mem.splitScalar(u8, command, '\n');
    while (it.next()) |ln| {
        if (std.mem.trim(u8, ln, " \t\r").len == 0) continue;
        try lines.append(gpa, ln);
    }
    if (lines.items.len == 0) return body;

    const sentinel_line = lines.items[lines.items.len - 1];
    const typed = if (lines.items.len > 1) blk: {
        var joined: std.ArrayList(u8) = .empty;
        errdefer joined.deinit(gpa);
        for (lines.items[0 .. lines.items.len - 1], 0..) |ln, idx| {
            if (idx > 0) try joined.append(gpa, '\n');
            try joined.appendSlice(gpa, ln);
        }
        break :blk try joined.toOwnedSlice(gpa);
    } else "";
    defer if (typed.len > 0) gpa.free(typed);

    const s_hit = findIgnoringWhitespace(body, sentinel_line, 0);
    if (s_hit.s >= 0) {
        const cut: usize = @intCast(s_hit.s);
        const trimmed = try gpa.dupe(u8, body[0..cut]);
        gpa.free(body);
        body = trimmed;
    }
    if (typed.len > 0) {
        const t_hit = findIgnoringWhitespace(body, typed, 0);
        if (t_hit.e >= 0) {
            const from: usize = @intCast(t_hit.e);
            const trimmed = try gpa.dupe(u8, body[from..]);
            gpa.free(body);
            body = trimmed;
        }
    }
    return body;
}

pub const SplitResult = struct {
    exit_code: i32,
    body: []u8,
};

pub fn splitSentinel(
    gpa: std.mem.Allocator,
    text: []const u8,
    sentinel: []const u8,
    command: []const u8,
    conpty: bool,
) Error!SplitResult {
    if (findSentinel(text, sentinel)) |hit| {
        const num = text[hit.code_start..hit.code_end];
        const code: i32 = if (num.len == 0) 0 else std.fmt.parseInt(i32, num, 10) catch 0;
        var body = try gpa.dupe(u8, text[0..hit.start]);
        errdefer gpa.free(body);
        if (conpty) {
            const stripped = try stripVt(gpa, body);
            gpa.free(body);
            body = stripped;
            const no_echo = try stripEcho(gpa, body, command);
            gpa.free(body);
            body = no_echo;
        }
        const trimmed = std.mem.trim(u8, body, " \t\r\n");
        const owned = try gpa.dupe(u8, trimmed);
        gpa.free(body);
        return .{ .exit_code = code, .body = owned };
    }
    if (conpty) {
        const stripped = try stripVt(gpa, text);
        const trimmed = std.mem.trim(u8, stripped, " \t\r\n");
        const owned = try gpa.dupe(u8, trimmed);
        gpa.free(stripped);
        return .{ .exit_code = -1, .body = owned };
    }
    const owned = try gpa.dupe(u8, text);
    return .{ .exit_code = -1, .body = owned };
}

pub fn sessionArgv(arena: std.mem.Allocator, io: std.Io, host_name: []const u8) Error![]const []const u8 {
    if (std.mem.eql(u8, host_name, "pwsh")) {
        const exe = (try shell_ir.resolveWhich(arena, io, "pwsh")) orelse
            (try shell_ir.resolveWhich(arena, io, "powershell")) orelse
            "pwsh";
        const argv = try arena.alloc([]const u8, 3);
        argv[0] = exe;
        argv[1] = "-NoLogo";
        argv[2] = "-NoProfile";
        return argv;
    }
    if (is_windows) {
        const exe = (try shell_ir.resolveWhich(arena, io, "cmd")) orelse "cmd.exe";
        const argv = try arena.alloc([]const u8, 3);
        argv[0] = exe;
        argv[1] = "/Q";
        argv[2] = "/K";
        return argv;
    }
    const sh = (try shell_ir.resolveWhich(arena, io, "bash")) orelse
        (try shell_ir.resolveWhich(arena, io, "sh")) orelse
        "/bin/sh";
    const argv = try arena.alloc([]const u8, 1);
    argv[0] = sh;
    return argv;
}

fn randomToken(buf: []u8) void {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    var raw: [16]u8 = undefined;
    io.random(raw[0..]);
    const hex = "0123456789abcdef";
    const n = @min(buf.len, 16);
    var i: usize = 0;
    while (i < n) : (i += 1) {
        buf[i] = hex[raw[i] & 0xf];
    }
}

fn looksLikeInteractivePrompt(buf: []const u8) bool {
    // Current unterminated line only (after last newline).
    const tail = if (std.mem.lastIndexOfScalar(u8, buf, '\n')) |idx|
        buf[idx + 1 ..]
    else
        buf;
    var lower_buf: [512]u8 = undefined;
    const n = @min(tail.len, lower_buf.len);
    for (tail[0..n], 0..) |ch, i| {
        lower_buf[i] = std.ascii.toLower(ch);
    }
    const lower = lower_buf[0..n];
    for (prompt_markers) |marker| {
        if (std.mem.indexOf(u8, lower, marker) != null) return true;
    }
    return false;
}

fn scrubSessionEnv(arena: std.mem.Allocator, env_json: []const u8) Error![]u8 {
    var obj: std.json.ObjectMap = .empty;

    if (env_json.len > 0) {
        const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, env_json, .{}) catch return error.InvalidArgument;
        if (parsed != .object) return error.InvalidArgument;
        var it = parsed.object.iterator();
        while (it.next()) |entry| {
            if (entry.value_ptr.* != .string) return error.InvalidArgument;
            if (std.ascii.eqlIgnoreCase(entry.key_ptr.*, "GIT_ASKPASS")) continue;
            try obj.put(arena, entry.key_ptr.*, entry.value_ptr.*);
        }
    }

    const defaults = [_]struct { []const u8, []const u8 }{
        .{ "PYTHONIOENCODING", "utf-8" },
        .{ "PYTHONUTF8", "1" },
        .{ "GIT_TERMINAL_PROMPT", "0" },
        .{ "GCM_INTERACTIVE", "never" },
        .{ "GH_PROMPT_DISABLED", "1" },
    };
    for (defaults) |pair| {
        if (obj.get(pair[0]) == null) {
            try obj.put(arena, pair[0], .{ .string = pair[1] });
        }
    }
    if (is_windows and obj.get("CHCP") == null) {
        try obj.put(arena, "CHCP", .{ .string = "65001" });
    }

    return std.json.Stringify.valueAlloc(arena, std.json.Value{ .object = obj }, .{}) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// Session state
// ---------------------------------------------------------------------------

const Transport = enum { conpty, pipe };

const Session = struct {
    host_name: []u8,
    cwd: []u8,
    use_conpty: bool,
    used_conpty: bool,
    transport: Transport,
    handle: u64 = 0, // ConPTY or Piped opaque
    started: bool = false,
    mutex: std.atomic.Mutex = .unlocked,

    fn lock(self: *Session) void {
        while (!self.mutex.tryLock()) std.atomic.spinLoopHint();
    }
    fn unlock(self: *Session) void {
        self.mutex.unlock();
    }
};

fn sessionFrom(handle: u64) Error!*Session {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(handle)));
}

fn millisNow() u64 {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const ts = std.Io.Timestamp.now(io, .awake);
    return @intCast(ts.toMilliseconds());
}

fn sleepBrief(ms: u32) void {
    if (is_windows) {
        const SleepFn = struct {
            extern "kernel32" fn Sleep(dwell: u32) callconv(.winapi) void;
        };
        SleepFn.Sleep(ms);
        return;
    }
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    std.Io.sleep(io, .fromMilliseconds(@intCast(ms)), .awake) catch {};
}

pub const SessionResult = struct {
    exit_code: i32,
    stdout: []const u8,
    stderr: []const u8 = "",
    cwd: []const u8 = "",
    timed_out: bool = false,
    interactive: bool = false,
    host: []const u8,
    used_conpty: bool = false,
};

fn transportWrite(session: *Session, data: []const u8) Error!void {
    if (!is_windows) return error.Unsupported;
    var payload = data;
    var owned: ?[]u8 = null;
    defer if (owned) |o| allocator.free(o);
    if (session.used_conpty) {
        // Pseudoconsole submits on CR.
        var converted: std.ArrayList(u8) = .empty;
        errdefer converted.deinit(allocator);
        for (data) |ch| {
            if (ch == '\r') continue;
            if (ch == '\n') {
                try converted.append(allocator, '\r');
            } else {
                try converted.append(allocator, ch);
            }
        }
        owned = try converted.toOwnedSlice(allocator);
        payload = owned.?;
    }
    switch (session.transport) {
        .conpty => _ = try windows_conpty.write(session.handle, payload),
        .pipe => _ = try windows_host.pipedWrite(session.handle, payload),
    }
}

fn transportReadAvailable(session: *Session, buf: []u8) Error!usize {
    if (!is_windows) return error.Unsupported;
    return switch (session.transport) {
        .conpty => windows_conpty.readAvailable(session.handle, buf),
        .pipe => windows_host.pipedReadAvailable(session.handle, buf),
    };
}

fn transportPoll(session: *Session) Error!struct { exited: bool, exit_code: u32 } {
    if (!is_windows) return error.Unsupported;
    switch (session.transport) {
        .conpty => {
            const o = try windows_conpty.poll(session.handle);
            return .{ .exited = o.exited, .exit_code = o.exit_code };
        },
        .pipe => {
            const o = try windows_host.pipedPoll(session.handle);
            return .{ .exited = o.exited, .exit_code = o.exit_code };
        },
    }
}

fn transportKill(session: *Session) void {
    if (!is_windows) return;
    switch (session.transport) {
        .conpty => windows_conpty.kill(session.handle) catch {},
        .pipe => windows_host.pipedKill(session.handle) catch {},
    }
}

fn transportClose(session: *Session) void {
    if (!is_windows or session.handle == 0) return;
    switch (session.transport) {
        .conpty => windows_conpty.close(session.handle) catch {},
        .pipe => windows_host.pipedClose(session.handle) catch {},
    }
    session.handle = 0;
}

fn abandon(session: *Session) void {
    transportKill(session);
    transportClose(session);
    session.started = false;
}

fn openSession(
    host_name_in: []const u8,
    cwd_in: []const u8,
    env_json_in: []const u8,
    use_conpty: bool,
) Error!u64 {
    if (!is_windows) return error.Unsupported;
    const host_name = if (host_name_in.len == 0) defaultHost() else host_name_in;
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const a = arena.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();

    const argv = try sessionArgv(a, io, host_name);
    if (!std.fs.path.isAbsolute(argv[0])) return error.InvalidArgument;
    const argv_json = std.json.Stringify.valueAlloc(a, argv, .{}) catch return error.OutOfMemory;
    const env_json = try scrubSessionEnv(a, env_json_in);

    const session = allocator.create(Session) catch return error.OutOfMemory;
    errdefer allocator.destroy(session);
    const host_owned = allocator.dupe(u8, host_name) catch return error.OutOfMemory;
    errdefer allocator.free(host_owned);
    const cwd_owned = allocator.dupe(u8, cwd_in) catch return error.OutOfMemory;
    errdefer allocator.free(cwd_owned);

    session.* = .{
        .host_name = host_owned,
        .cwd = cwd_owned,
        .use_conpty = use_conpty,
        .used_conpty = false,
        .transport = .pipe,
        .started = false,
    };

    const want_conpty = use_conpty and windows_conpty.available();
    if (want_conpty) {
        const spawned = windows_conpty.spawn(argv_json, cwd_in, env_json, 120, 40) catch |err| {
            allocator.free(host_owned);
            allocator.free(cwd_owned);
            allocator.destroy(session);
            return err;
        };
        session.transport = .conpty;
        session.used_conpty = true;
        session.handle = spawned.handle;
    } else {
        const spawned = windows_host.spawnPiped(argv_json, cwd_in, env_json) catch |err| {
            allocator.free(host_owned);
            allocator.free(cwd_owned);
            allocator.destroy(session);
            return err;
        };
        session.transport = .pipe;
        session.used_conpty = false;
        session.handle = spawned.handle;
    }

    session.started = true;
    const boot = bootCommands(host_name);
    if (boot.len > 0) {
        var boot_line: std.ArrayList(u8) = .empty;
        defer boot_line.deinit(allocator);
        boot_line.appendSlice(allocator, boot) catch {};
        boot_line.append(allocator, '\n') catch {};
        transportWrite(session, boot_line.items) catch {};
        sleepBrief(50);
    }
    return @intFromPtr(session);
}

fn readUntil(session: *Session, sentinel: []const u8, timeout_ms: u32) Error!struct {
    raw: []u8,
    timed_out: bool,
    interactive: bool,
    shell_exit: ?u32,
} {
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(allocator);
    const deadline = millisNow() + @as(u64, timeout_ms);
    var chunk: [4096]u8 = undefined;
    var interactive = false;

    while (true) {
        const now = millisNow();
        if (now >= deadline) {
            return .{
                .raw = try buf.toOwnedSlice(allocator),
                .timed_out = true,
                .interactive = interactive,
                .shell_exit = null,
            };
        }
        const n = try transportReadAvailable(session, chunk[0..]);
        if (n > 0) {
            try buf.appendSlice(allocator, chunk[0..n]);
            if (sentinelDone(buf.items, sentinel)) {
                return .{
                    .raw = try buf.toOwnedSlice(allocator),
                    .timed_out = false,
                    .interactive = interactive,
                    .shell_exit = null,
                };
            }
            if (looksLikeInteractivePrompt(buf.items)) {
                interactive = true;
                transportKill(session);
                abandon(session);
                return .{
                    .raw = try buf.toOwnedSlice(allocator),
                    .timed_out = true,
                    .interactive = true,
                    .shell_exit = null,
                };
            }
        } else {
            const outcome = try transportPoll(session);
            if (outcome.exited) {
                return .{
                    .raw = try buf.toOwnedSlice(allocator),
                    .timed_out = false,
                    .interactive = interactive,
                    .shell_exit = outcome.exit_code,
                };
            }
            sleepBrief(40);
        }
    }
}

fn runCommand(session: *Session, command: []const u8, timeout_ms: u32) Error!SessionResult {
    const trimmed = std.mem.trim(u8, command, " \t\r\n");
    if (trimmed.len == 0) {
        return .{
            .exit_code = -1,
            .stdout = try allocator.dupe(u8, ""),
            .stderr = try allocator.dupe(u8, "empty command"),
            .host = session.host_name,
            .used_conpty = session.used_conpty,
        };
    }
    if (!session.started) return error.InvalidArgument;

    var tok: [10]u8 = undefined;
    randomToken(&tok);
    var sentinel_buf: [sentinel_prefix.len + 10]u8 = undefined;
    @memcpy(sentinel_buf[0..sentinel_prefix.len], sentinel_prefix);
    @memcpy(sentinel_buf[sentinel_prefix.len..], &tok);
    const sentinel = sentinel_buf[0..];

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const wrapped = try wrapWithSentinel(arena.allocator(), session.host_name, trimmed, sentinel);

    session.lock();
    defer session.unlock();

    try transportWrite(session, wrapped);
    const read = try readUntil(session, sentinel, if (timeout_ms < 1000) 1000 else timeout_ms);
    defer allocator.free(read.raw);

    if (read.shell_exit) |code| {
        abandon(session);
        const split = try splitSentinel(allocator, read.raw, sentinel, wrapped, session.used_conpty);
        defer allocator.free(split.body);
        const note = try std.fmt.allocPrint(
            allocator,
            "{s}\nthe shell exited (code {d}) while running the command",
            .{ split.body, code },
        );
        return .{
            .exit_code = @intCast(code),
            .stdout = note,
            .timed_out = false,
            .interactive = read.interactive,
            .host = session.host_name,
            .used_conpty = session.used_conpty,
        };
    }
    if (read.timed_out) {
        if (!read.interactive) {
            transportKill(session);
            abandon(session);
        }
        const split = try splitSentinel(allocator, read.raw, sentinel, wrapped, session.used_conpty);
        return .{
            .exit_code = -1,
            .stdout = split.body,
            .timed_out = true,
            .interactive = read.interactive,
            .host = session.host_name,
            .used_conpty = session.used_conpty,
        };
    }

    const split = try splitSentinel(allocator, read.raw, sentinel, wrapped, session.used_conpty);
    const cwd = currentCwdLocked(session) catch "";
    return .{
        .exit_code = split.exit_code,
        .stdout = split.body,
        .cwd = cwd,
        .timed_out = false,
        .interactive = false,
        .host = session.host_name,
        .used_conpty = session.used_conpty,
    };
}

fn currentCwdLocked(session: *Session) Error![]u8 {
    if (!session.started) return allocator.dupe(u8, "") catch return error.OutOfMemory;
    var tok: [8]u8 = undefined;
    randomToken(&tok);
    var sentinel_buf: [sentinel_prefix.len + 4 + 8]u8 = undefined;
    const prefix = sentinel_prefix ++ "cwd_";
    @memcpy(sentinel_buf[0..prefix.len], prefix);
    @memcpy(sentinel_buf[prefix.len..][0..8], &tok);
    const sentinel = sentinel_buf[0 .. prefix.len + 8];

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const cmd = cwdCommand(session.host_name);
    const wrapped = try wrapWithSentinel(arena.allocator(), session.host_name, cmd, sentinel);
    try transportWrite(session, wrapped);
    const read = try readUntil(session, sentinel, 8000);
    defer allocator.free(read.raw);
    if (read.timed_out or read.shell_exit != null) {
        if (read.timed_out) {
            transportKill(session);
        }
        abandon(session);
        return allocator.dupe(u8, "") catch return error.OutOfMemory;
    }
    const split = try splitSentinel(allocator, read.raw, sentinel, wrapped, session.used_conpty);
    defer allocator.free(split.body);
    var last: []const u8 = "";
    var it = std.mem.splitScalar(u8, split.body, '\n');
    while (it.next()) |ln| {
        const t = std.mem.trim(u8, ln, " \t\r");
        if (t.len > 0) last = t;
    }
    return allocator.dupe(u8, last) catch return error.OutOfMemory;
}

fn closeSession(session: *Session) void {
    if (session.started) {
        transportWrite(session, "exit\n") catch {};
        sleepBrief(20);
        transportKill(session);
        transportClose(session);
    }
    allocator.free(session.host_name);
    allocator.free(session.cwd);
    allocator.destroy(session);
}

fn resultToJson(result: SessionResult) Error![]u8 {
    return std.json.Stringify.valueAlloc(allocator, .{
        .exit_code = result.exit_code,
        .stdout = result.stdout,
        .stderr = result.stderr,
        .cwd = result.cwd,
        .timed_out = result.timed_out,
        .interactive = result.interactive,
        .host = result.host,
        .used_conpty = result.used_conpty,
    }, .{}) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// C ABI
// ---------------------------------------------------------------------------

fn parseOpenJson(arena: std.mem.Allocator, json_in: []const u8) Error!struct {
    host: []const u8,
    cwd: []const u8,
    env_json: []const u8,
    use_conpty: bool,
} {
    if (json_in.len == 0) {
        return .{ .host = defaultHost(), .cwd = "", .env_json = "", .use_conpty = false };
    }
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json_in, .{}) catch return error.InvalidArgument;
    if (parsed != .object) return error.InvalidArgument;
    const obj = parsed.object;
    const host_name = if (obj.get("host")) |v| (if (v == .string) v.string else return error.InvalidArgument) else defaultHost();
    const cwd = if (obj.get("cwd")) |v| (if (v == .string) v.string else return error.InvalidArgument) else "";
    var use_conpty = false;
    if (obj.get("use_conpty")) |v| {
        use_conpty = switch (v) {
            .bool => v.bool,
            .integer => v.integer != 0,
            else => return error.InvalidArgument,
        };
    }
    var env_json: []const u8 = "";
    if (obj.get("env")) |v| {
        env_json = std.json.Stringify.valueAlloc(arena, v, .{}) catch return error.OutOfMemory;
    }
    return .{ .host = host_name, .cwd = cwd, .env_json = env_json, .use_conpty = use_conpty };
}

export fn remedy_core_host_session_open(
    json_in: ?[*]const u8,
    json_len: usize,
    out_handle: ?*u64,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const slot = out_handle orelse return invalid_status;
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const opts = parseOpenJson(arena.allocator(), slice(json_in, json_len)) catch |err| {
        slot.* = 0;
        return statusOf(err);
    };
    const handle = openSession(opts.host, opts.cwd, opts.env_json, opts.use_conpty) catch |err| {
        slot.* = 0;
        return statusOf(err);
    };
    slot.* = handle;
    return ok_status;
}

export fn remedy_core_host_session_open_authorized(
    json_in: ?[*]const u8,
    json_len: usize,
    token: ?[*]const u8,
    token_len: usize,
    subject: ?[*]const u8,
    subject_len: usize,
    scope: ?[*]const u8,
    scope_len: usize,
    owner_confirmed: u8,
    now_ms: u64,
    out_handle: ?*u64,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const slot = out_handle orelse return invalid_status;
    slot.* = 0;
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const opts = parseOpenJson(a, slice(json_in, json_len)) catch |err| return statusOf(err);

    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const host_name = if (opts.host.len == 0) defaultHost() else opts.host;
    const argv = sessionArgv(a, io, host_name) catch |err| return statusOf(err);
    if (!std.fs.path.isAbsolute(argv[0])) return invalid_status;

    const subj = if (subject_len == 0) default_subject else slice(subject, subject_len);
    const scp = if (scope_len == 0) default_scope else slice(scope, scope_len);
    spawn_auth.authorize(
        argv,
        opts.cwd,
        slice(token, token_len),
        subj,
        scp,
        owner_confirmed != 0,
        now_ms,
    ) catch |err| return authStatus(err);

    const handle = openSession(opts.host, opts.cwd, opts.env_json, opts.use_conpty) catch |err| {
        return statusOf(err);
    };
    slot.* = handle;
    return ok_status;
}

export fn remedy_core_host_session_run(
    handle: u64,
    command: ?[*]const u8,
    command_len: usize,
    timeout_ms: u32,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const session = sessionFrom(handle) catch return invalid_status;
    const result = runCommand(session, slice(command, command_len), timeout_ms) catch |err| {
        return deliverBytes(@as(Error![]u8, err), out_json, out_len);
    };
    defer allocator.free(result.stdout);
    if (result.stderr.len > 0) allocator.free(result.stderr);
    if (result.cwd.len > 0) allocator.free(result.cwd);
    return deliverBytes(resultToJson(result), out_json, out_len);
}

export fn remedy_core_host_session_cwd(
    handle: u64,
    out_utf8: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const session = sessionFrom(handle) catch return invalid_status;
    session.lock();
    defer session.unlock();
    const cwd = currentCwdLocked(session) catch |err| {
        return deliverBytes(@as(Error![]u8, err), out_utf8, out_len);
    };
    return deliverBytes(cwd, out_utf8, out_len);
}

export fn remedy_core_host_session_close(handle: u64) callconv(.c) i32 {
    const session = sessionFrom(handle) catch return invalid_status;
    closeSession(session);
    return ok_status;
}

/// Pure protocol helper: {"host","command","sentinel"} → {"wrapped"}.
export fn remedy_core_host_session_wrap(
    json_in: ?[*]const u8,
    json_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, a, slice(json_in, json_len), .{}) catch {
        return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    };
    if (parsed != .object) return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    const host_name = if (parsed.object.get("host")) |v| (if (v == .string) v.string else "") else defaultHost();
    const command = if (parsed.object.get("command")) |v| (if (v == .string) v.string else "") else "";
    const sentinel = if (parsed.object.get("sentinel")) |v| (if (v == .string) v.string else "") else "";
    if (sentinel.len == 0) return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    const wrapped = wrapWithSentinel(a, host_name, command, sentinel) catch |err| {
        return deliverBytes(@as(Error![]u8, err), out_json, out_len);
    };
    const out = std.json.Stringify.valueAlloc(allocator, .{ .wrapped = wrapped }, .{}) catch {
        return deliverBytes(@as(Error![]u8, error.OutOfMemory), out_json, out_len);
    };
    return deliverBytes(out, out_json, out_len);
}

/// Pure protocol helper: {"text","sentinel","command"?,"conpty"?} → {"exit_code","body"}.
export fn remedy_core_host_session_split(
    json_in: ?[*]const u8,
    json_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, a, slice(json_in, json_len), .{}) catch {
        return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    };
    if (parsed != .object) return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    const text = if (parsed.object.get("text")) |v| (if (v == .string) v.string else "") else "";
    const sentinel = if (parsed.object.get("sentinel")) |v| (if (v == .string) v.string else "") else "";
    const command = if (parsed.object.get("command")) |v| (if (v == .string) v.string else "") else "";
    var conpty = false;
    if (parsed.object.get("conpty")) |v| {
        conpty = switch (v) {
            .bool => v.bool,
            .integer => v.integer != 0,
            else => false,
        };
    }
    if (sentinel.len == 0) return deliverBytes(@as(Error![]u8, error.InvalidArgument), out_json, out_len);
    const split = splitSentinel(allocator, text, sentinel, command, conpty) catch |err| {
        return deliverBytes(@as(Error![]u8, err), out_json, out_len);
    };
    defer allocator.free(split.body);
    const out = std.json.Stringify.valueAlloc(allocator, .{
        .exit_code = split.exit_code,
        .body = split.body,
    }, .{}) catch return deliverBytes(@as(Error![]u8, error.OutOfMemory), out_json, out_len);
    return deliverBytes(out, out_json, out_len);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test "host_session wrap matches Python dialects" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const a = arena.allocator();
    const cmd = try wrapWithSentinel(a, "cmd", "echo hello", "REMEDY_HOST_DONE_abc");
    try std.testing.expectEqualStrings("echo hello\necho REMEDY_HOST_DONE_abc:%ERRORLEVEL%\n", cmd);
    const pwsh = try wrapWithSentinel(a, "pwsh", "echo hello", "REMEDY_HOST_DONE_abc");
    try std.testing.expectEqualStrings("echo hello\nWrite-Output \"REMEDY_HOST_DONE_abc:$LASTEXITCODE\"\n", pwsh);
    const posix = try wrapWithSentinel(a, "posix", "echo hello", "REMEDY_HOST_DONE_abc");
    // Python: non-pwsh on Windows still uses %ERRORLEVEL%.
    if (is_windows) {
        try std.testing.expectEqualStrings("echo hello\necho REMEDY_HOST_DONE_abc:%ERRORLEVEL%\n", posix);
    } else {
        try std.testing.expectEqualStrings("echo hello\necho REMEDY_HOST_DONE_abc:$?\n", posix);
    }
}

test "host_session sentinel expanded form only" {
    try std.testing.expect(sentinelDone("hi\r\nREMEDY_HOST_DONE_abc:0\r\n", "REMEDY_HOST_DONE_abc"));
    try std.testing.expect(sentinelDone("out\r\nREMEDY_HOST_DONE_abc:\r\n", "REMEDY_HOST_DONE_abc"));
    try std.testing.expect(sentinelDone("hello\r\nREMEDY_HOST_DONE_abc:0\x1b[5;1H", "REMEDY_HOST_DONE_abc"));
    try std.testing.expect(!sentinelDone("echo REMEDY_HOST_DONE_abc:%ERRORLEVEL%\r\n", "REMEDY_HOST_DONE_abc"));
    try std.testing.expect(!sentinelDone("Write-Output \"REMEDY_HOST_DONE_abc:$LASTEXITCODE\"\r\n", "REMEDY_HOST_DONE_abc"));
    try std.testing.expect(!sentinelDone("echo REMEDY_HOST_DONE_abc:$?\n", "REMEDY_HOST_DONE_abc"));
}

test "host_session split_sentinel cases" {
    const cases = [_]struct { text: []const u8, code: i32, body: []const u8 }{
        .{ .text = "hi\r\nREMEDY_HOST_DONE_abc:0\r\n", .code = 0, .body = "hi" },
        .{ .text = "REMEDY_HOST_DONE_abc:12\r\n", .code = 12, .body = "" },
        .{ .text = "x\nREMEDY_HOST_DONE_abc:-1\n", .code = -1, .body = "x" },
        .{ .text = "out\r\nREMEDY_HOST_DONE_abc:\r\n", .code = 0, .body = "out" },
        .{ .text = "hello\r\nREMEDY_HOST_DONE_abc:0\x1b[5;1H", .code = 0, .body = "hello" },
    };
    for (cases) |c| {
        const got = try splitSentinel(std.testing.allocator, c.text, "REMEDY_HOST_DONE_abc", "", false);
        defer std.testing.allocator.free(got.body);
        try std.testing.expectEqual(c.code, got.exit_code);
        try std.testing.expectEqualStrings(c.body, got.body);
    }
}

test "host_session split strips VT and echo for conpty" {
    const sentinel = "REMEDY_HOST_DONE_abc";
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const wrapped = try wrapWithSentinel(arena.allocator(), "cmd", "echo hello", sentinel);
    // Build with real ESC bytes.
    var raw: std.ArrayList(u8) = .empty;
    defer raw.deinit(std.testing.allocator);
    const prelude = [_]u8{ 0x1b, '[', '?', '9', '0', '0', '1', 'h' };
    try raw.appendSlice(std.testing.allocator, &prelude);
    try raw.appendSlice(std.testing.allocator, "chcp 65001 >NUL & @echo off");
    try raw.appendSlice(std.testing.allocator, &[_]u8{ 0x1b, '[', '2', ';', '1', 'H' });
    try raw.appendSlice(std.testing.allocator, "echo hello");
    try raw.appendSlice(std.testing.allocator, &[_]u8{ 0x1b, '[', '3', ';', '1', 'H' });
    try raw.appendSlice(std.testing.allocator, "hello\r\n");
    try raw.appendSlice(std.testing.allocator, "echo REMEDY_HOST_DONE_abc:%ERRORLEVEL%");
    try raw.appendSlice(std.testing.allocator, &[_]u8{ 0x1b, '[', '5', ';', '1', 'H' });
    try raw.appendSlice(std.testing.allocator, &[_]u8{ 0x1b, '[', '?', '2', '5', 'h' });
    try raw.appendSlice(std.testing.allocator, "REMEDY_HOST_DONE_abc:0\r\n");

    const got = try splitSentinel(std.testing.allocator, raw.items, sentinel, wrapped, true);
    defer std.testing.allocator.free(got.body);
    try std.testing.expectEqual(@as(i32, 0), got.exit_code);
    try std.testing.expectEqualStrings("hello", got.body);
}

test "host_session wrap/split C ABI" {
    const wrap_in =
        \\{"host":"cmd","command":"echo hi","sentinel":"REMEDY_HOST_DONE_x"}
    ;
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    try std.testing.expectEqual(ok_status, remedy_core_host_session_wrap(wrap_in.ptr, wrap_in.len, &out_ptr, &out_len));
    defer host.allocator.free(out_ptr.?[0..out_len]);
    try std.testing.expect(std.mem.indexOf(u8, out_ptr.?[0..out_len], "ERRORLEVEL") != null);

    const split_in =
        \\{"text":"hi\r\nREMEDY_HOST_DONE_x:0\r\n","sentinel":"REMEDY_HOST_DONE_x"}
    ;
    out_ptr = null;
    out_len = 0;
    try std.testing.expectEqual(ok_status, remedy_core_host_session_split(split_in.ptr, split_in.len, &out_ptr, &out_len));
    defer host.allocator.free(out_ptr.?[0..out_len]);
    try std.testing.expect(std.mem.indexOf(u8, out_ptr.?[0..out_len], "\"exit_code\":0") != null);
}

test "host_session live echo round-trip" {
    if (!is_windows) return;
    const payload =
        \\{"host":"cmd","use_conpty":false}
    ;
    var handle: u64 = 0;
    const ost = remedy_core_host_session_open(payload.ptr, payload.len, &handle);
    try std.testing.expectEqual(ok_status, ost);
    defer _ = remedy_core_host_session_close(handle);

    const cmd = "echo host-session-ok";
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const rst = remedy_core_host_session_run(handle, cmd.ptr, cmd.len, 20000, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, rst);
    defer host.allocator.free(out_ptr.?[0..out_len]);
    try std.testing.expect(std.mem.indexOf(u8, out_ptr.?[0..out_len], "host-session-ok") != null);
}
