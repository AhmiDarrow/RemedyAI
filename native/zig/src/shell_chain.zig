//! Shell-chain expand + execute (ABI 5 additive).
//!
//! Ports Python `expand_shell_chain` / sandbox hop runner:
//! `cmd /c A && B` or `sh -c A && B` → cd|mkdir|run hops without a shell.
//! Execute uses write_jail + process.runCaptureSoft (Linux+Windows).

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const shell_ir = @import("shell_ir.zig");
const write_jail = @import("write_jail.zig");
const process = @import("process.zig");
const policy = @import("policy.zig");
const capability = @import("capability.zig");

const Status = root.Status;
const Error = host.Error;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);

const is_windows = builtin.os.tag == .windows;

pub const HopKind = enum { run, cd, mkdir };

pub const ChainHop = struct {
    kind: HopKind,
    argv: []const []const u8 = &.{},
    paths: []const []const u8 = &.{},
};

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn trimSpace(s: []const u8) []const u8 {
    return std.mem.trim(u8, s, " \t\r\n");
}

fn unquoteCmdToken(tok: []const u8) []const u8 {
    const t = trimSpace(tok);
    if (t.len >= 2 and t[0] == '"' and t[t.len - 1] == '"') {
        // Caller arena-owns tokens from coerceArgv; strip quotes in place via slice.
        return t[1 .. t.len - 1];
    }
    return t;
}

fn argvForHiddenHop(arena: std.mem.Allocator, part: []const u8) error{OutOfMemory}![]const []const u8 {
    const hop = try shell_ir.coerceArgv(arena, part);
    if (!is_windows) return hop;
    var out = try arena.alloc([]const u8, hop.len);
    for (hop, 0..) |tok, i| out[i] = unquoteCmdToken(tok);
    return out;
}

/// Quote-aware `&&` split. Null if fewer than two hops or quotes never close.
pub fn splitAndSegments(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}!?[]const []const u8 {
    const raw = trimSpace(text);
    if (raw.len == 0 or std.mem.indexOf(u8, raw, "||") != null) return null;

    var parts: std.ArrayList([]const u8) = .empty;
    errdefer parts.deinit(arena);
    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(arena);

    var quote: u8 = 0;
    var i: usize = 0;
    while (i < raw.len) {
        const ch = raw[i];
        if (quote != 0) {
            try buf.append(arena, ch);
            if (ch == quote) quote = 0;
            i += 1;
            continue;
        }
        if (ch == '"' or ch == '\'') {
            quote = ch;
            try buf.append(arena, ch);
            i += 1;
            continue;
        }
        if (std.mem.startsWith(u8, raw[i..], "&&")) {
            const part = trimSpace(buf.items);
            if (part.len != 0) try parts.append(arena, try arena.dupe(u8, part));
            buf.clearRetainingCapacity();
            i += 2;
            continue;
        }
        try buf.append(arena, ch);
        i += 1;
    }
    if (quote != 0) return null;
    const last = trimSpace(buf.items);
    if (last.len != 0) try parts.append(arena, try arena.dupe(u8, last));
    if (parts.items.len < 2) return null;
    return try parts.toOwnedSlice(arena);
}

fn shellChainTextAlloc(arena: std.mem.Allocator, argv: []const []const u8) error{OutOfMemory}!?[]const u8 {
    if (argv.len < 3) return null;
    const head = shell_ir.exeStem(argv[0]);
    const flag = argv[1];
    const is_cmd = std.ascii.eqlIgnoreCase(head, "cmd") and std.ascii.eqlIgnoreCase(flag, "/c");
    const is_sh = (std.ascii.eqlIgnoreCase(head, "sh") or std.ascii.eqlIgnoreCase(head, "bash")) and
        std.ascii.eqlIgnoreCase(flag, "-c");
    if (!is_cmd and !is_sh) return null;
    if (argv.len == 3) return argv[2];
    var total: usize = argv.len - 3; // spaces between
    for (argv[2..]) |a| total += a.len;
    var out = try arena.alloc(u8, total);
    var n: usize = 0;
    for (argv[2..], 0..) |a, idx| {
        if (idx != 0) {
            out[n] = ' ';
            n += 1;
        }
        @memcpy(out[n..][0..a.len], a);
        n += a.len;
    }
    return out[0..n];
}

fn parseCdHop(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}!?[]const u8 {
    const toks = try argvForHiddenHop(arena, text);
    if (toks.len == 0 or !std.ascii.eqlIgnoreCase(shell_ir.exeStem(toks[0]), "cd")) return null;
    var rest = toks[1..];
    if (rest.len > 0 and std.ascii.eqlIgnoreCase(rest[0], "/d")) rest = rest[1..];
    if (rest.len != 1) return null;
    return rest[0];
}

