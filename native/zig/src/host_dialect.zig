//! Last-good host dialect for this machine (`~/.remedy/host/dialect.json`).
//! Owns probe/load/save/record/format previously in Python `execution/host/dialect.py`.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const shell_ir = @import("shell_ir.zig");

const Status = root.Status;
const Error = host.Error;
const allocator = host.allocator;
const is_windows = builtin.os.tag == .windows;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const failed_status: i32 = @intFromEnum(Status.operation_failed);

const max_json_bytes: usize = 64 * 1024;

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

fn pathExists(io: std.Io, path: []const u8) bool {
    if (path.len == 0) return false;
    if (std.fs.path.isAbsolute(path)) {
        std.Io.Dir.accessAbsolute(io, path, .{}) catch return false;
        return true;
    }
    std.Io.Dir.cwd().access(io, path, .{}) catch return false;
    return true;
}

fn readFileAlloc(gpa: std.mem.Allocator, io: std.Io, path: []const u8) ![]u8 {
    var file = if (std.fs.path.isAbsolute(path))
        try std.Io.Dir.openFileAbsolute(io, path, .{})
    else
        try std.Io.Dir.cwd().openFile(io, path, .{});
    defer file.close(io);
    var reader = file.reader(io, &.{});
    return reader.interface.allocRemaining(gpa, .limited(max_json_bytes));
}

fn writeJsonAtomic(io: std.Io, path: []const u8, json: []const u8) Error!void {
    const dir = std.fs.path.dirname(path) orelse ".";
    // cwd.createDirPath accepts absolute sub_paths on Zig 0.16.
    std.Io.Dir.cwd().createDirPath(io, dir) catch return error.OperationFailed;
    var buf: [12]u8 = undefined;
    var bytes: [6]u8 = undefined;
    io.random(&bytes);
    const hex = "0123456789abcdef";
    for (bytes, 0..) |b, i| {
        buf[i * 2] = hex[b >> 4];
        buf[i * 2 + 1] = hex[b & 0xf];
    }
    const tmp = try std.fmt.allocPrint(allocator, "{s}.{s}.tmp", .{ path, buf[0..] });
    defer allocator.free(tmp);

    var file = if (std.fs.path.isAbsolute(tmp))
        std.Io.Dir.createFileAbsolute(io, tmp, .{}) catch return error.OperationFailed
    else
        std.Io.Dir.cwd().createFile(io, tmp, .{}) catch return error.OperationFailed;
    file.writeStreamingAll(io, json) catch {
        file.close(io);
        return error.OperationFailed;
    };
    file.close(io);

    if (std.fs.path.isAbsolute(path) and std.fs.path.isAbsolute(tmp)) {
        std.Io.Dir.renameAbsolute(tmp, path, io) catch {
            _ = std.Io.Dir.deleteFileAbsolute(io, tmp) catch {};
            return error.OperationFailed;
        };
    } else {
        const cwd = std.Io.Dir.cwd();
        cwd.rename(tmp, cwd, path, io) catch {
            _ = cwd.deleteFile(io, tmp) catch {};
            return error.OperationFailed;
        };
    }
}

fn basenameLower(path: []const u8, buf: []u8) []const u8 {
    var base = path;
    if (std.mem.lastIndexOfAny(u8, base, "/\\")) |i| base = base[i + 1 ..];
    const n = @min(base.len, buf.len);
    for (base[0..n], 0..) |c, i| buf[i] = std.ascii.toLower(c);
    var name = buf[0..n];
    if (std.mem.endsWith(u8, name, ".exe")) name = name[0 .. name.len - 4];
    return name;
}

fn stemIsPython(path: []const u8) bool {
    var buf: [128]u8 = undefined;
    const name = basenameLower(path, &buf);
    if (name.len < 6) return false;
    if (!std.mem.startsWith(u8, name, "python")) return false;
    // python / pythonw / python3 / python3.12 / pythonw3
    var rest = name[6..];
    if (rest.len > 0 and rest[0] == 'w') rest = rest[1..];
    for (rest) |c| {
        if (!(std.ascii.isDigit(c) or c == '.')) return false;
    }
    return true;
}

