//! Connect Tailscale opt-in: status / login / launch MSI installer.
//!
//! Spawns only the discovered Tailscale CLI (fixed argv shapes) or
//! `msiexec /i <absolute.msi>`. Fail-closed: no PATH lookup after resolve,
//! no soft fallback to unsigned helpers outside this allowlist.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const capability = @import("capability.zig");
const process = @import("process.zig");
const shell_ir = @import("shell_ir.zig");

const is_windows = builtin.os.tag == .windows;
const windows_host = if (is_windows) @import("host_windows.zig") else struct {};

const Status = root.Status;
const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const failed_status: i32 = @intFromEnum(Status.operation_failed);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

const known_install_dirs = [_][]const u8{
    "C:\\Program Files\\Tailscale",
    "C:\\Program Files (x86)\\Tailscale",
};

const msiexec_path = "C:\\Windows\\System32\\msiexec.exe";

fn deliverOwned(bytes: []u8, out_ptr: ?*?[*]u8, out_len: ?*usize) i32 {
    const ptr_slot = out_ptr orelse {
        host.allocator.free(bytes);
        return invalid_status;
    };
    const len_slot = out_len orelse {
        host.allocator.free(bytes);
        return invalid_status;
    };
    ptr_slot.* = bytes.ptr;
    len_slot.* = bytes.len;
    return ok_status;
}

fn pathExists(io: std.Io, path: []const u8) bool {
    if (path.len == 0) return false;
    std.Io.Dir.accessAbsolute(io, path, .{}) catch return false;
    return true;
}

fn jsonEscapeAppend(buf: *std.ArrayList(u8), gpa: std.mem.Allocator, text: []const u8) !void {
    for (text) |c| {
        switch (c) {
            '"' => try buf.appendSlice(gpa, "\\\""),
            '\\' => try buf.appendSlice(gpa, "\\\\"),
            '\n' => try buf.appendSlice(gpa, "\\n"),
            '\r' => try buf.appendSlice(gpa, "\\r"),
            '\t' => try buf.appendSlice(gpa, "\\t"),
            else => {
                if (c < 0x20) {
                    var hex: [6]u8 = undefined;
                    const slice = try std.fmt.bufPrint(&hex, "\\u{x:0>4}", .{c});
                    try buf.appendSlice(gpa, slice);
                } else {
                    try buf.append(gpa, c);
                }
            },
        }
    }
}

fn statusJson(
    gpa: std.mem.Allocator,
    installed: bool,
    running: bool,
    logged_in: bool,
    tailnet_ipv4: []const u8,
    version: []const u8,
    error_text: []const u8,
) ![]u8 {
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(gpa);
    try buf.appendSlice(gpa, "{\"installed\":");
    try buf.appendSlice(gpa, if (installed) "true" else "false");
    try buf.appendSlice(gpa, ",\"running\":");
    try buf.appendSlice(gpa, if (running) "true" else "false");
    try buf.appendSlice(gpa, ",\"logged_in\":");
    try buf.appendSlice(gpa, if (logged_in) "true" else "false");
    try buf.appendSlice(gpa, ",\"tailnet_ipv4\":\"");
    try jsonEscapeAppend(&buf, gpa, tailnet_ipv4);
    try buf.appendSlice(gpa, "\",\"version\":\"");
    try jsonEscapeAppend(&buf, gpa, version);
    try buf.appendSlice(gpa, "\",\"error\":\"");
    try jsonEscapeAppend(&buf, gpa, error_text);
    try buf.appendSlice(gpa, "\"}");
    return try buf.toOwnedSlice(gpa);
}