fn parseOneMkdir(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}!?[]const []const u8 {
    const t = trimSpace(text);
    if (try parseIfNotExistMkdir(arena, t)) |paths| return paths;

    const toks = try argvForHiddenHop(arena, t);
    if (toks.len == 0) return null;
    const stem = shell_ir.exeStem(toks[0]);
    if (!std.ascii.eqlIgnoreCase(stem, "mkdir") and !std.ascii.eqlIgnoreCase(stem, "md")) return null;
    var paths: std.ArrayList([]const u8) = .empty;
    errdefer paths.deinit(arena);
    for (toks[1..]) |p| {
        if (std.mem.eql(u8, p, "-p") or std.mem.eql(u8, p, "--parents")) continue;
        if (std.mem.startsWith(u8, p, "-")) continue;
        try paths.append(arena, p);
    }
    if (paths.items.len == 0) return null;
    return try paths.toOwnedSlice(arena);
}

/// `(if not exist "out\." mkdir "out")` → `["out"]`.
fn parseIfNotExistMkdir(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}!?[]const []const u8 {
    var t = trimSpace(text);
    if (t.len < 2 or t[0] != '(' or t[t.len - 1] != ')') return null;
    t = trimSpace(t[1 .. t.len - 1]);
    // if not exist <src> mkdir <dest>
    if (!startsWithIgnoreCase(t, "if")) return null;
    var rest = trimSpace(t[2..]);
    if (!startsWithIgnoreCase(rest, "not")) return null;
    rest = trimSpace(rest[3..]);
    if (!startsWithIgnoreCase(rest, "exist")) return null;
    rest = trimSpace(rest[5..]);
    // skip exist target token
    const after_exist = skipToken(rest) orelse return null;
    rest = trimSpace(after_exist);
    if (!startsWithIgnoreCase(rest, "mkdir")) return null;
    rest = trimSpace(rest[5..]);
    const dest = takeToken(rest) orelse return null;
    if (trimSpace(dest.rest).len != 0) return null;
    const path = unquoteCmdToken(dest.tok);
    if (path.len == 0) return null;
    var out = try arena.alloc([]const u8, 1);
    out[0] = path;
    return out;
}

fn startsWithIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (hay.len < needle.len) return false;
    return std.ascii.eqlIgnoreCase(hay[0..needle.len], needle);
}

const TokenTake = struct { tok: []const u8, rest: []const u8 };

fn takeToken(s: []const u8) ?TokenTake {
    const t = trimSpace(s);
    if (t.len == 0) return null;
    if (t[0] == '"') {
        var i: usize = 1;
        while (i < t.len and t[i] != '"') : (i += 1) {}
        if (i >= t.len) return null;
        return .{ .tok = t[0 .. i + 1], .rest = t[i + 1 ..] };
    }
    var i: usize = 0;
    while (i < t.len and t[i] != ' ' and t[i] != '\t') : (i += 1) {}
    return .{ .tok = t[0..i], .rest = t[i..] };
}

fn skipToken(s: []const u8) ?[]const u8 {
    const taken = takeToken(s) orelse return null;
    return taken.rest;
}

fn parseMkdirHop(arena: std.mem.Allocator, text: []const u8) error{OutOfMemory}!?[]const []const u8 {
    const t = trimSpace(text);
    if (std.mem.indexOf(u8, t, " & ") != null and std.mem.indexOf(u8, t, "&&") == null) {
        var paths: std.ArrayList([]const u8) = .empty;
        errdefer paths.deinit(arena);
        var it = std.mem.splitSequence(u8, t, " & ");
        while (it.next()) |part| {
            const one = try parseOneMkdir(arena, trimSpace(part)) orelse return null;
            try paths.appendSlice(arena, one);
        }
        if (paths.items.len == 0) return null;
        return try paths.toOwnedSlice(arena);
    }
    return parseOneMkdir(arena, t);
}

pub fn classifyChainHop(
    arena: std.mem.Allocator,
    io: std.Io,
    text: []const u8,
    project_path: []const u8,
) error{OutOfMemory}!?ChainHop {
    _ = project_path;
    if (try parseCdHop(arena, text)) |cd| {
        var paths = try arena.alloc([]const u8, 1);
        paths[0] = cd;
        return .{ .kind = .cd, .paths = paths };
    }
    if (try parseMkdirHop(arena, text)) |mk| {
        return .{ .kind = .mkdir, .paths = mk };
    }
    if (!try shell_ir.looksLikePlainArgv(arena, text)) return null;
    var hop = try argvForHiddenHop(arena, text);
    if (hop.len == 0) return null;
    if (!shell_ir.pathExists(io, hop[0])) {
        if (try shell_ir.resolveWhich(arena, io, hop[0])) |resolved| {
            var copy = try arena.alloc([]const u8, hop.len);
            @memcpy(copy, hop);
            copy[0] = resolved;
            hop = copy;
        }
    }
    const deflated = try shell_ir.deflateUvRun(arena, io, hop);
    return .{ .kind = .run, .argv = deflated };
}