fn looksLikeSidecar(path: []const u8) bool {
    var buf: [128]u8 = undefined;
    const name = basenameLower(path, &buf);
    return std.mem.indexOf(u8, name, "remedy") != null and !stemIsPython(path);
}

pub fn isUsableHostPython(path: []const u8) bool {
    const p = std.mem.trim(u8, path, " \t\"'");
    if (p.len == 0) return false;
    if (looksLikeSidecar(p)) return false;
    var low_buf: [512]u8 = undefined;
    const n = @min(p.len, low_buf.len);
    for (p[0..n], 0..) |c, i| {
        low_buf[i] = if (c == '\\') '/' else std.ascii.toLower(c);
    }
    if (std.mem.indexOf(u8, low_buf[0..n], "windowsapps") != null) return false;
    var name_buf: [128]u8 = undefined;
    const name = basenameLower(p, &name_buf);
    if (std.mem.eql(u8, name, "uv")) return false;
    if (std.mem.eql(u8, name, "py") or std.mem.eql(u8, name, "pyw")) return true;
    return stemIsPython(p);
}

fn resolveHome(arena: std.mem.Allocator, home_in: []const u8) Error![]const u8 {
    const trimmed = std.mem.trim(u8, home_in, " \t");
    if (trimmed.len != 0) return try arena.dupe(u8, trimmed);
    if (shell_ir.getEnvAlloc(arena, "REMEDY_HOME")) |env| {
        if (env.len != 0) return env;
    }
    if (shell_ir.getEnvAlloc(arena, "HOME")) |home| {
        return try std.fs.path.join(arena, &.{ home, ".remedy" });
    }
    if (is_windows) {
        if (shell_ir.getEnvAlloc(arena, "USERPROFILE")) |up| {
            return try std.fs.path.join(arena, &.{ up, ".remedy" });
        }
    }
    return error.InvalidArgument;
}

fn dialectPath(arena: std.mem.Allocator, home: []const u8) Error![]const u8 {
    return try std.fs.path.join(arena, &.{ home, "host", "dialect.json" });
}

pub const Dialect = struct {
    host: []const u8 = "cmd",
    python_cmd: []const u8 = "",
    git_cmd: []const u8 = "",
    rg_cmd: []const u8 = "",
    curl_kind: []const u8 = "",
    pwsh_cmd: []const u8 = "",
    last_good_verify: []const u8 = "",
    successes: i64 = 0,
    last_success_at: []const u8 = "",
    notes: []const []const u8 = &[_][]const u8{},
};

fn emptyDialect() Dialect {
    return .{
        .host = if (is_windows) "cmd" else "posix",
    };
}

fn normalizeRgPath(arena: std.mem.Allocator, path: []const u8) Error![]const u8 {
    if (path.len == 0) return "";
    // Heal leftover `str((Path, source))` tuple stamps.
    if (std.mem.startsWith(u8, path, "(")) return "";
    // Keep Unix probe paths as POSIX.
    var posix_buf: [1024]u8 = undefined;
    if (path.len < posix_buf.len) {
        for (path, 0..) |c, i| posix_buf[i] = if (c == '\\') '/' else c;
        if (posix_buf[0] == '/') return try arena.dupe(u8, posix_buf[0..path.len]);
    }
    return try arena.dupe(u8, path);
}

fn findBundledRg(arena: std.mem.Allocator, io: std.Io, home: []const u8) Error![]const u8 {
    const name = if (is_windows) "rg.exe" else "rg";
    const candidates = [_][]const u8{
        try std.fs.path.join(arena, &.{ home, "bin", name }),
    };
    for (candidates) |c| {
        if (pathExists(io, c)) return try normalizeRgPath(arena, c);
    }
    return "";
}