fn actionJson(
    gpa: std.mem.Allocator,
    status_text: []const u8,
    message: []const u8,
    login_url: []const u8,
    msi_path: []const u8,
    installer_url: []const u8,
) ![]u8 {
    var buf: std.ArrayList(u8) = .empty;
    errdefer buf.deinit(gpa);
    try buf.appendSlice(gpa, "{\"status\":\"");
    try jsonEscapeAppend(&buf, gpa, status_text);
    try buf.appendSlice(gpa, "\",\"message\":\"");
    try jsonEscapeAppend(&buf, gpa, message);
    try buf.appendSlice(gpa, "\",\"login_url\":\"");
    try jsonEscapeAppend(&buf, gpa, login_url);
    try buf.appendSlice(gpa, "\",\"msi_path\":\"");
    try jsonEscapeAppend(&buf, gpa, msi_path);
    try buf.appendSlice(gpa, "\",\"installer_url\":\"");
    try jsonEscapeAppend(&buf, gpa, installer_url);
    try buf.appendSlice(gpa, "\"}");
    return try buf.toOwnedSlice(gpa);
}

fn findCli(gpa: std.mem.Allocator, io: std.Io) !?[]u8 {
    var arena = std.heap.ArenaAllocator.init(gpa);
    defer arena.deinit();
    const a = arena.allocator();
    const basename = if (is_windows) "tailscale.exe" else "tailscale";
    if (try shell_ir.resolveWhich(a, io, basename)) |found| {
        return try gpa.dupe(u8, found);
    }
    if (is_windows) {
        for (known_install_dirs) |dir| {
            const candidate = try std.fs.path.join(a, &.{ dir, basename });
            if (pathExists(io, candidate)) return try gpa.dupe(u8, candidate);
        }
    }
    return null;
}

fn basenameOf(path: []const u8) []const u8 {
    return std.fs.path.basename(path);
}

fn eqlIgnoreCase(a: []const u8, b: []const u8) bool {
    if (a.len != b.len) return false;
    for (a, b) |x, y| {
        const xl = if (x >= 'A' and x <= 'Z') x + 32 else x;
        const yl = if (y >= 'A' and y <= 'Z') y + 32 else y;
        if (xl != yl) return false;
    }
    return true;
}

fn assertTailscaleCli(path: []const u8) error{AccessDenied}!void {
    const base = basenameOf(path);
    const expect = if (is_windows) "tailscale.exe" else "tailscale";
    if (!eqlIgnoreCase(base, expect)) return error.AccessDenied;
}

fn timeoutMs(ms: u64) std.Io.Timeout {
    return .{ .duration = .{
        .raw = std.Io.Duration.fromMilliseconds(@intCast(ms)),
        .clock = .awake,
    } };
}

fn runCli(
    gpa: std.mem.Allocator,
    io: std.Io,
    cli: []const u8,
    args: []const []const u8,
    timeout_ms: u64,
) !process.SoftResult {
    try assertTailscaleCli(cli);
    var argv_buf: [8][]const u8 = undefined;
    if (args.len + 1 > argv_buf.len) return error.InvalidArguments;
    argv_buf[0] = cli;
    for (args, 0..) |a, i| argv_buf[i + 1] = a;
    return process.runCaptureSoft(
        gpa,
        io,
        capability.Set.one(.process_spawn),
        .{
            .argv = argv_buf[0 .. args.len + 1],
            .timeout = timeoutMs(timeout_ms),
        },
    );
}

fn combinedOut(gpa: std.mem.Allocator, soft: process.SoftResult) ![]u8 {
    defer gpa.free(soft.stdout);
    defer gpa.free(soft.stderr);
    if (soft.stderr.len == 0) return try gpa.dupe(u8, soft.stdout);
    if (soft.stdout.len == 0) return try gpa.dupe(u8, soft.stderr);
    return try std.mem.concat(gpa, u8, &.{ soft.stdout, soft.stderr });
}

fn firstLine(text: []const u8) []const u8 {
    if (std.mem.indexOfScalar(u8, text, '\n')) |i| {
        var line = text[0..i];
        if (line.len > 0 and line[line.len - 1] == '\r') line = line[0 .. line.len - 1];
        return std.mem.trim(u8, line, " \t");
    }
    return std.mem.trim(u8, text, " \t\r\n");
}

