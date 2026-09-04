//! First-home stretch — hardware, PATH tools, rooms, and local doors.
//! Owns census previously in Python `execution/host/stretch.py`.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const shell_ir = @import("shell_ir.zig");
const host_dialect = @import("host_dialect.zig");

const Status = root.Status;
const Error = host.Error;
const allocator = host.allocator;
const is_windows = builtin.os.tag == .windows;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);

const stale_days_default: i64 = 14;
const max_json_bytes: usize = 256 * 1024;

const tool_names = [_][]const u8{
    "python", "py",     "git",    "node",   "npm",   "npx",   "uv",      "pip",
    "pipx",   "cargo",  "rustc",  "go",     "gcc",   "clang", "cmake",   "docker",
    "rg",     "curl",   "pwsh",   "code",   "gh",    "ffmpeg","conda",   "winget",
    "choco",  "scoop",  "bun",    "pnpm",   "yarn",  "java",  "amd-smi", "rocm-smi",
    "vulkaninfo",
};

const room_keys = [_]struct { []const u8, []const u8 }{
    .{ "profile", "" },
    .{ "desktop", "Desktop" },
    .{ "documents", "Documents" },
    .{ "downloads", "Downloads" },
    .{ "pictures", "Pictures" },
};

const work_hints = [_][]const []const u8{
    &[_][]const u8{ "Documents", "Remedy Projects" },
    &[_][]const u8{"Projects"},
    &[_][]const u8{"dev"},
    &[_][]const u8{"src"},
};

const door_ports = [_]struct { []const u8, u16 }{
    .{ "remedy", 7400 },
    .{ "rmb", 8787 },
    .{ "vision", 8740 },
    .{ "ollama", 11434 },
    .{ "comfyui", 8188 },
};

const secret_bits = [_][]const u8{ "key", "token", "secret", "password", "auth", "cookie" };

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