pub fn expandShellChain(
    arena: std.mem.Allocator,
    io: std.Io,
    argv: []const []const u8,
    project_path: []const u8,
) error{OutOfMemory}!?[]const ChainHop {
    const text = (try shellChainTextAlloc(arena, argv)) orelse return null;
    const parts = (try splitAndSegments(arena, text)) orelse return null;
    var hops: std.ArrayList(ChainHop) = .empty;
    errdefer hops.deinit(arena);
    for (parts) |part| {
        const hop = (try classifyChainHop(arena, io, part, project_path)) orelse return null;
        try hops.append(arena, hop);
    }
    if (hops.items.len < 2) return null;
    return try hops.toOwnedSlice(arena);
}

fn appendJsonStringArray(buf: *std.ArrayList(u8), gpa: std.mem.Allocator, items: []const []const u8) Error!void {
    try buf.append(gpa, '[');
    for (items, 0..) |item, i| {
        if (i != 0) try buf.append(gpa, ',');
        const q = try jsonQuote(gpa, item);
        defer gpa.free(q);
        try buf.appendSlice(gpa, q);
    }
    try buf.append(gpa, ']');
}

fn hopToJson(gpa: std.mem.Allocator, hop: ChainHop) Error![]u8 {
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(gpa);
    try buf.appendSlice(gpa, "{\"kind\":");
    const kind_q = try jsonQuote(gpa, @tagName(hop.kind));
    defer gpa.free(kind_q);
    try buf.appendSlice(gpa, kind_q);
    switch (hop.kind) {
        .run => {
            try buf.appendSlice(gpa, ",\"argv\":");
            try appendJsonStringArray(&buf, gpa, hop.argv);
        },
        .cd, .mkdir => {
            try buf.appendSlice(gpa, ",\"paths\":");
            try appendJsonStringArray(&buf, gpa, hop.paths);
        },
    }
    try buf.append(gpa, '}');
    return buf.toOwnedSlice(gpa);
}

fn hopsToJson(gpa: std.mem.Allocator, hops: ?[]const ChainHop) Error![]u8 {
    if (hops == null) {
        return gpa.dupe(u8, "{\"hops\":null}") catch return error.OutOfMemory;
    }
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(gpa);
    try buf.appendSlice(gpa, "{\"hops\":[");
    for (hops.?, 0..) |hop, i| {
        if (i != 0) try buf.append(gpa, ',');
        const one = try hopToJson(gpa, hop);
        defer gpa.free(one);
        try buf.appendSlice(gpa, one);
    }
    try buf.appendSlice(gpa, "]}");
    return buf.toOwnedSlice(gpa);
}

fn parseStringArray(arena: std.mem.Allocator, value: std.json.Value) error{ OutOfMemory, InvalidArgument }![]const []const u8 {
    const arr = switch (value) {
        .array => |a| a,
        else => return error.InvalidArgument,
    };
    var out = try arena.alloc([]const u8, arr.items.len);
    for (arr.items, 0..) |item, i| {
        out[i] = switch (item) {
            .string => |s| s,
            else => return error.InvalidArgument,
        };
    }
    return out;
}

fn parseHop(arena: std.mem.Allocator, value: std.json.Value) error{ OutOfMemory, InvalidArgument }!ChainHop {
    const obj = switch (value) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };
    const kind_s = switch (obj.get("kind") orelse return error.InvalidArgument) {
        .string => |s| s,
        else => return error.InvalidArgument,
    };
    if (std.mem.eql(u8, kind_s, "run")) {
        const argv_v = obj.get("argv") orelse return error.InvalidArgument;
        return .{ .kind = .run, .argv = try parseStringArray(arena, argv_v) };
    }
    if (std.mem.eql(u8, kind_s, "cd") or std.mem.eql(u8, kind_s, "mkdir")) {
        const paths_v = obj.get("paths") orelse return error.InvalidArgument;
        const kind: HopKind = if (std.mem.eql(u8, kind_s, "cd")) .cd else .mkdir;
        return .{ .kind = kind, .paths = try parseStringArray(arena, paths_v) };
    }
    return error.InvalidArgument;
}

