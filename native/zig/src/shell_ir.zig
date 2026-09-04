//! Host Command IR — Zig mirror of `remedy.execution.host.ir` + prepare.
//! ABI 4: `remedy_core_host_op_prepare` (run|script|mkdir|which|env|chain).
//! Python `prepare_host_op` routes those kinds here. Translate is in
//! `shell_translate.zig`. prepare_host_command / ConPTY remain Python.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");

const Status = root.Status;
const Error = host.Error;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);

const utf8_bom = [_]u8{ 0xEF, 0xBB, 0xBF };
const max_script_chars: usize = 1_000_000;

const uv_run_modules = [_][]const u8{
    "pytest", "ruff", "mypy", "pip", "httpx", "uvicorn", "http.server",
};

pub const OpKind = enum {
    run,
    mkdir,
    which,
    env,
    script,
    raw,
    chain,

    pub fn fromName(raw: []const u8) OpKind {
        inline for (@typeInfo(OpKind).@"enum".fields) |field| {
            if (std.mem.eql(u8, field.name, raw)) return @field(OpKind, field.name);
        }
        return .raw;
    }

    pub fn asText(self: OpKind) []const u8 {
        return @tagName(self);
    }
};

pub const HostOp = struct {
    kind: OpKind = .raw,
    argv: []const []const u8 = &.{},
    paths: []const []const u8 = &.{},
    name: []const u8 = "",
    lang: []const u8 = "",
    body: []const u8 = "",
    host: []const u8 = "",
    text: []const u8 = "",
    cwd: []const u8 = "",
    env: []const EnvPair = &.{},
    ops: []const HostOp = &.{},

    pub const EnvPair = struct { key: []const u8, value: []const u8 };
};

pub const PrepareOptions = struct {
    scratch_dir: []const u8 = "",
    project_path: []const u8 = "",
};

pub const PreparedCommand = struct {
    argv: []const []const u8 = &.{},
    display: []const u8 = "",
    kind: []const u8 = "raw",
    ir: HostOp = .{},
    script_path: []const u8 = "",
    notes: []const []const u8 = &.{},
    translated: []const u8 = "",
    host_name: []const u8 = "cmd",
};

/// Parse one HostOp from a JSON object (Python `HostOp.from_dict` semantics).
/// Non-object / missing kind → `.raw` with empty text. Result lives in `arena`.
pub fn fromJsonValue(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}!HostOp {
    const object = switch (value) {
        .object => |object| object,
        else => return .{},
    };

    var op: HostOp = .{};
    if (object.get("kind")) |kind_v| {
        op.kind = switch (kind_v) {
            .string => |s| OpKind.fromName(s),
            else => .raw,
        };
    }

    if (object.get("argv")) |argv_v| {
        op.argv = try stringList(arena, argv_v);
    }
    if (object.get("paths")) |paths_v| {
        op.paths = try stringList(arena, paths_v);
    }
    if (object.get("name")) |v| op.name = try asString(arena, v);
    if (object.get("lang")) |v| op.lang = try asString(arena, v);
    if (object.get("body")) |v| op.body = try asString(arena, v);
    if (object.get("host")) |v| op.host = try asString(arena, v);
    if (object.get("text")) |v| op.text = try asString(arena, v);
    if (object.get("cwd")) |v| op.cwd = try asString(arena, v);

    if (object.get("env")) |env_v| {
        op.env = try envPairs(arena, env_v);
    }
    if (object.get("ops")) |ops_v| {
        op.ops = try childOps(arena, ops_v);
    }
    return op;
}

pub fn parse(arena: std.mem.Allocator, json: []const u8) error{ OutOfMemory, InvalidJson }!HostOp {
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json, .{}) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return error.InvalidJson,
    };
    return fromJsonValue(arena, parsed);
}

/// Serialize like Python `HostOp.to_dict` (omit empty fields). Caller frees.
pub fn toJson(gpa: std.mem.Allocator, op: HostOp) error{OutOfMemory}![]u8 {
    var list: std.ArrayList(u8) = .empty;
    errdefer list.deinit(gpa);
    try writeDict(&list, gpa, op);
    return try list.toOwnedSlice(gpa);
}