fn isDir(io: std.Io, path: []const u8) bool {
    if (!pathExists(io, path)) return false;
    var dir = if (std.fs.path.isAbsolute(path))
        std.Io.Dir.openDirAbsolute(io, path, .{}) catch return false
    else
        std.Io.Dir.cwd().openDir(io, path, .{}) catch return false;
    dir.close(io);
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
    std.Io.Dir.cwd().createDirPath(io, dir) catch return error.OperationFailed;
    var bytes: [6]u8 = undefined;
    io.random(&bytes);
    const hex = "0123456789abcdef";
    var buf: [12]u8 = undefined;
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

fn looksSecret(text: []const u8) bool {
    var low_buf: [256]u8 = undefined;
    const n = @min(text.len, low_buf.len);
    for (text[0..n], 0..) |c, i| low_buf[i] = std.ascii.toLower(c);
    const low = low_buf[0..n];
    for (secret_bits) |bit| {
        if (std.mem.indexOf(u8, low, bit) != null) return true;
    }
    return false;
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

fn censusPath(arena: std.mem.Allocator, home: []const u8) Error![]const u8 {
    return try std.fs.path.join(arena, &.{ home, "host", "home.json" });
}

fn userProfile(arena: std.mem.Allocator) Error![]const u8 {
    if (is_windows) {
        if (shell_ir.getEnvAlloc(arena, "USERPROFILE")) |up| return up;
    }
    if (shell_ir.getEnvAlloc(arena, "HOME")) |home| return home;
    return error.OperationFailed;
}

fn nowEpochSecs(io: std.Io) i64 {
    return std.Io.Timestamp.now(io, .real).toSeconds();
}

fn utcNowStamp(io: std.Io, buf: []u8) []const u8 {
    const epoch_s: u64 = @intCast(@max(@as(i64, 0), nowEpochSecs(io)));
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

fn civilDays(y: i64, m: i64, d: i64) i64 {
    const y0 = if (m <= 2) y - 1 else y;
    const era = @divFloor(y0, 400);
    const yoe = y0 - era * 400;
    const mp = if (m > 2) m - 3 else m + 9;
    const doy = @divTrunc(153 * mp + 2, 5) + d - 1;
    const doe = yoe * 365 + @divFloor(yoe, 4) - @divFloor(yoe, 100) + doy;
    return era * 146097 + doe - 719468;
}

fn parseIsoStamp(text: []const u8) ?i64 {
    if (text.len < 19) return null;
    const year = std.fmt.parseInt(i64, text[0..4], 10) catch return null;
    const month = std.fmt.parseInt(i64, text[5..7], 10) catch return null;
    const day = std.fmt.parseInt(i64, text[8..10], 10) catch return null;
    const hour = std.fmt.parseInt(i64, text[11..13], 10) catch return null;
    const minute = std.fmt.parseInt(i64, text[14..16], 10) catch return null;
    const second = std.fmt.parseInt(i64, text[17..19], 10) catch return null;
    if (month < 1 or month > 12 or day < 1 or day > 31) return null;
    const days = civilDays(year, month, day) - civilDays(1970, 1, 1);
    return days * 86400 + hour * 3600 + minute * 60 + second;
}

fn portOpen(io: std.Io, port: u16) bool {
    // Windows Zig 0.16 panics on ConnectOptions.timeout ("TODO … with timeout").
    // Localhost refused ports fail fast with the default (.none) timeout.
    const address = std.Io.net.IpAddress.parse("127.0.0.1", port) catch return false;
    const stream = address.connect(io, .{ .mode = .stream }) catch return false;
    stream.close(io);
    return true;
}

fn probeRamMb() struct { total: u64, avail: u64 } {
    if (comptime is_windows) {
        const MEMORYSTATUSEX = extern struct {
            dwLength: u32,
            dwMemoryLoad: u32,
            ullTotalPhys: u64,
            ullAvailPhys: u64,
            ullTotalPageFile: u64,
            ullAvailPageFile: u64,
            ullTotalVirtual: u64,
            ullAvailVirtual: u64,
            ullAvailExtendedVirtual: u64,
        };
        const GlobalMemoryStatusEx = struct {
            extern "kernel32" fn GlobalMemoryStatusEx(lpBuffer: *MEMORYSTATUSEX) callconv(.winapi) i32;
        }.GlobalMemoryStatusEx;
        var status: MEMORYSTATUSEX = std.mem.zeroes(MEMORYSTATUSEX);
        status.dwLength = @sizeOf(MEMORYSTATUSEX);
        if (GlobalMemoryStatusEx(&status) != 0) {
            return .{
                .total = status.ullTotalPhys / (1024 * 1024),
                .avail = status.ullAvailPhys / (1024 * 1024),
            };
        }
        return .{ .total = 0, .avail = 0 };
    }
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const raw = readFileAlloc(allocator, io, "/proc/meminfo") catch return .{ .total = 0, .avail = 0 };
    defer allocator.free(raw);
    var total: u64 = 0;
    var avail: u64 = 0;
    var it = std.mem.splitScalar(u8, raw, '\n');
    while (it.next()) |line| {
        if (std.mem.startsWith(u8, line, "MemTotal:")) {
            var toks = std.mem.tokenizeAny(u8, line["MemTotal:".len..], " \t");
            if (toks.next()) |n| total = (std.fmt.parseInt(u64, n, 10) catch 0) / 1024;
        } else if (std.mem.startsWith(u8, line, "MemAvailable:")) {
            var toks = std.mem.tokenizeAny(u8, line["MemAvailable:".len..], " \t");
            if (toks.next()) |n| avail = (std.fmt.parseInt(u64, n, 10) catch 0) / 1024;
        }
    }
    return .{ .total = total, .avail = avail };
}

fn gbLabel(mb: u64, buf: []u8) []const u8 {
    if (mb >= 1024) {
        if (mb % 1024 < 80) {
            return std.fmt.bufPrint(buf, "{d} GB", .{mb / 1024}) catch "?";
        }
        const tenths = (mb * 10) / 1024;
        return std.fmt.bufPrint(buf, "{d}.{d} GB", .{ tenths / 10, tenths % 10 }) catch "?";
    }
    return std.fmt.bufPrint(buf, "{d} MB", .{mb}) catch "?";
}

fn jsonEscapeAppend(buf: *std.ArrayList(u8), gpa: std.mem.Allocator, text: []const u8) Error!void {
    for (text) |c| {
        switch (c) {
            '"' => try buf.appendSlice(gpa, "\\\""),
            '\\' => try buf.appendSlice(gpa, "\\\\"),
            '\n' => try buf.appendSlice(gpa, "\\n"),
            '\r' => try buf.appendSlice(gpa, "\\r"),
            '\t' => try buf.appendSlice(gpa, "\\t"),
            else => {
                if (c < 0x20) {
                    const hex = "0123456789abcdef";
                    try buf.appendSlice(gpa, "\\u00");
                    try buf.append(gpa, hex[c >> 4]);
                    try buf.append(gpa, hex[c & 0xf]);
                } else {
                    try buf.append(gpa, c);
                }
            },
        }
    }
}

const KV = struct { key: []const u8, value: []const u8 };
const Door = struct { key: []const u8, open: bool };
const GpuEntry = struct {
    name: []const u8 = "",
    vendor: []const u8 = "",
    vram_total_mb: u64 = 0,
    dedicated: bool = true,
    backend: []const u8 = "",
};

pub const Census = struct {
    stretched_at: []const u8 = "",
    os_name: []const u8 = "",
    os_release: []const u8 = "",
    arch: []const u8 = "",
    hostname: []const u8 = "",
    cpu_count: u32 = 0,
    ram_total_mb: u64 = 0,
    ram_avail_mb: u64 = 0,
    gpu_name: []const u8 = "",
    vram_total_mb: u64 = 0,
    nvidia: bool = false,
    gpu_vendor: []const u8 = "",
    gpus: []const GpuEntry = &[_]GpuEntry{},
    disk_home_free_gb: f64 = 0.0,
    tools: []const KV = &[_]KV{},
    missing: []const []const u8 = &[_][]const u8{},
    rooms: []const KV = &[_]KV{},
    work_rooms: []const KV = &[_]KV{},
    doors: []const Door = &[_]Door{},
    host: []const u8 = "cmd",
};

fn appendFmt(buf: *std.ArrayList(u8), gpa: std.mem.Allocator, comptime fmt: []const u8, args: anytype) Error!void {
    const piece = std.fmt.allocPrint(gpa, fmt, args) catch return error.OutOfMemory;
    defer gpa.free(piece);
    try buf.appendSlice(gpa, piece);
}

fn appendKvObject(buf: *std.ArrayList(u8), gpa: std.mem.Allocator, items: []const KV) Error!void {
    try buf.append(gpa, '{');
    var first = true;
    for (items) |kv| {
        if (looksSecret(kv.key) or looksSecret(kv.value)) continue;
        if (!first) try buf.append(gpa, ',');
        first = false;
        try buf.append(gpa, '"');
        try jsonEscapeAppend(buf, gpa, kv.key);
        try buf.appendSlice(gpa, "\":\"");
        const clipped = if (kv.value.len > 400) kv.value[0..400] else kv.value;
        try jsonEscapeAppend(buf, gpa, clipped);
        try buf.append(gpa, '"');
    }
    try buf.append(gpa, '}');
}

fn stringifyCensus(c: Census) Error![]u8 {
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(allocator);
    try buf.appendSlice(allocator, "{\"stretched_at\":\"");
    try jsonEscapeAppend(&buf, allocator, c.stretched_at);
    try buf.appendSlice(allocator, "\",\"os_name\":\"");
    try jsonEscapeAppend(&buf, allocator, c.os_name);
    try buf.appendSlice(allocator, "\",\"os_release\":\"");
    try jsonEscapeAppend(&buf, allocator, c.os_release);
    try buf.appendSlice(allocator, "\",\"arch\":\"");
    try jsonEscapeAppend(&buf, allocator, c.arch);
    try buf.appendSlice(allocator, "\",\"hostname\":\"");
    try jsonEscapeAppend(&buf, allocator, c.hostname);
    try buf.appendSlice(allocator, "\",\"cpu_count\":");
    try appendFmt(&buf, allocator, "{d}", .{c.cpu_count});
    try buf.appendSlice(allocator, ",\"ram_total_mb\":");
    try appendFmt(&buf, allocator, "{d}", .{c.ram_total_mb});
    try buf.appendSlice(allocator, ",\"ram_avail_mb\":");
    try appendFmt(&buf, allocator, "{d}", .{c.ram_avail_mb});
    try buf.appendSlice(allocator, ",\"gpu_name\":\"");
    try jsonEscapeAppend(&buf, allocator, c.gpu_name);
    try buf.appendSlice(allocator, "\",\"vram_total_mb\":");
    try appendFmt(&buf, allocator, "{d}", .{c.vram_total_mb});
    try buf.appendSlice(allocator, ",\"nvidia\":");
    try buf.appendSlice(allocator, if (c.nvidia) "true" else "false");
    try buf.appendSlice(allocator, ",\"gpu_vendor\":\"");
    try jsonEscapeAppend(&buf, allocator, c.gpu_vendor);
    try buf.appendSlice(allocator, "\",\"gpus\":[");
    for (c.gpus, 0..) |g, i| {
        if (i != 0) try buf.append(allocator, ',');
        try buf.appendSlice(allocator, "{\"name\":\"");
        try jsonEscapeAppend(&buf, allocator, g.name);
        try buf.appendSlice(allocator, "\",\"vendor\":\"");
        try jsonEscapeAppend(&buf, allocator, g.vendor);
        try buf.appendSlice(allocator, "\",\"vram_total_mb\":");
        try appendFmt(&buf, allocator, "{d}", .{g.vram_total_mb});
        try buf.appendSlice(allocator, ",\"dedicated\":");
        try buf.appendSlice(allocator, if (g.dedicated) "true" else "false");
        try buf.appendSlice(allocator, ",\"backend\":\"");
        try jsonEscapeAppend(&buf, allocator, g.backend);
        try buf.appendSlice(allocator, "\"}");
    }
    try buf.appendSlice(allocator, "],\"disk_home_free_gb\":");
    try appendFmt(&buf, allocator, "{d:.1}", .{c.disk_home_free_gb});
    try buf.appendSlice(allocator, ",\"tools\":");
    try appendKvObject(&buf, allocator, c.tools);
    try buf.appendSlice(allocator, ",\"missing\":[");
    for (c.missing, 0..) |m, i| {
        if (i != 0) try buf.append(allocator, ',');
        try buf.append(allocator, '"');
        try jsonEscapeAppend(&buf, allocator, m);
        try buf.append(allocator, '"');
    }
    try buf.appendSlice(allocator, "],\"rooms\":");
    try appendKvObject(&buf, allocator, c.rooms);
    try buf.appendSlice(allocator, ",\"work_rooms\":");
    try appendKvObject(&buf, allocator, c.work_rooms);
    try buf.appendSlice(allocator, ",\"doors\":{");
    for (c.doors, 0..) |d, i| {
        if (looksSecret(d.key)) continue;
        if (i != 0) try buf.append(allocator, ',');
        try buf.append(allocator, '"');
        try jsonEscapeAppend(&buf, allocator, d.key);
        try buf.appendSlice(allocator, "\":");
        try buf.appendSlice(allocator, if (d.open) "true" else "false");
    }
    try buf.appendSlice(allocator, "},\"host\":\"");
    try jsonEscapeAppend(&buf, allocator, c.host);
    try buf.appendSlice(allocator, "\"}");
    return try buf.toOwnedSlice(allocator);
}

fn probeCensus(arena: std.mem.Allocator, io: std.Io, home: []const u8) Error!Census {
    var tools_list: std.ArrayList(KV) = .empty;
    var missing_list: std.ArrayList([]const u8) = .empty;
    for (tool_names) |name| {
        if (try shell_ir.resolveWhich(arena, io, name)) |found| {
            if ((std.mem.eql(u8, name, "python") or std.mem.eql(u8, name, "py") or std.mem.eql(u8, name, "python3")) and
                !host_dialect.isUsableHostPython(found))
            {
                try missing_list.append(arena, name);
                continue;
            }
            try tools_list.append(arena, .{ .key = name, .value = found });
        } else {
            try missing_list.append(arena, name);
        }
    }
    var has_python = false;
    for (tools_list.items) |kv| {
        if (std.mem.eql(u8, kv.key, "python")) has_python = true;
    }
    if (!has_python) {
        for (tools_list.items) |kv| {
            if (std.mem.eql(u8, kv.key, "python3") and host_dialect.isUsableHostPython(kv.value)) {
                try tools_list.append(arena, .{ .key = "python", .value = kv.value });
                var filtered: std.ArrayList([]const u8) = .empty;
                for (missing_list.items) |m| {
                    if (!std.mem.eql(u8, m, "python")) try filtered.append(arena, m);
                }
                missing_list = filtered;
                break;
            }
        }
    }

    const ram = probeRamMb();
    const cpu: u32 = @intCast(std.Thread.getCpuCount() catch 0);
    const nvidia = (shell_ir.resolveWhich(arena, io, "nvidia-smi") catch null) != null;

    const profile = try userProfile(arena);
    var rooms_list: std.ArrayList(KV) = .empty;
    for (room_keys) |rk| {
        const p = if (rk[1].len == 0) profile else try std.fs.path.join(arena, &.{ profile, rk[1] });
        if (isDir(io, p)) try rooms_list.append(arena, .{ .key = rk[0], .value = p });
    }
    var work_list: std.ArrayList(KV) = .empty;
    for (work_hints) |parts| {
        const full = try std.fs.path.join(arena, &.{ profile, try std.fs.path.join(arena, parts) });
        if (isDir(io, full)) {
            var key_buf: std.ArrayList(u8) = .empty;
            for (parts, 0..) |part, i| {
                if (i != 0) try key_buf.append(arena, '/');
                try key_buf.appendSlice(arena, part);
            }
            try work_list.append(arena, .{ .key = try key_buf.toOwnedSlice(arena), .value = full });
        }
    }

    var doors_list: std.ArrayList(Door) = .empty;
    for (door_ports) |d| {
        try doors_list.append(arena, .{ .key = d[0], .open = portOpen(io, d[1]) });
    }

    var host_name: []const u8 = if (is_windows) "cmd" else "posix";
    if (host_dialect.probe(arena, io, home)) |dialect| {
        if (dialect.host.len != 0) host_name = dialect.host;
    } else |_| {}

    var stamp_buf: [32]u8 = undefined;
    const stamp = try arena.dupe(u8, utcNowStamp(io, &stamp_buf));

    var hostname: []const u8 = "";
    if (is_windows) {
        if (shell_ir.getEnvAlloc(arena, "COMPUTERNAME")) |n| {
            hostname = if (n.len > 80) n[0..80] else n;
        }
    } else if (comptime builtin.os.tag != .windows) {
        var hostname_buf: [std.posix.HOST_NAME_MAX]u8 = undefined;
        if (std.posix.gethostname(&hostname_buf)) |n| {
            hostname = try arena.dupe(u8, if (n.len > 80) n[0..80] else n);
        } else |_| {}
    }

    const os_pretty = if (is_windows) "Windows" else if (builtin.os.tag == .linux) "Linux" else if (builtin.os.tag == .macos) "Darwin" else @tagName(builtin.os.tag);

    return .{
        .stretched_at = stamp,
        .os_name = try arena.dupe(u8, os_pretty),
        .os_release = "",
        .arch = try arena.dupe(u8, @tagName(builtin.cpu.arch)),
        .hostname = hostname,
        .cpu_count = cpu,
        .ram_total_mb = ram.total,
        .ram_avail_mb = ram.avail,
        .gpu_name = "",
        .vram_total_mb = 0,
        .nvidia = nvidia,
        .gpu_vendor = if (nvidia) "nvidia" else "",
        .gpus = &[_]GpuEntry{},
        .disk_home_free_gb = 0.0,
        .tools = try tools_list.toOwnedSlice(arena),
        .missing = try missing_list.toOwnedSlice(arena),
        .rooms = try rooms_list.toOwnedSlice(arena),
        .work_rooms = try work_list.toOwnedSlice(arena),
        .doors = try doors_list.toOwnedSlice(arena),
        .host = host_name,
    };
}

fn jsonStr(obj: std.json.ObjectMap, key: []const u8) []const u8 {
    const v = obj.get(key) orelse return "";
    return switch (v) {
        .string => |s| s,
        else => "",
    };
}

fn jsonInt(obj: std.json.ObjectMap, key: []const u8) i64 {
    const v = obj.get(key) orelse return 0;
    return switch (v) {
        .integer => |i| i,
        .float => |f| @intFromFloat(f),
        else => 0,
    };
}

fn jsonBool(obj: std.json.ObjectMap, key: []const u8) bool {
    const v = obj.get(key) orelse return false;
    return switch (v) {
        .bool => |b| b,
        else => false,
    };
}

fn jsonFloat(obj: std.json.ObjectMap, key: []const u8) f64 {
    const v = obj.get(key) orelse return 0;
    return switch (v) {
        .float => |f| f,
        .integer => |i| @floatFromInt(i),
        else => 0,
    };
}

fn censusFromObject(arena: std.mem.Allocator, obj: std.json.ObjectMap) Error!Census {
    var tools: std.ArrayList(KV) = .empty;
    if (obj.get("tools")) |tv| {
        if (tv == .object) {
            var it = tv.object.iterator();
            while (it.next()) |e| {
                if (looksSecret(e.key_ptr.*) or (e.value_ptr.* == .string and looksSecret(e.value_ptr.string))) continue;
                if (e.value_ptr.* != .string) continue;
                const val = e.value_ptr.string;
                const clipped = if (val.len > 400) val[0..400] else val;
                try tools.append(arena, .{ .key = e.key_ptr.*, .value = clipped });
                if (tools.items.len >= 48) break;
            }
        }
    }
    var rooms: std.ArrayList(KV) = .empty;
    if (obj.get("rooms")) |tv| {
        if (tv == .object) {
            var it = tv.object.iterator();
            while (it.next()) |e| {
                if (looksSecret(e.key_ptr.*) or (e.value_ptr.* == .string and looksSecret(e.value_ptr.string))) continue;
                if (e.value_ptr.* != .string) continue;
                try rooms.append(arena, .{ .key = e.key_ptr.*, .value = e.value_ptr.string });
                if (rooms.items.len >= 48) break;
            }
        }
    }
    var work: std.ArrayList(KV) = .empty;
    if (obj.get("work_rooms")) |tv| {
        if (tv == .object) {
            var it = tv.object.iterator();
            while (it.next()) |e| {
                if (looksSecret(e.key_ptr.*)) continue;
                if (e.value_ptr.* != .string) continue;
                try work.append(arena, .{ .key = e.key_ptr.*, .value = e.value_ptr.string });
            }
        }
    }
    var doors_list: std.ArrayList(Door) = .empty;
    if (obj.get("doors")) |tv| {
        if (tv == .object) {
            var it = tv.object.iterator();
            while (it.next()) |e| {
                if (looksSecret(e.key_ptr.*)) continue;
                const open = switch (e.value_ptr.*) {
                    .bool => |b| b,
                    else => false,
                };
                try doors_list.append(arena, .{ .key = e.key_ptr.*, .open = open });
            }
        }
    }
    var missing: std.ArrayList([]const u8) = .empty;
    if (obj.get("missing")) |tv| {
        if (tv == .array) {
            for (tv.array.items) |item| {
                if (item != .string) continue;
                try missing.append(arena, item.string);
                if (missing.items.len >= 40) break;
            }
        }
    }
    var gpus: std.ArrayList(GpuEntry) = .empty;
    if (obj.get("gpus")) |tv| {
        if (tv == .array) {
            for (tv.array.items) |item| {
                if (item != .object) continue;
                const g = item.object;
                const name = jsonStr(g, "name");
                const vendor = jsonStr(g, "vendor");
                if (name.len == 0 and vendor.len == 0) continue;
                try gpus.append(arena, .{
                    .name = if (name.len > 80) name[0..80] else name,
                    .vendor = if (vendor.len > 16) vendor[0..16] else vendor,
                    .vram_total_mb = @intCast(@max(@as(i64, 0), jsonInt(g, "vram_total_mb"))),
                    .dedicated = if (g.get("dedicated") == null) true else jsonBool(g, "dedicated"),
                    .backend = jsonStr(g, "backend"),
                });
                if (gpus.items.len >= 8) break;
            }
        }
    }
    const host_name = jsonStr(obj, "host");
    return .{
        .stretched_at = jsonStr(obj, "stretched_at"),
        .os_name = jsonStr(obj, "os_name"),
        .os_release = jsonStr(obj, "os_release"),
        .arch = jsonStr(obj, "arch"),
        .hostname = jsonStr(obj, "hostname"),
        .cpu_count = @intCast(@max(@as(i64, 0), jsonInt(obj, "cpu_count"))),
        .ram_total_mb = @intCast(@max(@as(i64, 0), jsonInt(obj, "ram_total_mb"))),
        .ram_avail_mb = @intCast(@max(@as(i64, 0), jsonInt(obj, "ram_avail_mb"))),
        .gpu_name = jsonStr(obj, "gpu_name"),
        .vram_total_mb = @intCast(@max(@as(i64, 0), jsonInt(obj, "vram_total_mb"))),
        .nvidia = jsonBool(obj, "nvidia"),
        .gpu_vendor = jsonStr(obj, "gpu_vendor"),
        .gpus = try gpus.toOwnedSlice(arena),
        .disk_home_free_gb = jsonFloat(obj, "disk_home_free_gb"),
        .tools = try tools.toOwnedSlice(arena),
        .missing = try missing.toOwnedSlice(arena),
        .rooms = try rooms.toOwnedSlice(arena),
        .work_rooms = try work.toOwnedSlice(arena),
        .doors = try doors_list.toOwnedSlice(arena),
        .host = if (host_name.len != 0) host_name else (if (is_windows) "cmd" else "posix"),
    };
}

fn loadCensus(arena: std.mem.Allocator, io: std.Io, home: []const u8) Error!?Census {
    const path = try censusPath(arena, home);
    const raw = readFileAlloc(arena, io, path) catch return null;
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, raw, .{
        .allocate = .alloc_if_needed,
    }) catch return null;
    return switch (parsed) {
        .object => |o| try censusFromObject(arena, o),
        else => null,
    };
}

fn needsStretch(io: std.Io, c: ?Census, stale_days: i64) bool {
    const census = c orelse return true;
    if (census.stretched_at.len == 0) return true;
    const ts = parseIsoStamp(census.stretched_at) orelse return true;
    const age_days = @divTrunc(nowEpochSecs(io) - ts, 86400);
    return age_days >= @max(@as(i64, 1), stale_days);
}

fn formatHomeLine(arena: std.mem.Allocator, c: ?Census, home: []const u8, io: std.Io) Error![]u8 {
    if (c == null or c.?.stretched_at.len == 0) {
        const d = host_dialect.probe(arena, io, home) catch return try allocator.dupe(u8, "");
        var bits: std.ArrayList(u8) = .empty;
        errdefer bits.deinit(allocator);
        try bits.appendSlice(allocator, "Host bridge: ");
        try bits.appendSlice(allocator, if (d.host.len != 0) d.host else "cmd");
        if (d.python_cmd.len != 0) {
            try bits.appendSlice(allocator, " · python=");
            try bits.appendSlice(allocator, d.python_cmd);
        }
        try bits.appendSlice(allocator, " · prefer host_run(argv) / host_mkdir / host_script over quoted bash");
        return try bits.toOwnedSlice(allocator);
    }
    const census = c.?;
    var parts: std.ArrayList([]const u8) = .empty;
    try parts.append(arena, try std.fmt.allocPrint(arena, "This home: {s}", .{if (census.os_name.len != 0) census.os_name else "unknown"}));
    var gb_buf: [32]u8 = undefined;
    if (census.ram_total_mb != 0) {
        try parts.append(arena, try std.fmt.allocPrint(arena, "{s} RAM", .{gbLabel(census.ram_total_mb, &gb_buf)}));
    }
    if (census.gpu_name.len != 0) {
        if (census.vram_total_mb != 0) {
            var vbuf: [32]u8 = undefined;
            try parts.append(arena, try std.fmt.allocPrint(arena, "{s} {s}", .{ census.gpu_name, gbLabel(census.vram_total_mb, &vbuf) }));
        } else {
            try parts.append(arena, census.gpu_name);
        }
    }
    if (census.cpu_count != 0) {
        try parts.append(arena, try std.fmt.allocPrint(arena, "{d} CPU", .{census.cpu_count}));
    }
    const prefer = [_][]const u8{ "python", "git", "uv", "node", "rg", "pwsh", "cargo" };
    var present: std.ArrayList([]const u8) = .empty;
    for (prefer) |name| {
        for (census.tools) |kv| {
            if (std.mem.eql(u8, kv.key, name)) {
                try present.append(arena, name);
                break;
            }
        }
    }
    if (present.items.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        for (present.items, 0..) |n, i| {
            if (i != 0) try joined.append(arena, ' ');
            try joined.appendSlice(arena, n);
        }
        try parts.append(arena, try joined.toOwnedSlice(arena));
    }
    var open_doors: std.ArrayList([]const u8) = .empty;
    for (census.doors) |d| {
        if (d.open) try open_doors.append(arena, d.key);
    }
    if (open_doors.items.len != 0) {
        var dj: std.ArrayList(u8) = .empty;
        try dj.appendSlice(arena, "doors: ");
        for (open_doors.items, 0..) |n, i| {
            if (i != 0) try dj.append(arena, ',');
            try dj.appendSlice(arena, n);
        }
        try parts.append(arena, try dj.toOwnedSlice(arena));
    }
    try parts.append(arena, try std.fmt.allocPrint(arena, "host={s}", .{if (census.host.len != 0) census.host else "cmd"}));
    try parts.append(arena, "prefer host_run / host_mkdir / host_script");

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(allocator);
    for (parts.items, 0..) |p, i| {
        if (i != 0) try out.appendSlice(allocator, " · ");
        try out.appendSlice(allocator, p);
    }
    return try out.toOwnedSlice(allocator);
}

fn formatWhoami(arena: std.mem.Allocator, c: ?Census) Error![]u8 {
    if (c == null or c.?.stretched_at.len == 0) {
        return try allocator.dupe(u8, "_This home has not been stretched yet. `/stretch` maps hardware and tools._");
    }
    const census = c.?;
    var lines: std.ArrayList([]const u8) = .empty;
    const stamp = if (census.stretched_at.len >= 16) blk: {
        var b: [16]u8 = undefined;
        @memcpy(b[0..10], census.stretched_at[0..10]);
        b[10] = ' ';
        @memcpy(b[11..16], census.stretched_at[11..16]);
        break :blk try arena.dupe(u8, b[0..16]);
    } else census.stretched_at;
    try lines.append(arena, try std.fmt.allocPrint(arena, "**This home** (stretched {s} UTC)", .{stamp}));

    var hw: std.ArrayList([]const u8) = .empty;
    if (census.os_name.len != 0) {
        if (census.os_release.len != 0)
            try hw.append(arena, try std.fmt.allocPrint(arena, "{s} {s}", .{ census.os_name, census.os_release }))
        else
            try hw.append(arena, census.os_name);
    }
    if (census.arch.len != 0) try hw.append(arena, census.arch);
    if (census.cpu_count != 0) try hw.append(arena, try std.fmt.allocPrint(arena, "{d} CPU", .{census.cpu_count}));
    var gb_buf: [32]u8 = undefined;
    if (census.ram_total_mb != 0) try hw.append(arena, try std.fmt.allocPrint(arena, "{s} RAM", .{gbLabel(census.ram_total_mb, &gb_buf)}));
    if (census.gpu_name.len != 0) {
        if (census.vram_total_mb != 0) {
            var vbuf: [32]u8 = undefined;
            try hw.append(arena, try std.fmt.allocPrint(arena, "{s} ({s})", .{ census.gpu_name, gbLabel(census.vram_total_mb, &vbuf) }));
        } else try hw.append(arena, census.gpu_name);
    }
    if (hw.items.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        try joined.appendSlice(arena, "- **Hardware:** ");
        for (hw.items, 0..) |h, i| {
            if (i != 0) try joined.appendSlice(arena, " · ");
            try joined.appendSlice(arena, h);
        }
        try lines.append(arena, try joined.toOwnedSlice(arena));
    }
    if (census.tools.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        try joined.appendSlice(arena, "- **Tools:** ");
        const limit = @min(census.tools.len, 16);
        for (census.tools[0..limit], 0..) |kv, i| {
            if (i != 0) try joined.appendSlice(arena, ", ");
            try joined.appendSlice(arena, kv.key);
        }
        try lines.append(arena, try joined.toOwnedSlice(arena));
    }
    if (census.missing.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        try joined.appendSlice(arena, "- **Not on PATH:** ");
        const limit = @min(census.missing.len, 12);
        for (census.missing[0..limit], 0..) |m, i| {
            if (i != 0) try joined.appendSlice(arena, ", ");
            try joined.appendSlice(arena, m);
        }
        try lines.append(arena, try joined.toOwnedSlice(arena));
    }
    if (census.rooms.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        try joined.appendSlice(arena, "- **Rooms:** ");
        for (census.rooms, 0..) |kv, i| {
            if (i != 0) try joined.appendSlice(arena, ", ");
            try joined.appendSlice(arena, kv.key);
        }
        try lines.append(arena, try joined.toOwnedSlice(arena));
    }
    if (census.work_rooms.len != 0) {
        var joined: std.ArrayList(u8) = .empty;
        try joined.appendSlice(arena, "- **Work rooms:** ");
        for (census.work_rooms, 0..) |kv, i| {
            if (i != 0) try joined.appendSlice(arena, ", ");
            try joined.appendSlice(arena, kv.key);
        }
        try lines.append(arena, try joined.toOwnedSlice(arena));
    }
    if (census.doors.len != 0) {
        var open_d: std.ArrayList([]const u8) = .empty;
        var shut: std.ArrayList([]const u8) = .empty;
        for (census.doors) |d| {
            if (d.open) try open_d.append(arena, d.key) else try shut.append(arena, d.key);
        }
        var door_s: std.ArrayList(u8) = .empty;
        try door_s.appendSlice(arena, "- **Doors:** ");
        var first = true;
        if (open_d.items.len != 0) {
            try door_s.appendSlice(arena, "open ");
            for (open_d.items, 0..) |n, i| {
                if (i != 0) try door_s.appendSlice(arena, ", ");
                try door_s.appendSlice(arena, n);
            }
            first = false;
        }
        if (shut.items.len != 0) {
            if (!first) try door_s.appendSlice(arena, " · ");
            try door_s.appendSlice(arena, "quiet ");
            for (shut.items, 0..) |n, i| {
                if (i != 0) try door_s.appendSlice(arena, ", ");
                try door_s.appendSlice(arena, n);
            }
        }
        try lines.append(arena, try door_s.toOwnedSlice(arena));
    }
    try lines.append(arena, "_Re-stretch anytime with_ `/stretch` _after you install tools. No disk crawl — PATH, hardware, and a few local ports only._");

    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(allocator);
    for (lines.items, 0..) |line, i| {
        if (i != 0) try out.append(allocator, '\n');
        try out.appendSlice(allocator, line);
    }
    return try out.toOwnedSlice(allocator);
}

fn stretchToOwned(home_in: []const u8, force: bool) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    if (!force) {
        if (try loadCensus(arena, io, home)) |existing| {
            if (!needsStretch(io, existing, stale_days_default)) {
                return try stringifyCensus(existing);
            }
        }
    }
    const census = try probeCensus(arena, io, home);
    const json = try stringifyCensus(census);
    const path = try censusPath(arena, home);
    try writeJsonAtomic(io, path, json);
    const dialect = try host_dialect.probe(arena, io, home);
    const djson = try host.jsonAlloc(.{
        .host = dialect.host,
        .python_cmd = dialect.python_cmd,
        .git_cmd = dialect.git_cmd,
        .rg_cmd = dialect.rg_cmd,
        .curl_kind = dialect.curl_kind,
        .pwsh_cmd = dialect.pwsh_cmd,
        .last_good_verify = dialect.last_good_verify,
        .successes = dialect.successes,
        .last_success_at = dialect.last_success_at,
        .notes = dialect.notes,
    });
    defer allocator.free(djson);
    const dpath = try std.fs.path.join(arena, &.{ home, "host", "dialect.json" });
    writeJsonAtomic(io, dpath, djson) catch {};
    return json;
}

fn loadToOwned(home_in: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    if (try loadCensus(arena, io, home)) |census| return try stringifyCensus(census);
    return try allocator.dupe(u8, "null");
}

fn needsToOwned(home_in: []const u8, stale_days: i64) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    const c = try loadCensus(arena, io, home);
    return try allocator.dupe(u8, if (needsStretch(io, c, stale_days)) "true" else "false");
}

