const std = @import("std");

pub const capability = @import("capability.zig");
pub const filesystem = @import("filesystem.zig");
pub const process = @import("process.zig");
pub const serialization = @import("serialization.zig");
pub const system = @import("system.zig");

pub const abi_version: u32 = 1;
pub const header_size: usize = 32;
pub const max_payload_size: u32 = 16 << 20;

pub const Status = enum(i32) {
    ok = 0,
    invalid_argument = 1,
    access_denied = 2,
    operation_failed = 3,
};

export fn remedy_core_abi_version() callconv(.c) u32 {
    return abi_version;
}

export fn remedy_core_validate_frame(ptr: ?[*]const u8, len: usize) callconv(.c) u8 {
    const raw_ptr = ptr orelse return 0;
    return if (validateFrame(raw_ptr[0..len])) 1 else 0;
}

export fn remedy_core_file_size(
    capability_bits: u64,
    root_ptr: ?[*]const u8,
    root_len: usize,
    path_ptr: ?[*]const u8,
    path_len: usize,
    out_size: ?*u64,
) callconv(.c) i32 {
    const root_raw = root_ptr orelse return @intFromEnum(Status.invalid_argument);
    const path_raw = path_ptr orelse return @intFromEnum(Status.invalid_argument);
    const output = out_size orelse return @intFromEnum(Status.invalid_argument);

    var threaded: std.Io.Threaded = .init_single_threaded;
    const size = filesystem.fileSize(
        threaded.io(),
        capability.Set.fromBits(capability_bits),
        root_raw[0..root_len],
        path_raw[0..path_len],
    ) catch |err| return switch (err) {
        error.AccessDenied => @intFromEnum(Status.access_denied),
        error.InvalidPath => @intFromEnum(Status.invalid_argument),
        else => @intFromEnum(Status.operation_failed),
    };
    output.* = size;
    return @intFromEnum(Status.ok);
}

export fn remedy_core_logical_cpu_count(capability_bits: u64, out_count: ?*usize) callconv(.c) i32 {
    const output = out_count orelse return @intFromEnum(Status.invalid_argument);
    const result = system.snapshot(capability.Set.fromBits(capability_bits)) catch |err| {
        return switch (err) {
            error.AccessDenied => @intFromEnum(Status.access_denied),
            else => @intFromEnum(Status.operation_failed),
        };
    };
    output.* = result.logical_cpu_count;
    return @intFromEnum(Status.ok);
}

pub fn validateFrame(raw: []const u8) bool {
    if (raw.len < header_size) return false;
    if (!std.mem.eql(u8, raw[0..4], "RMDY")) return false;
    if (std.mem.readInt(u16, raw[4..6], .little) != abi_version) return false;
    if (std.mem.readInt(u16, raw[6..8], .little) == 0) return false;
    const payload_len = std.mem.readInt(u32, raw[12..16], .little);
    if (payload_len > max_payload_size) return false;
    return payload_len == raw.len - header_size;
}

test "validates the shared golden frame" {
    const golden = [_]u8{
        'R', 'M', 'D', 'Y', 1, 0, 1, 0, 5, 0, 0,  0,  4,  0,  0,  0,
        0,   1,   2,   3,   4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        'p', 'i', 'n', 'g',
    };
    try std.testing.expect(validateFrame(&golden));
    try std.testing.expectEqual(@as(u8, 1), remedy_core_validate_frame(&golden, golden.len));
}

test "rejects malformed frame families" {
    var frame = [_]u8{
        'R', 'M', 'D', 'Y', 1, 0, 3, 0, 0, 0, 0, 0, 1, 0, 0, 0,
        0,   0,   0,   0,   0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        'x',
    };
    try std.testing.expect(validateFrame(&frame));
    try std.testing.expect(!validateFrame(frame[0 .. header_size - 1]));
    frame[0] = 'N';
    try std.testing.expect(!validateFrame(&frame));
    frame[0] = 'R';
    frame[4] = 2;
    try std.testing.expect(!validateFrame(&frame));
    frame[4] = 1;
    frame[6] = 0;
    try std.testing.expect(!validateFrame(&frame));
    frame[6] = 3;
    frame[12] = 2;
    try std.testing.expect(!validateFrame(&frame));
}

test {
    _ = capability;
    _ = filesystem;
    _ = process;
    _ = serialization;
    _ = system;
}
