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

test "process primitive rejects missing rights before spawning" {
    const argv = [_][]const u8{"never-runs"};
    try std.testing.expectError(
        error.AccessDenied,
        runCapture(std.testing.allocator, std.testing.io, .{}, &argv, .none),
    );
}

test "process arguments reject malformed families" {
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{}));
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{""}));
    try std.testing.expectError(error.InvalidArguments, validateArguments(&.{ "tool", "bad\x00arg" }));
    try validateArguments(&.{ "tool", "--safe", "value" });
}

test "process primitive captures bounded output" {
    const argv = switch (builtin.os.tag) {
        .windows => &[_][]const u8{ "cmd.exe", "/d", "/c", "echo remedy" },
        else => &[_][]const u8{ "sh", "-c", "printf remedy" },
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