fn formatLineToOwned(home_in: []const u8, census_json: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    const c: ?Census = if (census_json.len != 0 and !std.mem.eql(u8, census_json, "null")) blk: {
        const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, census_json, .{ .allocate = .alloc_if_needed }) catch break :blk null;
        break :blk switch (parsed) {
            .object => |o| try censusFromObject(arena, o),
            else => null,
        };
    } else try loadCensus(arena, io, home);
    return try formatHomeLine(arena, c, home, io);
}

fn formatWhoamiToOwned(home_in: []const u8, census_json: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const home = try resolveHome(arena, home_in);
    const c: ?Census = if (census_json.len != 0 and !std.mem.eql(u8, census_json, "null")) blk: {
        const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, census_json, .{ .allocate = .alloc_if_needed }) catch break :blk null;
        break :blk switch (parsed) {
            .object => |o| try censusFromObject(arena, o),
            else => null,
        };
    } else try loadCensus(arena, io, home);
    return try formatWhoami(arena, c);
}

export fn remedy_core_stretch_home(
    home_ptr: ?[*]const u8,
    home_len: usize,
    force: u8,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(stretchToOwned(slice(home_ptr, home_len), force != 0), out_json, out_len);
}

export fn remedy_core_stretch_load(
    home_ptr: ?[*]const u8,
    home_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(loadToOwned(slice(home_ptr, home_len)), out_json, out_len);
}

