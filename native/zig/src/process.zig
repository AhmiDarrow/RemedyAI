const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");

pub const max_arguments = 256;
pub const max_argument_bytes = 64 * 1024;
pub const max_output_bytes = 1024 * 1024;

pub fn validateArguments(argv: []const []const u8) error{InvalidArguments}!void {
    if (argv.len == 0 or argv.len > max_arguments or argv[0].len == 0) {
        return error.InvalidArguments;
    }
    // Never let the process launcher perform a PATH/current-directory lookup
    // after policy has approved a textual executable name. Callers retain the
    // same tools by resolving them once into their trusted absolute identity.
    if (!std.fs.path.isAbsolute(argv[0])) return error.InvalidArguments;
    var total: usize = 0;
    for (argv) |argument| {
        if (std.mem.indexOfScalar(u8, argument, 0) != null) return error.InvalidArguments;
        total = std.math.add(usize, total, argument.len) catch return error.InvalidArguments;
        if (total > max_argument_bytes) return error.InvalidArguments;
    }
}

/// Low-level capture primitive. The Phase 3 policy layer supplies the approved
/// executable and arguments; this function still refuses use without a spawn right.
pub fn runCapture(
    allocator: std.mem.Allocator,
    io: std.Io,
    capabilities: capability.Set,
    argv: []const []const u8,
    timeout: std.Io.Timeout,
) !std.process.RunResult {
    try capabilities.require(.process_spawn);
    try validateArguments(argv);
    return std.process.run(allocator, io, .{
        .argv = argv,
        .stdout_limit = .limited(max_output_bytes),
        .stderr_limit = .limited(max_output_bytes),
        .timeout = timeout,
    });
}

pub const SoftResult = struct {
    exit_code: u32,
    timed_out: bool,
    stdout: []u8,
    stderr: []u8,
};

/// Like `runCapture`, but on wall-clock timeout keeps stdout/stderr (exit 124)
/// so callers such as `tailscale up` can recover a printed login URL.
pub fn runCaptureSoft(
    allocator: std.mem.Allocator,
    io: std.Io,
    capabilities: capability.Set,
    argv: []const []const u8,
    timeout: std.Io.Timeout,
) !SoftResult {
    try capabilities.require(.process_spawn);
    try validateArguments(argv);

    var child = try std.process.spawn(io, .{
        .argv = argv,
        .stdin = .ignore,
        .stdout = .pipe,
        .stderr = .pipe,
        .create_no_window = true,
    });
    defer child.kill(io);

    var multi_reader_buffer: std.Io.File.MultiReader.Buffer(2) = undefined;
    var multi_reader: std.Io.File.MultiReader = undefined;
    multi_reader.init(allocator, io, multi_reader_buffer.toStreams(), &.{ child.stdout.?, child.stderr.? });
    defer multi_reader.deinit();

    const stdout_reader = multi_reader.reader(0);
    const stderr_reader = multi_reader.reader(1);

    var timed_out = false;
    while (multi_reader.fill(64, timeout)) |_| {
        if (stdout_reader.buffered().len > max_output_bytes) return error.StreamTooLong;
        if (stderr_reader.buffered().len > max_output_bytes) return error.StreamTooLong;
    } else |err| switch (err) {
        error.EndOfStream => {},
        error.Timeout => timed_out = true,
        else => |e| return e,
    }

    try multi_reader.checkAnyError();

    const stdout_slice = try multi_reader.toOwnedSlice(0);
    errdefer allocator.free(stdout_slice);
    const stderr_slice = try multi_reader.toOwnedSlice(1);
    errdefer allocator.free(stderr_slice);

    if (timed_out) {
        return .{
            .exit_code = 124,
            .timed_out = true,
            .stdout = stdout_slice,
            .stderr = stderr_slice,
        };
    }

    const term = try child.wait(io);
    const code: u32 = switch (term) {
        .exited => |c| c,
        .unknown => |u| u,
        else => 1,
    };
    return .{
        .exit_code = code,
        .timed_out = false,
        .stdout = stdout_slice,
        .stderr = stderr_slice,
    };
}

test "process primitive rejects missing rights before spawning" {
    const argv = [_][]const u8{"never-runs"};
    try std.testing.expectError(
        error.AccessDenied,
        runCapture(std.testing.allocator, std.testing.io, .{}, &argv, .none),
    );
}

test "process arguments reject malformed families" {
    const trusted_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\tool.exe" else "/opt/remedy/tool";
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{}));
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{""}));
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{ "tool", "--safe" }));
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{ trusted_tool, "bad\x00arg" }));
    try validateArguments(&.{ trusted_tool, "--safe", "value" });
}

test "process primitive captures bounded output" {
    const argv = switch (builtin.os.tag) {
        .windows => &[_][]const u8{ "C:\\Windows\\System32\\cmd.exe", "/d", "/c", "echo remedy" },
        else => &[_][]const u8{ "/bin/sh", "-c", "printf remedy" },
    };
    const result = try runCapture(
        std.testing.allocator,
        std.testing.io,
        capability.Set.one(.process_spawn),
        argv,
        .none,
    );
    defer std.testing.allocator.free(result.stdout);
    defer std.testing.allocator.free(result.stderr);

    try std.testing.expectEqual(@as(u8, 0), result.term.exited);
    try std.testing.expect(std.mem.startsWith(u8, result.stdout, "remedy"));
    try std.testing.expectEqual(@as(usize, 0), result.stderr.len);
}
