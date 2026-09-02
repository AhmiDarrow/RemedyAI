const std = @import("std");
const capability = @import("capability.zig");

pub fn fileSize(
    io: std.Io,
    capabilities: capability.Set,
    root_absolute: []const u8,
    relative_path: []const u8,
) !u64 {
    try capabilities.require(.filesystem_read);
    try capability.validateRelativePath(relative_path);
    if (!std.fs.path.isAbsolute(root_absolute)) return error.InvalidPath;
    var root = try std.Io.Dir.openDirAbsolute(io, root_absolute, .{});
    defer root.close(io);
    return fileSizeInDir(io, capabilities, root, relative_path);
}

pub fn fileSizeInDir(
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    relative_path: []const u8,
) !u64 {
    try capabilities.require(.filesystem_read);
    try capability.validateRelativePath(relative_path);
    var file = try root.openFile(io, relative_path, .{ .resolve_beneath = true });
    defer file.close(io);
    return (try file.stat(io)).size;
}

pub fn writeAllInDir(
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    relative_path: []const u8,
    data: []const u8,
) !void {
    try capabilities.require(.filesystem_write);
    try capability.validateRelativePath(relative_path);
    var file = try root.createFile(io, relative_path, .{ .resolve_beneath = true });
    defer file.close(io);
    try file.writeStreamingAll(io, data);
}

test "filesystem primitives require rights and remain beneath their root" {
    const io = std.testing.io;
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();

    const write_only = capability.Set.one(.filesystem_write);
    try writeAllInDir(io, write_only, tmp.dir, "sample.txt", "remedy");
    try std.testing.expectError(
        error.AccessDenied,
        fileSizeInDir(io, write_only, tmp.dir, "sample.txt"),
    );

    const read_only = capability.Set.one(.filesystem_read);
    try std.testing.expectEqual(@as(u64, 6), try fileSizeInDir(io, read_only, tmp.dir, "sample.txt"));
    try std.testing.expectError(
        error.InvalidPath,
        fileSizeInDir(io, read_only, tmp.dir, "../sample.txt"),
    );
}
