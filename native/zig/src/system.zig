const std = @import("std");
const capability = @import("capability.zig");

pub const Snapshot = struct {
    logical_cpu_count: usize,
};

pub fn snapshot(capabilities: capability.Set) !Snapshot {
    try capabilities.require(.system_read);
    return .{ .logical_cpu_count = try std.Thread.getCpuCount() };
}

test "system inspection is capability gated" {
    try std.testing.expectError(error.AccessDenied, snapshot(.{}));
    const result = try snapshot(capability.Set.one(.system_read));
    try std.testing.expect(result.logical_cpu_count > 0);
}
