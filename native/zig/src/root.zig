const std = @import("std");

pub const abi_version: u32 = 1;
pub const header_size: usize = 32;
pub const max_payload_size: u32 = 16 << 20;

export fn remedy_core_abi_version() callconv(.c) u32 {
    return abi_version;
}

export fn remedy_core_validate_frame(ptr: ?[*]const u8, len: usize) callconv(.c) u8 {
    const raw_ptr = ptr orelse return 0;
    return if (validateFrame(raw_ptr[0..len])) 1 else 0;
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