pub fn probe(arena: std.mem.Allocator, io: std.Io, home: []const u8) Error!Dialect {
    var python: []const u8 = "";
    for ([_][]const u8{ "python", "python3", "py" }) |name| {
        if (try shell_ir.resolveWhich(arena, io, name)) |found| {
            if (isUsableHostPython(found)) {
                python = found;
                break;
            }
        }
    }
    if (shell_ir.getEnvAlloc(arena, "REMEDY_PYTHON")) |override| {
        const o = std.mem.trim(u8, override, " \t\"'");
        if (o.len != 0 and pathExists(io, o) and isUsableHostPython(o)) {
            python = o;
        }
    }

    const git = (try shell_ir.resolveWhich(arena, io, "git")) orelse "";
    const pwsh = (try shell_ir.resolveWhich(arena, io, "pwsh")) orelse "";
    const curl = (try shell_ir.resolveWhich(arena, io, "curl")) orelse "";

    var rg = try findBundledRg(arena, io, home);
    if (rg.len == 0) {
        if (try shell_ir.resolveWhich(arena, io, "rg")) |found| {
            rg = try normalizeRgPath(arena, found);
        } else if (try shell_ir.resolveWhich(arena, io, "ripgrep")) |found| {
            rg = try normalizeRgPath(arena, found);
        }
    }

    return .{
        .host = if (is_windows) "cmd" else "posix",
        .python_cmd = python,
        .git_cmd = git,
        .rg_cmd = rg,
        .curl_kind = if (curl.len != 0) "real" else "missing",
        .pwsh_cmd = pwsh,
    };
}

fn stringifyDialect(d: Dialect) Error![]u8 {
    // Cap notes at 12 for wire shape.
    var notes_buf: [12][]const u8 = undefined;
    const n = @min(d.notes.len, notes_buf.len);
    for (d.notes[0..n], 0..) |note, i| notes_buf[i] = note;
    return host.jsonAlloc(.{
        .host = d.host,
        .python_cmd = d.python_cmd,
        .git_cmd = d.git_cmd,
        .rg_cmd = d.rg_cmd,
        .curl_kind = d.curl_kind,
        .pwsh_cmd = d.pwsh_cmd,
        .last_good_verify = d.last_good_verify,
        .successes = d.successes,
        .last_success_at = d.last_success_at,
        .notes = notes_buf[0..n],
    });
}

const DialectJson = struct {
    host: []const u8 = "",
    python_cmd: []const u8 = "",
    git_cmd: []const u8 = "",
    rg_cmd: []const u8 = "",
    curl_kind: []const u8 = "",
    pwsh_cmd: []const u8 = "",
    last_good_verify: []const u8 = "",
    successes: i64 = 0,
    last_success_at: []const u8 = "",
    notes: []const []const u8 = &[_][]const u8{},
};

fn parseDialect(arena: std.mem.Allocator, raw: []const u8) Error!Dialect {
    const parsed = std.json.parseFromSliceLeaky(DialectJson, arena, raw, .{
        .ignore_unknown_fields = true,
        .allocate = .alloc_if_needed,
    }) catch return emptyDialect();
    var notes = try arena.alloc([]const u8, @min(parsed.notes.len, 12));
    for (parsed.notes[0..notes.len], 0..) |n, i| notes[i] = n;
    return .{
        .host = if (parsed.host.len != 0) parsed.host else (if (is_windows) "cmd" else "posix"),
        .python_cmd = parsed.python_cmd,
        .git_cmd = parsed.git_cmd,
        .rg_cmd = parsed.rg_cmd,
        .curl_kind = parsed.curl_kind,
        .pwsh_cmd = parsed.pwsh_cmd,
        .last_good_verify = parsed.last_good_verify,
        .successes = parsed.successes,
        .last_success_at = parsed.last_success_at,
        .notes = notes,
    };
}

fn mergeProbe(d: *Dialect, probed: Dialect) void {
    if (d.python_cmd.len == 0 or !isUsableHostPython(d.python_cmd)) d.python_cmd = probed.python_cmd;
    if (d.git_cmd.len == 0) d.git_cmd = probed.git_cmd;
    if (d.rg_cmd.len == 0 or std.mem.startsWith(u8, d.rg_cmd, "(")) d.rg_cmd = probed.rg_cmd;
    if (d.curl_kind.len == 0) d.curl_kind = probed.curl_kind;
    if (d.pwsh_cmd.len == 0) d.pwsh_cmd = probed.pwsh_cmd;
    if (d.host.len == 0) d.host = probed.host;
}