fn writeDict(list: *std.ArrayList(u8), gpa: std.mem.Allocator, op: HostOp) error{OutOfMemory}!void {
    try list.append(gpa, '{');
    var first = true;
    try writeKey(list, gpa, &first, "kind");
    try writeString(list, gpa, op.kind.asText());

    if (op.argv.len != 0) {
        try writeKey(list, gpa, &first, "argv");
        try writeStringArray(list, gpa, op.argv);
    }
    if (op.paths.len != 0) {
        try writeKey(list, gpa, &first, "paths");
        try writeStringArray(list, gpa, op.paths);
    }
    if (op.name.len != 0) {
        try writeKey(list, gpa, &first, "name");
        try writeString(list, gpa, op.name);
    }
    if (op.lang.len != 0) {
        try writeKey(list, gpa, &first, "lang");
        try writeString(list, gpa, op.lang);
    }
    if (op.body.len != 0) {
        try writeKey(list, gpa, &first, "body");
        try writeString(list, gpa, op.body);
    }
    if (op.host.len != 0) {
        try writeKey(list, gpa, &first, "host");
        try writeString(list, gpa, op.host);
    }
    if (op.text.len != 0) {
        try writeKey(list, gpa, &first, "text");
        try writeString(list, gpa, op.text);
    }
    if (op.cwd.len != 0) {
        try writeKey(list, gpa, &first, "cwd");
        try writeString(list, gpa, op.cwd);
    }
    if (op.env.len != 0) {
        try writeKey(list, gpa, &first, "env");
        try list.append(gpa, '{');
        var env_first = true;
        for (op.env) |pair| {
            if (!env_first) try list.append(gpa, ',');
            env_first = false;
            try writeString(list, gpa, pair.key);
            try list.append(gpa, ':');
            try writeString(list, gpa, pair.value);
        }
        try list.append(gpa, '}');
    }
    if (op.ops.len != 0) {
        try writeKey(list, gpa, &first, "ops");
        try list.append(gpa, '[');
        for (op.ops, 0..) |child, i| {
            if (i != 0) try list.append(gpa, ',');
            try writeDict(list, gpa, child);
        }
        try list.append(gpa, ']');
    }
    try list.append(gpa, '}');
}

fn writePrepared(list: *std.ArrayList(u8), gpa: std.mem.Allocator, prep: PreparedCommand) error{OutOfMemory}!void {
    try list.append(gpa, '{');
    var first = true;
    try writeKey(list, gpa, &first, "argv");
    try writeStringArray(list, gpa, prep.argv);
    try writeKey(list, gpa, &first, "display");
    try writeString(list, gpa, prep.display);
    try writeKey(list, gpa, &first, "kind");
    try writeString(list, gpa, prep.kind);
    try writeKey(list, gpa, &first, "ir");
    try writeDict(list, gpa, prep.ir);
    if (prep.script_path.len != 0) {
        try writeKey(list, gpa, &first, "script_path");
        try writeString(list, gpa, prep.script_path);
    }
    if (prep.notes.len != 0) {
        try writeKey(list, gpa, &first, "notes");
        try writeStringArray(list, gpa, prep.notes);
    }
    if (prep.translated.len != 0) {
        try writeKey(list, gpa, &first, "translated");
        try writeString(list, gpa, prep.translated);
    }
    try writeKey(list, gpa, &first, "host");
    try writeString(list, gpa, prep.host_name);
    try list.append(gpa, '}');
}