fn parseHops(arena: std.mem.Allocator, value: std.json.Value) error{ OutOfMemory, InvalidArgument }![]const ChainHop {
    const arr = switch (value) {
        .array => |a| a,
        else => return error.InvalidArgument,
    };
    var out = try arena.alloc(ChainHop, arr.items.len);
    for (arr.items, 0..) |item, i| out[i] = try parseHop(arena, item);
    return out;
}

fn jsonString(obj: std.json.ObjectMap, key: []const u8) []const u8 {
    const v = obj.get(key) orelse return "";
    return switch (v) {
        .string => |s| s,
        else => "",
    };
}

fn jsonUint(obj: std.json.ObjectMap, key: []const u8, default: u64) u64 {
    const v = obj.get(key) orelse return default;
    return switch (v) {
        .integer => |i| if (i < 0) default else @intCast(i),
        .float => |f| if (f < 0) default else @intFromFloat(f),
        else => default,
    };
}

fn resolvePath(arena: std.mem.Allocator, cwd: []const u8, raw: []const u8) error{ OutOfMemory, InvalidArgument }![]const u8 {
    const trimmed = trimSpace(raw);
    if (trimmed.len == 0) return error.InvalidArgument;
    // Expand ~ lightly
    var path = trimmed;
    if (trimmed.len > 0 and trimmed[0] == '~') {
        const home = shell_ir.getEnvAlloc(arena, if (is_windows) "USERPROFILE" else "HOME") orelse "";
        if (home.len != 0) {
            if (trimmed.len == 1 or trimmed[1] == '/' or trimmed[1] == '\\') {
                path = try std.fs.path.join(arena, &.{ home, if (trimmed.len > 2) trimmed[2..] else "" });
            }
        }
    }
    return write_jail.normalizePathAlloc(arena, path, cwd) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return error.InvalidArgument,
    };
}

fn isDirectory(io: std.Io, path: []const u8) bool {
    var dir = if (std.fs.path.isAbsolute(path))
        std.Io.Dir.openDirAbsolute(io, path, .{}) catch return false
    else
        std.Io.Dir.cwd().openDir(io, path, .{}) catch return false;
    dir.close(io);
    return true;
}

fn createDirPath(io: std.Io, path: []const u8) !void {
    std.Io.Dir.cwd().createDirPath(io, path) catch return error.OperationFailed;
}

const ExecuteResult = struct {
    exit_code: i64 = 0,
    stdout: []const u8 = "",
    stderr: []const u8 = "",
    duration_ms: f64 = 0,
    cwd: []const u8 = "",
    timed_out: bool = false,
    aborted: bool = false,
    not_a_chain: bool = false,
    hops_run: u32 = 0,
};

fn jsonQuote(gpa: std.mem.Allocator, s: []const u8) Error![]u8 {
    return std.json.Stringify.valueAlloc(gpa, s, .{}) catch return error.OutOfMemory;
}

fn buildExecuteJson(gpa: std.mem.Allocator, result: ExecuteResult) Error![]u8 {
    const stdout_q = try jsonQuote(gpa, result.stdout);
    defer gpa.free(stdout_q);
    const stderr_q = try jsonQuote(gpa, result.stderr);
    defer gpa.free(stderr_q);
    const cwd_q = try jsonQuote(gpa, result.cwd);
    defer gpa.free(cwd_q);
    return std.fmt.allocPrint(gpa,
        \\{{"exit_code":{d},"stdout":{s},"stderr":{s},"duration_ms":{d:.3},"cwd":{s},"timed_out":{s},"aborted":{s},"not_a_chain":{s},"hops_run":{d}}}
    , .{
        result.exit_code,
        stdout_q,
        stderr_q,
        result.duration_ms,
        cwd_q,
        boolText(result.timed_out),
        boolText(result.aborted),
        boolText(result.not_a_chain),
        result.hops_run,
    }) catch return error.OutOfMemory;
}

fn boolText(v: bool) []const u8 {
    return if (v) "true" else "false";
}

fn appendJoined(arena: std.mem.Allocator, parts: *std.ArrayList(u8), chunk: []const u8) error{OutOfMemory}!void {
    if (chunk.len == 0) return;
    if (parts.items.len != 0) try parts.append(arena, '\n');
    try parts.appendSlice(arena, chunk);
}

fn timeoutFromMs(ms: u64) std.Io.Timeout {
    if (ms == 0) return .none;
    return .{ .duration = .{
        .raw = std.Io.Duration.fromMilliseconds(@intCast(ms)),
        .clock = .awake,
    } };
}