fn extractTailnetIpv4(text: []const u8) []const u8 {
    var it = std.mem.splitScalar(u8, text, '\n');
    while (it.next()) |raw| {
        const line = std.mem.trim(u8, raw, " \t\r");
        if (std.mem.startsWith(u8, line, "100.")) return line;
    }
    return "";
}

fn extractLoginUrl(text: []const u8) []const u8 {
    const needle = "https://login.tailscale.com/";
    const start = std.mem.indexOf(u8, text, needle) orelse return "";
    var end = start;
    while (end < text.len) : (end += 1) {
        const c = text[end];
        if (c == ' ' or c == '\t' or c == '\n' or c == '\r' or c == '"' or c == '\'') break;
    }
    var url = text[start..end];
    while (url.len > 0) {
        const last = url[url.len - 1];
        if (last == '.' or last == ',' or last == ';' or last == ')' or last == ']') {
            url = url[0 .. url.len - 1];
            continue;
        }
        break;
    }
    // Reject bare prefix without a node token path.
    if (url.len <= needle.len) return "";
    return url;
}

fn buildStatusJson(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    const cli = (try findCli(gpa, io)) orelse {
        return statusJson(
            gpa,
            false,
            false,
            false,
            "",
            "",
            "Tailscale is not installed. Opt in to download it automatically.",
        );
    };
    defer gpa.free(cli);

    const status_soft = runCli(gpa, io, cli, &.{"status"}, 6000) catch {
        return statusJson(
            gpa,
            true,
            false,
            false,
            "",
            "",
            "Tailscale is installed but not running. Start it, then sign in.",
        );
    };
    const status_out = try combinedOut(gpa, status_soft);
    defer gpa.free(status_out);
    const lower = try std.ascii.allocLowerString(gpa, status_out);
    defer gpa.free(lower);
    const running = status_soft.exit_code == 0 or std.mem.indexOf(u8, lower, "logged out") != null;
    if (!running) {
        return statusJson(
            gpa,
            true,
            false,
            false,
            "",
            "",
            "Tailscale is installed but not running. Start it, then sign in.",
        );
    }

    var version: []const u8 = "";
    var version_owned: ?[]u8 = null;
    defer if (version_owned) |v| gpa.free(v);
    if (runCli(gpa, io, cli, &.{"version"}, 6000)) |ver_soft| {
        const ver_out = try combinedOut(gpa, ver_soft);
        if (ver_soft.exit_code == 0) {
            version_owned = try gpa.dupe(u8, firstLine(ver_out));
            version = version_owned.?;
        }
        gpa.free(ver_out);
    } else |_| {}

    var ts_ip: []const u8 = "";
    var ip_owned: ?[]u8 = null;
    defer if (ip_owned) |v| gpa.free(v);
    if (runCli(gpa, io, cli, &.{ "ip", "-4" }, 6000)) |ip_soft| {
        const ip_out = try combinedOut(gpa, ip_soft);
        if (ip_soft.exit_code == 0) {
            const found = extractTailnetIpv4(ip_out);
            if (found.len > 0) {
                ip_owned = try gpa.dupe(u8, found);
                ts_ip = ip_owned.?;
            }
        }
        gpa.free(ip_out);
    } else |_| {}

    const logged_in = ts_ip.len > 0;
    const err_text: []const u8 = if (!logged_in)
        "Tailscale is running but not logged in. Sign in to get a tailnet address."
    else
        "";
    return statusJson(gpa, true, true, logged_in, ts_ip, version, err_text);
}

