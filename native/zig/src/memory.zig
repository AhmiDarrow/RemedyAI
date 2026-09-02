const std = @import("std");

pub const header_size = 8;
pub const max_record_size = 16 << 20;

pub const ScanResult = struct {
    valid_bytes: usize,
    record_count: usize,
    truncated_tail: bool,
};

/// Scans the language-neutral memory log format used by the Go orchestrator.
/// A partial tail is recoverable; a checksum mismatch in a complete record is not.
pub fn scanLog(raw: []const u8) error{ CorruptLog, RecordTooLarge }!ScanResult {
    var offset: usize = 0;
    var count: usize = 0;
    while (offset < raw.len) {
        if (raw.len - offset < header_size) {
            return .{ .valid_bytes = offset, .record_count = count, .truncated_tail = true };
        }
        const payload_len = std.mem.readInt(u32, raw[offset..][0..4], .little);
        if (payload_len > max_record_size) return error.RecordTooLarge;
        const record_end = offset + header_size + payload_len;
        if (record_end > raw.len) {
            return .{ .valid_bytes = offset, .record_count = count, .truncated_tail = true };
        }
        const expected_crc = std.mem.readInt(u32, raw[offset + 4 ..][0..4], .little);
        const payload = raw[offset + header_size .. record_end];
        if (std.hash.crc.Crc32.hash(payload) != expected_crc) return error.CorruptLog;
        offset = record_end;
        count += 1;
    }
    return .{ .valid_bytes = offset, .record_count = count, .truncated_tail = false };
}

test "memory log scanner agrees with checksum format and recovers tails" {
    const payload = "{\"id\":\"one\"}";
    var raw: [header_size + payload.len + 3]u8 = undefined;
    std.mem.writeInt(u32, raw[0..4], payload.len, .little);
    std.mem.writeInt(u32, raw[4..8], std.hash.crc.Crc32.hash(payload), .little);
    @memcpy(raw[8 .. 8 + payload.len], payload);
    const tail = [_]u8{ 9, 8, 7 };
    @memcpy(raw[8 + payload.len ..], &tail);
    const result = try scanLog(&raw);
    try std.testing.expectEqual(@as(usize, 1), result.record_count);
    try std.testing.expect(result.truncated_tail);
    raw[8] ^= 1;
    try std.testing.expectError(error.CorruptLog, scanLog(&raw));
}