fn aborted(flag: ?*const u8) bool {
    const f = flag orelse return false;
    return @as(*const volatile u8, @ptrCast(f)).* != 0;
}

fn executeHops(
    gpa: std.mem.Allocator,
    io: std.Io,
    hops: []const ChainHop,
    initial_cwd: []const u8,
    env_pairs: ?[]const host.EnvPair,
    timeout_ms: u64,
    abort_flag: ?*const u8,
) Error!ExecuteResult {
    var arena_state = std.heap.ArenaAllocator.init(gpa);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const start_ms = std.Io.Timestamp.now(io, .awake).toMilliseconds();
    var cwd_buf = try arena.dupe(u8, initial_cwd);
    var stdout_parts: std.ArrayList(u8) = .empty;
    var stderr_parts: std.ArrayList(u8) = .empty;
    var last_exit: i64 = 0;
    var hops_run: u32 = 0;
    var timed_out = false;
    var was_aborted = false;
    var remaining_ms: u64 = if (timeout_ms == 0) std.math.maxInt(u64) else timeout_ms;

    var env_map: std.process.Environ.Map = undefined;
    var have_env = false;
    defer if (have_env) env_map.deinit();
    var env_map_ptr: ?*const std.process.Environ.Map = null;
    if (env_pairs) |pairs| {
        env_map = std.process.Environ.Map.init(gpa);
        have_env = true;
        for (pairs) |p| env_map.put(p.key, p.value) catch return error.OutOfMemory;
        env_map_ptr = &env_map;
    }

    for (hops) |hop| {
        if (aborted(abort_flag)) {
            was_aborted = true;
            last_exit = -1;
            try appendJoined(arena, &stderr_parts, "Aborted before start (session stop)");
            break;
        }
        if (remaining_ms <= 50 and timeout_ms != 0) {
            timed_out = true;
            last_exit = -1;
            const msg = try std.fmt.allocPrint(arena, "Command timed out after {d}s", .{timeout_ms / 1000});
            try appendJoined(arena, &stderr_parts, msg);
            break;
        }

        const hop_start = std.Io.Timestamp.now(io, .awake).toMilliseconds();
        hops_run += 1;

        switch (hop.kind) {
            .cd => {
                const raw = if (hop.paths.len > 0) hop.paths[0] else "";
                const target = resolvePath(arena, cwd_buf, raw) catch {
                    last_exit = -1;
                    try appendJoined(arena, &stderr_parts, "Blocked by write jail (cd): invalid path");
                    break;
                };
                write_jail.checkPath(gpa, target, cwd_buf) catch {
                    last_exit = -1;
                    const msg = try std.fmt.allocPrint(arena, "Blocked by write jail (cd {s}): access denied", .{raw});
                    try appendJoined(arena, &stderr_parts, msg);
                    break;
                };
                if (!isDirectory(io, target)) {
                    last_exit = 1;
                    const msg = try std.fmt.allocPrint(arena, "cd: {s} is not a directory", .{target});
                    try appendJoined(arena, &stderr_parts, msg);
                    break;
                }
                cwd_buf = try arena.dupe(u8, target);
                last_exit = 0;
            },
            .mkdir => {
                var failed = false;
                for (hop.paths) |p| {
                    const dest = resolvePath(arena, cwd_buf, p) catch {
                        last_exit = -1;
                        try appendJoined(arena, &stderr_parts, "Blocked by write jail (mkdir): invalid path");
                        failed = true;
                        break;
                    };
                    write_jail.checkPath(gpa, dest, cwd_buf) catch {
                        last_exit = -1;
                        const msg = try std.fmt.allocPrint(arena, "Blocked by write jail (mkdir {s}): access denied", .{p});
                        try appendJoined(arena, &stderr_parts, msg);
                        failed = true;
                        break;
                    };
                    createDirPath(io, dest) catch {
                        last_exit = 1;
                        const msg = try std.fmt.allocPrint(arena, "mkdir failed: {s}", .{dest});
                        try appendJoined(arena, &stderr_parts, msg);
                        failed = true;
                        break;
                    };
                }
                if (failed) break;
                last_exit = 0;
            },
            .run => {
                var argv = hop.argv;
                if (argv.len == 0) {
                    last_exit = -1;
                    try appendJoined(arena, &stderr_parts, "internal: empty run hop");
                    break;
                }
                // Resolve relative argv[0] for process.validateArguments.
                if (!std.fs.path.isAbsolute(argv[0])) {
                    if (try shell_ir.resolveWhich(arena, io, argv[0])) |resolved| {
                        var copy = try arena.alloc([]const u8, argv.len);
                        @memcpy(copy, argv);
                        copy[0] = resolved;
                        argv = copy;
                    } else {
                        last_exit = -1;
                        const msg = try std.fmt.allocPrint(arena, "Command not found: {s}", .{argv[0]});
                        try appendJoined(arena, &stderr_parts, msg);
                        break;
                    }
                }
                if (policy.isDangerousProcess(argv)) {
                    last_exit = -1;
                    const msg = try std.fmt.allocPrint(
                        arena,
                        "Blocked by security policy: Dangerous command: {s}",
                        .{shell_ir.exeStem(argv[0])},
                    );
                    try appendJoined(arena, &stderr_parts, msg);
                    break;
                }
                write_jail.checkSpawn(gpa, argv, cwd_buf) catch {
                    last_exit = -1;
                    try appendJoined(arena, &stderr_parts, "Blocked by write jail (spawn): access denied");
                    break;
                };

                const soft = process.runCaptureSoft(gpa, io, capability.Set.one(.process_spawn), .{
                    .argv = argv,
                    .timeout = timeoutFromMs(if (timeout_ms == 0) 0 else remaining_ms),
                    .cwd = cwd_buf,
                    .environ_map = env_map_ptr,
                    .abort_flag = abort_flag,
                }) catch |err| {
                    last_exit = -1;
                    const msg = try std.fmt.allocPrint(arena, "OS error: {s}", .{@errorName(err)});
                    try appendJoined(arena, &stderr_parts, msg);
                    break;
                };
                defer gpa.free(soft.stdout);
                defer gpa.free(soft.stderr);

                try appendJoined(arena, &stdout_parts, soft.stdout);
                try appendJoined(arena, &stderr_parts, soft.stderr);
                if (soft.aborted) {
                    was_aborted = true;
                    last_exit = -1;
                    try appendJoined(arena, &stderr_parts, "Aborted (session stop) — shell killed");
                    break;
                }
                if (soft.timed_out) {
                    timed_out = true;
                    last_exit = -1;
                    const msg = try std.fmt.allocPrint(arena, "Command timed out after {d}s", .{timeout_ms / 1000});
                    try appendJoined(arena, &stderr_parts, msg);
                    break;
                }
                last_exit = @intCast(soft.exit_code);
                if (last_exit != 0) break;
            },
        }

        const now_ms = std.Io.Timestamp.now(io, .awake).toMilliseconds();
        const elapsed_ms: u64 = @intCast(@max(@as(i64, 0), now_ms - hop_start));
        if (timeout_ms != 0) {
            if (elapsed_ms >= remaining_ms) remaining_ms = 0 else remaining_ms -= elapsed_ms;
        }
    }

    const end_ms = std.Io.Timestamp.now(io, .awake).toMilliseconds();
    const duration_ms: f64 = @floatFromInt(@max(@as(i64, 0), end_ms - start_ms));
    return .{
        .exit_code = last_exit,
        .stdout = try gpa.dupe(u8, stdout_parts.items),
        .stderr = try gpa.dupe(u8, stderr_parts.items),
        .duration_ms = duration_ms,
        .cwd = try gpa.dupe(u8, cwd_buf),
        .timed_out = timed_out,
        .aborted = was_aborted,
        .not_a_chain = false,
        .hops_run = hops_run,
    };
}