fn loadDialect(arena: std.mem.Allocator, io: std.Io, home: []const u8) Error!Dialect {
    const path = try dialectPath(arena, home);
    const raw = readFileAlloc(arena, io, path) catch {
        const probed = try probe(arena, io, home);
        return probed;
    };
    var d = try parseDialect(arena, raw);
    const needs = d.python_cmd.len == 0 or !isUsableHostPython(d.python_cmd) or
        d.git_cmd.len == 0 or d.rg_cmd.len == 0 or std.mem.startsWith(u8, d.rg_cmd, "(") or
        d.curl_kind.len == 0 or d.pwsh_cmd.len == 0 or d.host.len == 0;
    if (needs) {
        const probed = try probe(arena, io, home);
        mergeProbe(&d, probed);
    }
    return d;
}

fn utcNowStamp(io: std.Io, buf: []u8) []const u8 {
    const epoch_s: u64 = @intCast(@max(@as(i64, 0), std.Io.Timestamp.now(io, .real).toSeconds()));
    const epoch: std.time.epoch.EpochSeconds = .{ .secs = epoch_s };
    const day = epoch.getEpochDay();
    const day_seconds = epoch.getDaySeconds();
    const year_day = day.calculateYearDay();
    const month_day = year_day.calculateMonthDay();
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2}T{d:0>2}:{d:0>2}:{d:0>2}Z", .{
        year_day.year,
        month_day.month.numeric(),
        month_day.day_index + 1,
        day_seconds.getHoursIntoDay(),
        day_seconds.getMinutesIntoHour(),
        day_seconds.getSecondsIntoMinute(),
    }) catch "1970-01-01T00:00:00Z";
}

fn isVerifyCommand(command: []const u8) bool {
    var low_buf: [512]u8 = undefined;
    const n = @min(command.len, low_buf.len);
    for (command[0..n], 0..) |c, i| low_buf[i] = std.ascii.toLower(c);
    const low = low_buf[0..n];
    const keys = [_][]const u8{ "pytest", "py_compile", "cargo test", "npm test", "go test" };
    for (keys) |k| {
        if (std.mem.indexOf(u8, low, k) != null) return true;
    }
    return false;
}

fn formatLine(arena: std.mem.Allocator, d: Dialect) Error![]u8 {
    var bits: std.ArrayList([]const u8) = .empty;
    defer bits.deinit(arena);
    try bits.append(arena, try std.fmt.allocPrint(arena, "Host bridge: {s}", .{if (d.host.len != 0) d.host else "cmd"}));
    if (d.python_cmd.len != 0) try bits.append(arena, try std.fmt.allocPrint(arena, "python={s}", .{d.python_cmd}));
    if (d.rg_cmd.len != 0) try bits.append(arena, "rg=yes");
    if (d.curl_kind.len != 0) try bits.append(arena, try std.fmt.allocPrint(arena, "curl={s}", .{d.curl_kind}));
    if (d.last_good_verify.len != 0) {
        const clip = if (d.last_good_verify.len > 80) d.last_good_verify[0..80] else d.last_good_verify;
        try bits.append(arena, try std.fmt.allocPrint(arena, "last_verify={s}", .{clip}));
    }
    try bits.append(arena, "prefer host_run(argv) / host_mkdir / host_script over quoted bash");
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(allocator);
    for (bits.items, 0..) |bit, i| {
        if (i != 0) try out.appendSlice(allocator, " · ");
        try out.appendSlice(allocator, bit);
    }
    return try out.toOwnedSlice(allocator);
}

fn probeToOwned(home_in: []const u8, persist: bool) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    const d = try probe(arena, io, home);
    const json = try stringifyDialect(d);
    if (persist) {
        const path = try dialectPath(arena, home);
        try writeJsonAtomic(io, path, json);
    }
    return json;
}

fn loadToOwned(home_in: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    const d = try loadDialect(arena, io, home);
    return try stringifyDialect(d);
}