fn buildLoginJson(gpa: std.mem.Allocator, io: std.Io) ![]u8 {
    const cli = (try findCli(gpa, io)) orelse {
        return actionJson(gpa, "error", "Tailscale is not installed yet.", "", "", "");
    };
    defer gpa.free(cli);

    const soft = runCli(gpa, io, cli, &.{"up"}, 10000) catch {
        return actionJson(
            gpa,
            "needs_login",
            "Start the Tailscale app and sign in — use the same account as your phone.",
            "",
            "",
            "",
        );
    };
    const out = try combinedOut(gpa, soft);
    defer gpa.free(out);
    const url = extractLoginUrl(out);
    if (soft.exit_code == 0 and !soft.timed_out) {
        return actionJson(gpa, "ok", "Tailscale is already signed in.", "", "", "");
    }
    if (url.len > 0) {
        return actionJson(
            gpa,
            "needs_login",
            "Open the sign-in link to connect this PC to your tailnet.",
            url,
            "",
            "",
        );
    }
    return actionJson(
        gpa,
        "needs_login",
        "Start the Tailscale app and sign in — use the same account as your phone.",
        "",
        "",
        "",
    );
}

fn validateMsiPath(path: []const u8) error{InvalidArgument}!void {
    if (path.len == 0 or !std.fs.path.isAbsolute(path)) return error.InvalidArgument;
    if (std.mem.indexOfScalar(u8, path, 0) != null) return error.InvalidArgument;
    if (!eqlIgnoreCase(std.fs.path.extension(path), ".msi")) return error.InvalidArgument;
}

/// Snapshot Tailscale install/run/login state as UTF-8 JSON.
export fn remedy_core_tailscale_status(out_json: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const bytes = buildStatusJson(host.allocator, io) catch return failed_status;
    return deliverOwned(bytes, out_json, out_len);
}

/// Start `tailscale up` and return UTF-8 JSON {status,message,login_url,...}.
export fn remedy_core_tailscale_login(out_json: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    const bytes = buildLoginJson(host.allocator, io) catch return failed_status;
    return deliverOwned(bytes, out_json, out_len);
}

/// Launch `msiexec /i <absolute.msi>` hidden (UAC prompt). Windows only.
export fn remedy_core_tailscale_launch_msi(
    msi_path: ?[*]const u8,
    msi_len: usize,
    out_pid: ?*u32,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    pid_slot.* = 0;
    const raw = msi_path orelse return invalid_status;
    const path = raw[0..msi_len];
    validateMsiPath(path) catch return invalid_status;

    var threaded: std.Io.Threaded = .init_single_threaded;
    const io = threaded.io();
    if (!pathExists(io, path)) return invalid_status;
    if (!pathExists(io, msiexec_path)) return failed_status;

    var argv_json_buf: std.ArrayList(u8) = .empty;
    defer argv_json_buf.deinit(host.allocator);
    argv_json_buf.appendSlice(host.allocator, "[\"") catch return failed_status;
    // msiexec path has no JSON-special chars.
    argv_json_buf.appendSlice(host.allocator, msiexec_path) catch return failed_status;
    argv_json_buf.appendSlice(host.allocator, "\",\"/i\",\"") catch return failed_status;
    jsonEscapeAppend(&argv_json_buf, host.allocator, path) catch return failed_status;
    argv_json_buf.appendSlice(host.allocator, "\"]") catch return failed_status;

    const pid = windows_host.spawnDetached(argv_json_buf.items, "", "") catch return failed_status;
    pid_slot.* = pid;
    return ok_status;
}

test "extract login url rejects bare prefix" {
    try std.testing.expectEqualStrings("", extractLoginUrl("visit https://login.tailscale.com/"));
    try std.testing.expectEqualStrings(
        "https://login.tailscale.com/a/abc123",
        extractLoginUrl("To authenticate, visit:\n\thttps://login.tailscale.com/a/abc123\n"),
    );
}

test "extract tailnet ipv4" {
    try std.testing.expectEqualStrings("100.101.102.103", extractTailnetIpv4("100.101.102.103\n"));
    try std.testing.expectEqualStrings("", extractTailnetIpv4("10.0.0.1\n"));
}

test "msi path policy" {
    try std.testing.expectError(error.InvalidArgument, validateMsiPath("relative.msi"));
    try std.testing.expectError(error.InvalidArgument, validateMsiPath("C:\\Temp\\setup.exe"));
    if (is_windows) {
        try validateMsiPath("C:\\Temp\\tailscale-setup.msi");
    }
}