fn expandToOwnedJson(input: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(host.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, input, .{}) catch return error.InvalidArgument;
    const obj = switch (parsed) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };
    const argv_v = obj.get("argv") orelse return error.InvalidArgument;
    const argv = try parseStringArray(arena, argv_v);
    const project_path = jsonString(obj, "project_path");

    // Expand only needs PATH lookups; failing allocator is fine for those.
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();

    const hops = try expandShellChain(arena, io, argv, project_path);
    return hopsToJson(host.allocator, hops);
}

fn threadedIoForExecute() std.Io.Threaded {
    // init_single_threaded uses Allocator.failing — process spawn OOMs.
    // Match its environ defaults so inherit works when env_map is null.
    const parent_env: std.process.Environ = if (is_windows)
        .{ .block = .global }
    else
        .{ .block = .empty };
    return std.Io.Threaded.init(host.allocator, .{ .environ = parent_env });
}

fn executeToOwnedJson(input: []const u8, abort_flag: ?*const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(host.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, input, .{}) catch return error.InvalidArgument;
    const obj = switch (parsed) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };

    const cwd = jsonString(obj, "cwd");
    const project_path = blk: {
        const p = jsonString(obj, "project_path");
        break :blk if (p.len != 0) p else cwd;
    };
    const timeout_ms = jsonUint(obj, "timeout_ms", 30_000);
    const env_pairs = if (obj.get("env")) |ev| blk: {
        const raw = std.json.Stringify.valueAlloc(arena, ev, .{}) catch return error.OutOfMemory;
        break :blk host.parseEnv(arena, raw) catch return error.InvalidArgument;
    } else null;

    var threaded = threadedIoForExecute();
    defer threaded.deinit();
    const io = threaded.io();

    const hops: []const ChainHop = if (obj.get("hops")) |hv| blk: {
        if (hv == .null) {
            const result = ExecuteResult{ .not_a_chain = true };
            return buildExecuteJson(host.allocator, result);
        }
        break :blk try parseHops(arena, hv);
    } else blk: {
        const argv_v = obj.get("argv") orelse return error.InvalidArgument;
        const argv = try parseStringArray(arena, argv_v);
        const expanded = (try expandShellChain(arena, io, argv, project_path)) orelse {
            const result = ExecuteResult{ .not_a_chain = true, .cwd = cwd };
            return buildExecuteJson(host.allocator, result);
        };
        break :blk expanded;
    };

    if (hops.len < 2) {
        const result = ExecuteResult{ .not_a_chain = true, .cwd = cwd };
        return buildExecuteJson(host.allocator, result);
    }

    const result = try executeHops(host.allocator, io, hops, cwd, env_pairs, timeout_ms, abort_flag);
    defer {
        host.allocator.free(result.stdout);
        host.allocator.free(result.stderr);
        host.allocator.free(result.cwd);
    }
    // buildExecuteJson copies strings into the output buffer.
    return buildExecuteJson(host.allocator, result);
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

