const std = @import("std");

pub const Right = enum(u6) {
    filesystem_read = 0,
    filesystem_write = 1,
    process_spawn = 2,
    system_read = 3,
    filesystem_delete = 4,
};

pub const Set = struct {
    bits: u64 = 0,

    pub fn fromBits(bits: u64) Set {
        return .{ .bits = bits };
    }

    pub fn one(right: Right) Set {
        return .{ .bits = mask(right) };
    }

    pub fn contains(self: Set, right: Right) bool {
        return self.bits & mask(right) != 0;
    }

    pub fn require(self: Set, right: Right) error{AccessDenied}!void {
        if (!self.contains(right)) return error.AccessDenied;
    }

    fn mask(right: Right) u64 {
        return @as(u64, 1) << @intFromEnum(right);
    }
};

pub const PathError = error{InvalidPath};

/// Accept only portable, relative paths. Kernel-level beneath enforcement is
/// still required at the file-open boundary to close symlink/reparse escapes.
pub fn validateRelativePath(path: []const u8) PathError!void {
    if (path.len == 0 or std.mem.indexOfScalar(u8, path, 0) != null) {
        return error.InvalidPath;
    }
    if (std.fs.path.isAbsolutePosix(path) or std.fs.path.isAbsoluteWindows(path)) {
        return error.InvalidPath;
    }

    var segment_start: usize = 0;
    for (path, 0..) |byte, index| {
        if (byte != '/' and byte != '\\') continue;
        try validateSegment(path[segment_start..index]);
        segment_start = index + 1;
    }
    try validateSegment(path[segment_start..]);
}

fn validateSegment(segment: []const u8) PathError!void {
    if (segment.len == 0 or std.mem.eql(u8, segment, ".") or std.mem.eql(u8, segment, "..")) {
        return error.InvalidPath;
    }
}

test "capability sets preserve least privilege" {
    const read_only = Set.one(.filesystem_read);
    try read_only.require(.filesystem_read);
    try std.testing.expectError(error.AccessDenied, read_only.require(.filesystem_write));
}

test "relative path validation rejects traversal families" {
    const invalid = [_][]const u8{
        "",              "/etc/passwd", "C:\\Windows", "..",     "../secret",   "a/../secret",
        "a\\..\\secret", "./file",      "a//b",        "a\\\\b", "nul\x00tail",
    };
    for (invalid) |path| {
        try std.testing.expectError(error.InvalidPath, validateRelativePath(path));
    }
    try validateRelativePath("workspace/data.json");
    try validateRelativePath("workspace\\data.json");
}