fn recordToOwned(home_in: []const u8, command: []const u8, note: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    var d = try loadDialect(arena, io, home);
    d.successes += 1;
    var stamp_buf: [32]u8 = undefined;
    const stamp = utcNowStamp(io, &stamp_buf);
    d.last_success_at = try arena.dupe(u8, stamp);
    if (isVerifyCommand(command)) {
        const clip = if (command.len > 240) command[0..240] else std.mem.trim(u8, command, " \t");
        d.last_good_verify = try arena.dupe(u8, clip);
    }
    if (note.len != 0) {
        var notes = try arena.alloc([]const u8, @min(d.notes.len + 1, 12));
        notes[0] = note;
        var w: usize = 1;
        for (d.notes) |n| {
            if (w >= notes.len) break;
            if (std.mem.eql(u8, n, note)) continue;
            notes[w] = n;
            w += 1;
        }
        d.notes = notes[0..w];
    }
    const json = try stringifyDialect(d);
    const path = try dialectPath(arena, home);
    try writeJsonAtomic(io, path, json);
    return json;
}

fn formatToOwned(home_in: []const u8, dialect_json: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const d = if (dialect_json.len != 0)
        try parseDialect(arena, dialect_json)
    else blk: {
        const home = try resolveHome(arena, home_in);
        break :blk try loadDialect(arena, io, home);
    };
    return try formatLine(arena, d);
}

/// Probe PATH tools into dialect JSON. `persist` 1 writes dialect.json under home.
export fn remedy_core_dialect_probe(
    home_ptr: ?[*]const u8,
    home_len: usize,
    persist: u8,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(probeToOwned(slice(home_ptr, home_len), persist != 0), out_json, out_len);
}

/// Load dialect.json (healing empty/sidecar fields via probe).
export fn remedy_core_dialect_load(
    home_ptr: ?[*]const u8,
    home_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(loadToOwned(slice(home_ptr, home_len)), out_json, out_len);
}

/// Record a successful host command into dialect.json; returns updated dialect JSON.
export fn remedy_core_dialect_record_success(
    home_ptr: ?[*]const u8,
    home_len: usize,
    command_ptr: ?[*]const u8,
    command_len: usize,
    note_ptr: ?[*]const u8,
    note_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(
        recordToOwned(slice(home_ptr, home_len), slice(command_ptr, command_len), slice(note_ptr, note_len)),
        out_json,
        out_len,
    );
}

/// One-line host inject. Optional dialect_json skips disk load when non-empty.
export fn remedy_core_dialect_format_line(
    home_ptr: ?[*]const u8,
    home_len: usize,
    dialect_json_ptr: ?[*]const u8,
    dialect_json_len: usize,
    out_utf8: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(
        formatToOwned(slice(home_ptr, home_len), slice(dialect_json_ptr, dialect_json_len)),
        out_utf8,
        out_len,
    );
}

test "usable host python rejects sidecar" {
    try std.testing.expect(!isUsableHostPython("C:\\Apps\\remedy-desktop.exe"));
    try std.testing.expect(isUsableHostPython("C:\\Python312\\python.exe"));
    try std.testing.expect(isUsableHostPython("/usr/bin/python3"));
    try std.testing.expect(isUsableHostPython("py"));
}

test "dialect probe roundtrip" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();

    const stamp_s = std.Io.Timestamp.now(io, .real).toSeconds();
    const tmp = try std.fmt.allocPrint(arena, "zig-cache/dialect-test-{d}", .{stamp_s});
    std.Io.Dir.cwd().createDirPath(io, tmp) catch {};
    defer std.Io.Dir.cwd().deleteTree(io, tmp) catch {};

    const d = try probe(arena, io, tmp);
    try std.testing.expect(d.host.len > 0);
    const json = try stringifyDialect(d);
    defer allocator.free(json);
    try std.testing.expect(std.mem.indexOf(u8, json, "\"host\"") != null);

    const path = try dialectPath(arena, tmp);
    try writeJsonAtomic(io, path, json);
    const loaded = try loadDialect(arena, io, tmp);
    try std.testing.expectEqualStrings(d.host, loaded.host);
}