/// Expand `cmd /c A && B` / `sh -c A && B` into hops JSON.
/// OK with `"hops": null` means not a chain (not an error).
export fn remedy_core_shell_chain_expand(
    json_in: ?[*]const u8,
    json_in_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(expandToOwnedJson(input), out_json, out_len);
}

/// Execute a shell chain from argv or hops. Jail/policy denies return OK with
/// exit_code -1 and stderr wording matching the Python sandbox family.
export fn remedy_core_shell_chain_execute(
    json_in: ?[*]const u8,
    json_in_len: usize,
    abort_flag: ?*const u8,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(executeToOwnedJson(input, abort_flag), out_json, out_len);
}

// ---- tests ----------------------------------------------------------------

fn testingIo() std.Io {
    return std.testing.io;
}

test "splitAndSegments quote-aware &&" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const parts = (try splitAndSegments(arena, "git add . && git status")).?;
    try std.testing.expectEqual(@as(usize, 2), parts.len);
    try std.testing.expectEqualStrings("git add .", parts[0]);
    try std.testing.expectEqualStrings("git status", parts[1]);

    const quoted = (try splitAndSegments(arena, "git commit -m \"fix: a && b\" && git status")).?;
    try std.testing.expectEqual(@as(usize, 2), quoted.len);
    try std.testing.expectEqualStrings("git commit -m \"fix: a && b\"", quoted[0]);

    try std.testing.expect(try splitAndSegments(arena, "git status") == null);
    try std.testing.expect(try splitAndSegments(arena, "a || b") == null);
}

test "expandShellChain cd and mkdir hops" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const io = testingIo();

    const cd_run = (try expandShellChain(arena, io, &.{ "cmd.exe", "/c", "cd src && git status" }, "")).?;
    try std.testing.expectEqual(@as(usize, 2), cd_run.len);
    try std.testing.expect(cd_run[0].kind == .cd);
    try std.testing.expectEqualStrings("src", cd_run[0].paths[0]);
    try std.testing.expect(cd_run[1].kind == .run);

    const mk = (try expandShellChain(
        arena,
        io,
        &.{ "cmd.exe", "/c", "(if not exist \"out\\.\" mkdir \"out\") && git add ." },
        "",
    )).?;
    try std.testing.expect(mk[0].kind == .mkdir);
    try std.testing.expectEqualStrings("out", mk[0].paths[0]);
    try std.testing.expect(mk[1].kind == .run);

    const posix_mk = (try expandShellChain(arena, io, &.{ "sh", "-c", "mkdir -p build && git status" }, "")).?;
    try std.testing.expect(posix_mk[0].kind == .mkdir);
    try std.testing.expectEqualStrings("build", posix_mk[0].paths[0]);

    try std.testing.expect(try expandShellChain(arena, io, &.{ "cmd", "/c", "git status" }, "") == null);
}