pub fn preparedToJson(gpa: std.mem.Allocator, prep: PreparedCommand) error{OutOfMemory}![]u8 {
    var list: std.ArrayList(u8) = .empty;
    errdefer list.deinit(gpa);
    try writePrepared(&list, gpa, prep);
    return try list.toOwnedSlice(gpa);
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

fn asString(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const u8 {
    return switch (value) {
        .string => |s| try arena.dupe(u8, s),
        .integer => |n| try std.fmt.allocPrint(arena, "{d}", .{n}),
        .float => |n| try std.fmt.allocPrint(arena, "{d}", .{n}),
        .bool => |b| if (b) "true" else "false",
        .null => "",
        else => "",
    };
}

fn stringList(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const []const u8 {
    const array = switch (value) {
        .array => |a| a,
        else => return &.{},
    };
    var out = try arena.alloc([]const u8, array.items.len);
    var n: usize = 0;
    for (array.items) |item| {
        const s = try asString(arena, item);
        if (s.len == 0) continue;
        out[n] = s;
        n += 1;
    }
    return out[0..n];
}

fn envPairs(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const HostOp.EnvPair {
    const object = switch (value) {
        .object => |o| o,
        else => return &.{},
    };
    var out = try arena.alloc(HostOp.EnvPair, object.count());
    var i: usize = 0;
    var it = object.iterator();
    while (it.next()) |entry| : (i += 1) {
        out[i] = .{
            .key = try arena.dupe(u8, entry.key_ptr.*),
            .value = try asString(arena, entry.value_ptr.*),
        };
    }
    return out;
}

fn childOps(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const HostOp {
    const array = switch (value) {
        .array => |a| a,
        else => return &.{},
    };
    var out = try arena.alloc(HostOp, array.items.len);
    var n: usize = 0;
    for (array.items) |item| {
        if (item != .object) continue;
        out[n] = try fromJsonValue(arena, item);
        n += 1;
    }
    return out[0..n];
}

fn defaultHostName() []const u8 {
    return if (builtin.os.tag == .windows) "cmd" else "posix";
}

fn exeStem(name: []const u8) []const u8 {
    var head = name;
    if (std.mem.lastIndexOfScalar(u8, head, '/')) |i| head = head[i + 1 ..];
    if (std.mem.lastIndexOfScalar(u8, head, '\\')) |i| head = head[i + 1 ..];
    if (head.len >= 4 and std.ascii.eqlIgnoreCase(head[head.len - 4 ..], ".exe")) {
        return head[0 .. head.len - 4];
    }
    return head;
}

fn testingIo() std.Io {
    return std.testing.io;
}

fn pathExists(io: std.Io, path: []const u8) bool {
    if (path.len == 0) return false;
    if (std.fs.path.isAbsolute(path)) {
        std.Io.Dir.accessAbsolute(io, path, .{}) catch return false;
        return true;
    }
    std.Io.Dir.cwd().access(io, path, .{}) catch return false;
    return true;
}

fn getEnvAlloc(gpa: std.mem.Allocator, key: []const u8) ?[]u8 {
    // Windows Environ.Block is GlobalBlock (`.global`). POSIX Block is a
    // PosixBlock slice — use libc getenv instead of inventing a fake `.global`.
    if (builtin.os.tag == .windows) {
        const environ: std.process.Environ = .{ .block = .global };
        return std.process.Environ.getAlloc(environ, gpa, key) catch null;
    }
    const key_z = gpa.dupeZ(u8, key) catch return null;
    defer gpa.free(key_z);
    const value = std.c.getenv(key_z) orelse return null;
    return gpa.dupe(u8, std.mem.span(value)) catch null;
}

/// Resolve an executable on PATH (Windows also tries `.exe` and PATHEXT).
pub fn resolveWhich(arena: std.mem.Allocator, io: std.Io, name: []const u8) error{OutOfMemory}!?[]const u8 {
    const n = std.mem.trim(u8, name, " \t");
    if (n.len == 0) return null;
    if (std.fs.path.isAbsolute(n) and pathExists(io, n)) {
        return try arena.dupe(u8, n);
    }

    const path_env = getEnvAlloc(arena, "PATH") orelse "";
    const delimiter: u8 = if (builtin.os.tag == .windows) ';' else ':';

    var ext_buf: [16][]const u8 = undefined;
    var ext_count: usize = 1;
    ext_buf[0] = "";
    if (builtin.os.tag == .windows) {
        if (!std.ascii.eqlIgnoreCase(std.fs.path.extension(n), ".exe")) {
            ext_buf[ext_count] = ".exe";
            ext_count += 1;
        }
        if (getEnvAlloc(arena, "PATHEXT")) |pathext| {
            var it = std.mem.tokenizeScalar(u8, pathext, ';');
            while (it.next()) |ext| {
                if (ext.len == 0) continue;
                if (ext_count >= ext_buf.len) break;
                if (std.ascii.eqlIgnoreCase(ext, ".exe")) continue;
                ext_buf[ext_count] = ext;
                ext_count += 1;
            }
        }
    }
    const extensions = ext_buf[0..ext_count];

    var dirs = std.mem.tokenizeScalar(u8, path_env, delimiter);
    while (dirs.next()) |dir| {
        if (dir.len == 0) continue;
        for (extensions) |ext| {
            const base = if (ext.len == 0) n else blk: {
                if (std.ascii.eqlIgnoreCase(std.fs.path.extension(n), ext)) break :blk n;
                break :blk try std.fmt.allocPrint(arena, "{s}{s}", .{ n, ext });
            };
            const candidate = try std.fs.path.join(arena, &.{ dir, base });
            if (pathExists(io, candidate)) return candidate;
        }
    }

    // Fallbacks matching Python launch_script defaults.
    const stem = exeStem(n);
    if (std.ascii.eqlIgnoreCase(stem, "pwsh")) {
        if (try resolveWhich(arena, io, "powershell")) |hit| return hit;
    }
    if (builtin.os.tag == .windows and std.ascii.eqlIgnoreCase(stem, "cmd")) {
        const system_root = getEnvAlloc(arena, "SystemRoot") orelse "C:\\Windows";
        const cmd = try std.fs.path.join(arena, &.{ system_root, "System32", "cmd.exe" });
        if (pathExists(io, cmd)) return cmd;
    }
    return null;
}

fn isUvRunModule(tool: []const u8) bool {
    for (uv_run_modules) |m| {
        if (std.ascii.eqlIgnoreCase(tool, m)) return true;
    }
    return false;
}

/// Turn `uv run pytest` into `python -m pytest` (Python `deflate_uv_run`).
pub fn deflateUvRun(arena: std.mem.Allocator, io: std.Io, argv: []const []const u8) error{OutOfMemory}![]const []const u8 {
    if (argv.len < 3) return argv;
    const head = exeStem(argv[0]);
    if (!std.ascii.eqlIgnoreCase(head, "uv")) return argv;
    if (!std.ascii.eqlIgnoreCase(argv[1], "run")) return argv;

    var rest_start: usize = 2;
    while (rest_start < argv.len and std.mem.startsWith(u8, argv[rest_start], "-")) {
        const flag = argv[rest_start];
        const takes_value = std.ascii.eqlIgnoreCase(flag, "--directory") or
            std.ascii.eqlIgnoreCase(flag, "--project") or
            std.ascii.eqlIgnoreCase(flag, "-p") or
            std.ascii.eqlIgnoreCase(flag, "--package");
        if (takes_value and rest_start + 1 < argv.len) {
            rest_start += 2;
            continue;
        }
        rest_start += 1;
    }
    if (rest_start >= argv.len) return argv;

    const py = (try resolveWhich(arena, io, "python")) orelse return argv;
    const tool = exeStem(argv[rest_start]);
    if (std.ascii.eqlIgnoreCase(tool, "python") or
        std.ascii.eqlIgnoreCase(tool, "python3") or
        std.ascii.eqlIgnoreCase(tool, "py"))
    {
        var out = try arena.alloc([]const u8, 1 + (argv.len - rest_start - 1));
        out[0] = py;
        @memcpy(out[1..], argv[rest_start + 1 ..]);
        return out;
    }
    if (isUvRunModule(tool)) {
        var out = try arena.alloc([]const u8, 3 + (argv.len - rest_start - 1));
        out[0] = py;
        out[1] = "-m";
        out[2] = try arena.dupe(u8, tool);
        @memcpy(out[3..], argv[rest_start + 1 ..]);
        return out;
    }
    if (std.ascii.endsWithIgnoreCase(tool, ".py") or std.mem.endsWith(u8, argv[rest_start], ".py")) {
        var out = try arena.alloc([]const u8, 1 + (argv.len - rest_start));
        out[0] = py;
        @memcpy(out[1..], argv[rest_start..]);
        return out;
    }
    return argv;
}

fn joinDisplay(arena: std.mem.Allocator, argv: []const []const u8) error{OutOfMemory}![]const u8 {
    if (argv.len == 0) return "";
    var total: usize = argv.len - 1;
    for (argv) |a| total += a.len;
    var out = try arena.alloc(u8, total);
    var i: usize = 0;
    for (argv, 0..) |a, idx| {
        if (idx != 0) {
            out[i] = ' ';
            i += 1;
        }
        @memcpy(out[i..][0..a.len], a);
        i += a.len;
    }
    return out;
}

fn ensureTrailingNewline(arena: std.mem.Allocator, body: []const u8) error{OutOfMemory}![]const u8 {
    if (body.len != 0 and body[body.len - 1] == '\n') return body;
    return try std.fmt.allocPrint(arena, "{s}\n", .{body});
}

fn scriptExtension(lang: []const u8) []const u8 {
    if (std.ascii.eqlIgnoreCase(lang, "cmd") or
        std.ascii.eqlIgnoreCase(lang, "bat") or
        std.ascii.eqlIgnoreCase(lang, "batch"))
        return ".cmd";
    if (std.ascii.eqlIgnoreCase(lang, "python") or std.ascii.eqlIgnoreCase(lang, "py"))
        return ".py";
    return ".ps1";
}

fn normalizeScriptLang(lang: []const u8) []const u8 {
    if (std.ascii.eqlIgnoreCase(lang, "cmd") or
        std.ascii.eqlIgnoreCase(lang, "bat") or
        std.ascii.eqlIgnoreCase(lang, "batch"))
        return "cmd";
    if (std.ascii.eqlIgnoreCase(lang, "python") or std.ascii.eqlIgnoreCase(lang, "py"))
        return "python";
    return "pwsh";
}

fn randomHex12(io: std.Io, buf: *[12]u8) void {
    var bytes: [6]u8 = undefined;
    io.random(&bytes);
    const hex = "0123456789abcdef";
    for (bytes, 0..) |b, i| {
        buf[i * 2] = hex[b >> 4];
        buf[i * 2 + 1] = hex[b & 0xf];
    }
}

fn resolveScratchDir(arena: std.mem.Allocator, io: std.Io, opts: PrepareOptions) error{ OutOfMemory, OperationFailed }![]const u8 {
    if (opts.scratch_dir.len != 0) {
        // cwd.createDirPath accepts absolute sub_paths on Zig 0.16.
        std.Io.Dir.cwd().createDirPath(io, opts.scratch_dir) catch return error.OperationFailed;
        return opts.scratch_dir;
    }
    const temp = getEnvAlloc(arena, "TEMP") orelse getEnvAlloc(arena, "TMP") orelse blk: {
        if (builtin.os.tag != .windows) break :blk try arena.dupe(u8, "/tmp");
        break :blk try arena.dupe(u8, ".");
    };
    const dir = try std.fs.path.join(arena, &.{ temp, "remedy-host" });
    std.Io.Dir.cwd().createDirPath(io, dir) catch return error.OperationFailed;
    return dir;
}

fn writeScriptFile(io: std.Io, lang: []const u8, body: []const u8, path: []const u8) error{OperationFailed}!void {
    if (body.len > max_script_chars) return error.OperationFailed;
    const kind = normalizeScriptLang(lang);
    var file = if (std.fs.path.isAbsolute(path))
        std.Io.Dir.createFileAbsolute(io, path, .{}) catch return error.OperationFailed
    else
        std.Io.Dir.cwd().createFile(io, path, .{}) catch return error.OperationFailed;
    defer file.close(io);

    if (std.mem.eql(u8, kind, "pwsh")) {
        file.writeStreamingAll(io, &utf8_bom) catch return error.OperationFailed;
        file.writeStreamingAll(io, body) catch return error.OperationFailed;
    } else {
        file.writeStreamingAll(io, body) catch return error.OperationFailed;
    }
}

const ScriptLaunch = struct {
    argv: []const []const u8,
    path: []const u8,
    lang: []const u8,
    body: []const u8,
};

fn launchScript(
    arena: std.mem.Allocator,
    io: std.Io,
    lang: []const u8,
    body: []const u8,
    opts: PrepareOptions,
) error{ OutOfMemory, OperationFailed }!ScriptLaunch {
    const kind = normalizeScriptLang(if (lang.len == 0) "pwsh" else lang);
    const ext = scriptExtension(kind);
    var hex: [12]u8 = undefined;
    randomHex12(io, &hex);
    const name = try std.fmt.allocPrint(arena, "host_{s}{s}", .{ hex[0..], ext });
    const dir = try resolveScratchDir(arena, io, opts);
    const path = try std.fs.path.join(arena, &.{ dir, name });
    const text = try ensureTrailingNewline(arena, body);
    try writeScriptFile(io, kind, text, path);

    if (std.mem.eql(u8, kind, "pwsh")) {
        const exe = (try resolveWhich(arena, io, "pwsh")) orelse
            (try resolveWhich(arena, io, "powershell")) orelse
            try arena.dupe(u8, "pwsh");
        const argv = try arena.alloc([]const u8, 7);
        argv[0] = exe;
        argv[1] = "-NoProfile";
        argv[2] = "-NonInteractive";
        argv[3] = "-ExecutionPolicy";
        argv[4] = "Bypass";
        argv[5] = "-File";
        argv[6] = path;
        return .{ .argv = argv, .path = path, .lang = "pwsh", .body = body };
    }
    if (std.mem.eql(u8, kind, "cmd")) {
        const exe = (try resolveWhich(arena, io, "cmd")) orelse try arena.dupe(u8, "cmd.exe");
        const argv = try arena.alloc([]const u8, 3);
        argv[0] = exe;
        argv[1] = "/c";
        argv[2] = path;
        return .{ .argv = argv, .path = path, .lang = "cmd", .body = body };
    }
    // python
    const py = (try resolveWhich(arena, io, "python")) orelse return error.OperationFailed;
    const argv = try arena.alloc([]const u8, 2);
    argv[0] = py;
    argv[1] = path;
    return .{ .argv = argv, .path = path, .lang = "python", .body = body };
}

/// Prepare argv from a structured HostOp (Python `prepare_host_op` for
/// run|script|mkdir|which|env|chain). `raw` is not handled here yet.
pub fn prepareHostOp(
    arena: std.mem.Allocator,
    io: std.Io,
    op: HostOp,
    opts: PrepareOptions,
) error{ OutOfMemory, OperationFailed, Unsupported }!PreparedCommand {
    const host_fallback = if (op.host.len != 0) op.host else defaultHostName();

    switch (op.kind) {
        .run => {
            var argv = op.argv;
            if (argv.len != 0) {
                if (try resolveWhich(arena, io, argv[0])) |resolved| {
                    var copy = try arena.alloc([]const u8, argv.len);
                    copy[0] = resolved;
                    @memcpy(copy[1..], argv[1..]);
                    argv = copy;
                }
                argv = try deflateUvRun(arena, io, argv);
            }
            return .{
                .argv = argv,
                .display = try joinDisplay(arena, argv),
                .kind = "argv",
                .ir = op,
                .host_name = host_fallback,
            };
        },
        .script => {
            const launch = try launchScript(arena, io, op.lang, op.body, opts);
            const display = try std.fmt.allocPrint(arena, "{s} -File {s}", .{ launch.lang, launch.path });
            return .{
                .argv = launch.argv,
                .display = display,
                .kind = "script",
                .ir = op,
                .script_path = launch.path,
                .host_name = launch.lang,
            };
        },
        .raw => return error.Unsupported,
        .mkdir, .which, .env, .chain => {
            return .{
                .argv = &.{},
                .display = op.kind.asText(),
                .kind = op.kind.asText(),
                .ir = op,
                .host_name = host_fallback,
            };
        },
    }
}

const PrepareRequest = struct {
    op: HostOp,
    opts: PrepareOptions,
};

fn parsePrepareRequest(arena: std.mem.Allocator, json: []const u8) error{ OutOfMemory, InvalidArgument }!PrepareRequest {
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json, .{}) catch return error.InvalidArgument;
    const object = switch (parsed) {
        .object => |o| o,
        else => return error.InvalidArgument,
    };

    var opts: PrepareOptions = .{};
    if (object.get("scratch_dir")) |v| opts.scratch_dir = try asString(arena, v);
    if (object.get("project_path")) |v| opts.project_path = try asString(arena, v);

    if (object.get("op")) |op_v| {
        return .{ .op = try fromJsonValue(arena, op_v), .opts = opts };
    }
    // Bare HostOp object.
    if (object.get("kind") != null) {
        return .{ .op = try fromJsonValue(arena, parsed), .opts = opts };
    }
    return error.InvalidArgument;
}

fn prepareToOwnedJson(json_in: []const u8) Error![]u8 {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();

    var arena_state = std.heap.ArenaAllocator.init(host.allocator);
    errdefer arena_state.deinit();
    const arena = arena_state.allocator();

    const req = parsePrepareRequest(arena, json_in) catch return error.InvalidArgument;
    const prep = prepareHostOp(arena, io, req.op, req.opts) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        error.OperationFailed => return error.OperationFailed,
        error.Unsupported => return error.Unsupported,
    };
    const encoded = preparedToJson(host.allocator, prep) catch return error.OutOfMemory;
    // Arena can drop; encoded owns its bytes on host.allocator.
    arena_state.deinit();
    return encoded;
}

fn readFileAlloc(gpa: std.mem.Allocator, io: std.Io, path: []const u8) ![]u8 {
    var file = if (std.fs.path.isAbsolute(path))
        try std.Io.Dir.openFileAbsolute(io, path, .{})
    else
        try std.Io.Dir.cwd().openFile(io, path, .{});
    defer file.close(io);
    var reader = file.reader(io, &.{});
    return reader.interface.allocRemaining(gpa, .limited(max_script_chars + 64));
}

fn loadPrepareFixture(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    const candidates = [_][]const u8{
        "../../tests/fixtures/host_ir/prepare_argv_scriptfile.json",
        "tests/fixtures/host_ir/prepare_argv_scriptfile.json",
        "../tests/fixtures/host_ir/prepare_argv_scriptfile.json",
    };
    for (candidates) |rel| {
        if (readFileAlloc(gpa, io, rel)) |bytes| return bytes else |_| {}
    }
    return error.FileNotFound;
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

/// Prepare a HostOp JSON into a PreparedCommand JSON (ABI 4).
/// Input may be a bare HostOp or `{op, scratch_dir?, project_path?}`.
export fn remedy_core_host_op_prepare(
    json_in: ?[*]const u8,
    json_in_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(prepareToOwnedJson(input), out_json, out_len);
}

fn expectFieldEql(op: HostOp, other: HostOp) !void {
    try std.testing.expectEqual(op.kind, other.kind);
    try std.testing.expectEqual(op.argv.len, other.argv.len);
    for (op.argv, other.argv) |a, b| try std.testing.expectEqualStrings(a, b);
    try std.testing.expectEqual(op.paths.len, other.paths.len);
    for (op.paths, other.paths) |a, b| try std.testing.expectEqualStrings(a, b);
    try std.testing.expectEqualStrings(op.name, other.name);
    try std.testing.expectEqualStrings(op.lang, other.lang);
    try std.testing.expectEqualStrings(op.body, other.body);
    try std.testing.expectEqualStrings(op.host, other.host);
    try std.testing.expectEqualStrings(op.text, other.text);
    try std.testing.expectEqualStrings(op.cwd, other.cwd);
    try std.testing.expectEqual(op.env.len, other.env.len);
    try std.testing.expectEqual(op.ops.len, other.ops.len);
    for (op.ops, other.ops) |a, b| try expectFieldEql(a, b);
}

fn normExeToken(token: []const u8, script_path: []const u8) []const u8 {
    if (script_path.len != 0 and std.mem.eql(u8, token, script_path)) return "<SCRIPT_PATH>";
    const base = blk: {
        var head = token;
        if (std.mem.lastIndexOfScalar(u8, head, '/')) |i| head = head[i + 1 ..];
        if (std.mem.lastIndexOfScalar(u8, head, '\\')) |i| head = head[i + 1 ..];
        break :blk head;
    };
    var lower_buf: [256]u8 = undefined;
    const lower = if (base.len <= lower_buf.len) blk: {
        for (base, 0..) |c, i| lower_buf[i] = std.ascii.toLower(c);
        break :blk lower_buf[0..base.len];
    } else base;
    if (std.mem.startsWith(u8, lower, "pwsh") or std.mem.startsWith(u8, lower, "powershell"))
        return "<PWSH>";
    if (std.mem.eql(u8, lower, "cmd") or std.mem.eql(u8, lower, "cmd.exe") or std.mem.startsWith(u8, lower, "cmd."))
        return "<CMD>";
    if (std.mem.startsWith(u8, lower, "git")) return "<GIT>";
    if (std.mem.startsWith(u8, lower, "python") or std.mem.startsWith(u8, lower, "py.exe") or
        std.mem.eql(u8, lower, "python") or std.mem.eql(u8, lower, "python3") or std.mem.eql(u8, lower, "py"))
        return "<PYTHON>";
    return token;
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

fn jsonStringArray(obj: std.json.ObjectMap, key: []const u8) ?[]const std.json.Value {
    const v = obj.get(key) orelse return null;
    return switch (v) {
        .array => |a| a.items,
        else => null,
    };
}

test "shell_ir roundtrips run_pytest fixture case" {
    const fixture =
        \\{"kind":"run","argv":["python","-m","pytest","-q"],"cwd":"."}
    ;
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const op = try parse(arena, fixture);
    try std.testing.expectEqual(OpKind.run, op.kind);
    try std.testing.expectEqual(@as(usize, 4), op.argv.len);
    try std.testing.expectEqualStrings("python", op.argv[0]);
    try std.testing.expectEqualStrings("-q", op.argv[3]);
    try std.testing.expectEqualStrings(".", op.cwd);

    const encoded = try toJson(std.testing.allocator, op);
    defer std.testing.allocator.free(encoded);

    var arena2 = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena2.deinit();
    const back = try parse(arena2.allocator(), encoded);
    try expectFieldEql(op, back);
}

test "shell_ir coerces unknown kind to raw" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const op = try parse(arena_state.allocator(), "{\"kind\":\"nope\",\"argv\":[1,2]}");
    try std.testing.expectEqual(OpKind.raw, op.kind);
    try std.testing.expectEqual(@as(usize, 2), op.argv.len);
    try std.testing.expectEqualStrings("1", op.argv[0]);
}

test "shell_ir prepare_op cases match prepare_argv_scriptfile fixtures" {
    const io = testingIo();
    const fixture_json = try loadPrepareFixture(std.testing.allocator, io);
    defer std.testing.allocator.free(fixture_json);

    var parsed = try std.json.parseFromSlice(std.json.Value, std.testing.allocator, fixture_json, .{});
    defer parsed.deinit();
    const root_obj = parsed.value.object;
    const cases = root_obj.get("cases").?.array.items;

    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    var scratch_buf: [std.fs.max_path_bytes]u8 = undefined;
    const scratch_len = try tmp.dir.realPath(io, &scratch_buf);
    const scratch_path = scratch_buf[0..scratch_len];

    var passed: usize = 0;
    for (cases) |case_v| {
        const case = case_v.object;
        const kind = jsonStringField(case, "kind") orelse continue;
        if (!std.mem.eql(u8, kind, "prepare_op")) continue;
        const input = case.get("input").?.object;
        const op_json = input.get("op").?;
        const expected = case.get("expected").?.object;

        var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
        defer arena_state.deinit();
        const arena = arena_state.allocator();

        const op = try fromJsonValue(arena, op_json);
        const prep = try prepareHostOp(arena, io, op, .{ .scratch_dir = scratch_path });

        const exp_kind = jsonStringField(expected, "kind").?;
        try std.testing.expectEqualStrings(exp_kind, prep.kind);

        if (jsonStringField(expected, "host")) |exp_host| {
            const want = if (std.mem.eql(u8, exp_host, "<DEFAULT>"))
                defaultHostName()
            else
                exp_host;
            try std.testing.expectEqualStrings(want, prep.host_name);
        }
        if (jsonStringField(expected, "display")) |exp_display| {
            try std.testing.expectEqualStrings(exp_display, prep.display);
        }

        if (jsonStringArray(expected, "argv")) |exp_argv| {
            try std.testing.expectEqual(exp_argv.len, prep.argv.len);
            for (exp_argv, prep.argv) |ev, av| {
                try std.testing.expectEqualStrings(ev.string, av);
            }
        }

        if (jsonStringArray(expected, "argv_template")) |tmpl| {
            try std.testing.expectEqual(tmpl.len, prep.argv.len);
            for (tmpl, prep.argv) |tv, av| {
                const got = normExeToken(av, prep.script_path);
                try std.testing.expectEqualStrings(tv.string, got);
            }
        }

        if (jsonStringField(expected, "script_suffix")) |suffix| {
            try std.testing.expect(std.mem.endsWith(u8, prep.script_path, suffix));
            const body_bytes = try readFileAlloc(std.testing.allocator, io, prep.script_path);
            defer std.testing.allocator.free(body_bytes);

            if (jsonBoolField(expected, "script_has_bom")) |has_bom| {
                const starts_bom = body_bytes.len >= 3 and std.mem.eql(u8, body_bytes[0..3], &utf8_bom);
                try std.testing.expectEqual(has_bom, starts_bom);
            }
            if (jsonStringField(expected, "script_body_utf8_sig")) |exp_body| {
                const decoded = if (body_bytes.len >= 3 and std.mem.eql(u8, body_bytes[0..3], &utf8_bom))
                    body_bytes[3..]
                else
                    body_bytes;
                try std.testing.expectEqualStrings(exp_body, decoded);
            }
        }

        if (expected.get("ir")) |ir_v| {
            const ir_obj = ir_v.object;
            if (jsonStringField(ir_obj, "kind")) |k| {
                try std.testing.expectEqualStrings(k, prep.ir.kind.asText());
            }
            if (jsonStringField(ir_obj, "lang")) |lang| {
                try std.testing.expectEqualStrings(lang, prep.ir.lang);
            }
            if (jsonStringField(ir_obj, "body")) |body| {
                try std.testing.expectEqualStrings(body, prep.ir.body);
            }
            if (jsonStringArray(ir_obj, "argv")) |argv| {
                try std.testing.expectEqual(argv.len, prep.ir.argv.len);
                for (argv, prep.ir.argv) |ev, av| {
                    try std.testing.expectEqualStrings(ev.string, av);
                }
            }
            if (jsonStringArray(ir_obj, "paths")) |paths| {
                try std.testing.expectEqual(paths.len, prep.ir.paths.len);
                for (paths, prep.ir.paths) |ev, av| {
                    try std.testing.expectEqualStrings(ev.string, av);
                }
            }
        }

        passed += 1;
    }
    try std.testing.expectEqual(@as(usize, 4), passed);
}

test "shell_ir prepare which yields empty argv" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const op = try parse(arena, "{\"kind\":\"which\",\"name\":\"git\"}");
    const prep = try prepareHostOp(arena, testingIo(), op, .{});
    try std.testing.expectEqualStrings("which", prep.kind);
    try std.testing.expectEqual(@as(usize, 0), prep.argv.len);
    try std.testing.expectEqualStrings("which", prep.display);
    try std.testing.expectEqualStrings("git", prep.ir.name);
}

test "shell_ir host_op_prepare C ABI returns prepared JSON" {
    const io = testingIo();
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    var scratch_buf: [std.fs.max_path_bytes]u8 = undefined;
    const scratch_len = try tmp.dir.realPath(io, &scratch_buf);
    const scratch_path = scratch_buf[0..scratch_len];

    const scratch_json = try std.json.Stringify.valueAlloc(std.testing.allocator, scratch_path, .{});
    defer std.testing.allocator.free(scratch_json);
    const input = try std.fmt.allocPrint(
        std.testing.allocator,
        "{{\"op\":{{\"kind\":\"run\",\"argv\":[\"git\",\"status\"]}},\"scratch_dir\":{s}}}",
        .{scratch_json},
    );
    defer std.testing.allocator.free(input);

    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const status = remedy_core_host_op_prepare(input.ptr, input.len, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, status);
    try std.testing.expect(out_ptr != null);
    defer host.allocator.free(out_ptr.?[0..out_len]);

    const out = out_ptr.?[0..out_len];
    try std.testing.expect(std.mem.indexOf(u8, out, "\"kind\":\"argv\"") != null);
    try std.testing.expect(std.mem.indexOf(u8, out, "status") != null);
}
