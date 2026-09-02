const std = @import("std");

pub const encoded_size = 24;

pub const Record = struct {
    kind: u16,
    flags: u16,
    sequence: u64,
    correlation_id: u64,
    payload_length: u32,
};

pub fn encode(record: Record, out: *[encoded_size]u8) void {
    std.mem.writeInt(u16, out[0..2], record.kind, .little);
    std.mem.writeInt(u16, out[2..4], record.flags, .little);
    std.mem.writeInt(u64, out[4..12], record.sequence, .little);
    std.mem.writeInt(u64, out[12..20], record.correlation_id, .little);
    std.mem.writeInt(u32, out[20..24], record.payload_length, .little);
}

pub fn decode(raw: []const u8) error{InvalidRecord}!Record {
    if (raw.len != encoded_size) return error.InvalidRecord;
    return .{
        .kind = std.mem.readInt(u16, raw[0..2], .little),
        .flags = std.mem.readInt(u16, raw[2..4], .little),
        .sequence = std.mem.readInt(u64, raw[4..12], .little),
        .correlation_id = std.mem.readInt(u64, raw[12..20], .little),
        .payload_length = std.mem.readInt(u32, raw[20..24], .little),
    };
}

test "record serialization is stable and little endian" {
    const expected = Record{
        .kind = 7,
        .flags = 3,
        .sequence = 42,
        .correlation_id = 99,
        .payload_length = 1024,
    };
    var bytes: [encoded_size]u8 = undefined;
    encode(expected, &bytes);
    try std.testing.expectEqual(@as(u8, 7), bytes[0]);
    try std.testing.expectEqualDeep(expected, try decode(&bytes));
    try std.testing.expectError(error.InvalidRecord, decode(bytes[0 .. encoded_size - 1]));
}