test "expandShellChain splits git without needing cmd builtins" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const io = testingIo();

    const hops = (try expandShellChain(
        arena,
        io,
        &.{ "cmd.exe", "/c", "git add . && git commit -m \"wip\"" },
        "",
    )).?;
    try std.testing.expectEqual(@as(usize, 2), hops.len);
    try std.testing.expect(hops[0].kind == .run);
    try std.testing.expect(hops[1].kind == .run);
    try std.testing.expectEqualStrings("add", hops[0].argv[1]);
    try std.testing.expectEqualStrings(".", hops[0].argv[2]);
    try std.testing.expectEqualStrings("commit", hops[1].argv[1]);
    try std.testing.expectEqualStrings("-m", hops[1].argv[2]);
    try std.testing.expectEqualStrings("wip", hops[1].argv[3]);
    try std.testing.expect(std.ascii.eqlIgnoreCase(shell_ir.exeStem(hops[0].argv[0]), "git"));
}

test "execute mkdir then run creates directory" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const io = testingIo();
    const gpa = std.testing.allocator;

    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    var path_buf: [std.fs.max_path_bytes]u8 = undefined;
    const abs_len = try tmp.dir.realPath(io, &path_buf);
    const abs = path_buf[0..abs_len];

    try write_jail.setRoots(gpa, &.{abs});
    defer write_jail.clearRoots(gpa);

    const dest = try std.fs.path.join(arena, &.{ abs, "made" });
    const py = (try shell_ir.resolveWhich(arena, io, "python")) orelse
        (try shell_ir.resolveWhich(arena, io, "python3")) orelse return error.SkipZigTest;

    const hops = [_]ChainHop{
        .{ .kind = .mkdir, .paths = &.{dest} },
        .{ .kind = .run, .argv = &.{ py, "-c", "print('mkdir-ok')" } },
    };
    const result = try executeHops(gpa, io, &hops, abs, null, 20_000, null);
    defer {
        gpa.free(result.stdout);
        gpa.free(result.stderr);
        gpa.free(result.cwd);
    }
    try std.testing.expectEqual(@as(i64, 0), result.exit_code);
    try std.testing.expect(std.mem.indexOf(u8, result.stdout, "mkdir-ok") != null);
    try std.testing.expect(isDirectory(io, dest));
}

test "execute stops on failure" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const io = testingIo();
    const gpa = std.testing.allocator;

    const py = (try shell_ir.resolveWhich(arena, io, "python")) orelse
        (try shell_ir.resolveWhich(arena, io, "python3")) orelse return error.SkipZigTest;

    const hops = [_]ChainHop{
        .{ .kind = .run, .argv = &.{ py, "-c", "raise SystemExit(3)" } },
        .{ .kind = .run, .argv = &.{ py, "-c", "print('chain-nope')" } },
    };
    const result = try executeHops(gpa, io, &hops, "", null, 20_000, null);
    defer {
        gpa.free(result.stdout);
        gpa.free(result.stderr);
        gpa.free(result.cwd);
    }
    try std.testing.expectEqual(@as(i64, 3), result.exit_code);
    try std.testing.expect(std.mem.indexOf(u8, result.stdout, "chain-nope") == null);
    try std.testing.expectEqual(@as(u32, 1), result.hops_run);
}

test "execute cd outside jail denies" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const io = testingIo();
    const gpa = std.testing.allocator;

    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    var path_buf: [std.fs.max_path_bytes]u8 = undefined;
    const abs_len = try tmp.dir.realPath(io, &path_buf);
    const abs = path_buf[0..abs_len];
    try write_jail.setRoots(gpa, &.{abs});
    defer write_jail.clearRoots(gpa);

    const outside = try std.fs.path.join(arena, &.{ abs, "..", ".." });
    const hops = [_]ChainHop{
        .{ .kind = .cd, .paths = &.{outside} },
        .{ .kind = .run, .argv = &.{ "git", "status" } },
    };
    const result = try executeHops(gpa, io, &hops, abs, null, 5_000, null);
    defer {
        gpa.free(result.stdout);
        gpa.free(result.stderr);
        gpa.free(result.cwd);
    }
    try std.testing.expect(result.exit_code != 0);
    try std.testing.expect(std.mem.indexOf(u8, result.stderr, "jail") != null or
        std.mem.indexOf(u8, result.stderr, "Blocked") != null);
}

test "ABI expand returns hops null for non-chain" {
    const input =
        \\{"argv":["git","status"]}
    ;
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const status = remedy_core_shell_chain_expand(input.ptr, input.len, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, status);
    defer host.allocator.free(out_ptr.?[0..out_len]);
    try std.testing.expect(std.mem.indexOf(u8, out_ptr.?[0..out_len], "\"hops\":null") != null);
}