export fn remedy_core_stretch_needs(
    home_ptr: ?[*]const u8,
    home_len: usize,
    stale_days: i32,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const days: i64 = if (stale_days > 0) stale_days else stale_days_default;
    return deliverBytes(needsToOwned(slice(home_ptr, home_len), days), out_json, out_len);
}

export fn remedy_core_stretch_format_line(
    home_ptr: ?[*]const u8,
    home_len: usize,
    census_json_ptr: ?[*]const u8,
    census_json_len: usize,
    out_utf8: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(
        formatLineToOwned(slice(home_ptr, home_len), slice(census_json_ptr, census_json_len)),
        out_utf8,
        out_len,
    );
}

export fn remedy_core_stretch_format_whoami(
    home_ptr: ?[*]const u8,
    home_len: usize,
    census_json_ptr: ?[*]const u8,
    census_json_len: usize,
    out_utf8: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    return deliverBytes(
        formatWhoamiToOwned(slice(home_ptr, home_len), slice(census_json_ptr, census_json_len)),
        out_utf8,
        out_len,
    );
}

test "civil days epoch" {
    try std.testing.expectEqual(@as(i64, 0), civilDays(1970, 1, 1) - civilDays(1970, 1, 1));
    try std.testing.expect(parseIsoStamp("2026-01-01T00:00:00Z") != null);
}

test "stretch secrets stripped on load" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();
    const raw =
        \\{"stretched_at":"2026-01-01T00:00:00Z","tools":{"python":"/usr/bin/python","api_key":"sk-secret"},"rooms":{"desktop":"C:/ok","auth_token":"nope"},"doors":{},"missing":[],"work_rooms":{},"gpus":[],"host":"cmd"}
    ;
    const parsed = try std.json.parseFromSliceLeaky(std.json.Value, arena, raw, .{ .allocate = .alloc_if_needed });
    const c = try censusFromObject(arena, parsed.object);
    var has_python = false;
    for (c.tools) |kv| {
        try std.testing.expect(!std.mem.eql(u8, kv.key, "api_key"));
        if (std.mem.eql(u8, kv.key, "python")) has_python = true;
    }
    try std.testing.expect(has_python);
    for (c.rooms) |kv| {
        try std.testing.expect(!std.mem.eql(u8, kv.key, "auth_token"));
    }
}
