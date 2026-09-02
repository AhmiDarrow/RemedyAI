const std = @import("std");
const capability = @import("capability.zig");

pub const max_read_bytes = 16 * 1024 * 1024;

pub const SearchResult = struct {
    paths: [][]u8,

    pub fn deinit(self: SearchResult, allocator: std.mem.Allocator) void {
        for (self.paths) |path| allocator.free(path);
        allocator.free(self.paths);
    }
};

/// A portable polling-watcher snapshot. Comparing two snapshots detects path,
/// metadata, and content-size changes without requiring platform-specific APIs.
pub const TreeSnapshot = struct {
    entry_count: u64 = 0,
    total_file_bytes: u64 = 0,
    fingerprint: u64 = 0,

    pub fn changed(self: TreeSnapshot, newer: TreeSnapshot) bool {
        return !std.meta.eql(self, newer);
    }
};

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

pub fn readAllInDir(
    allocator: std.mem.Allocator,
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    relative_path: []const u8,
) ![]u8 {
    try capabilities.require(.filesystem_read);
    try capability.validateRelativePath(relative_path);
    var file = try root.openFile(io, relative_path, .{ .resolve_beneath = true });
    defer file.close(io);
    var reader = file.reader(io, &.{});
    return reader.interface.allocRemaining(allocator, .limited(max_read_bytes));
}

pub fn copyInDir(
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    source_path: []const u8,
    destination_path: []const u8,
) !void {
    try capabilities.require(.filesystem_read);
    try capabilities.require(.filesystem_write);
    try capability.validateRelativePath(source_path);
    try capability.validateRelativePath(destination_path);
    try root.copyFile(source_path, root, destination_path, io, .{ .replace = false });
}

pub fn moveInDir(
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    source_path: []const u8,
    destination_path: []const u8,
) !void {
    try capabilities.require(.filesystem_write);
    try capability.validateRelativePath(source_path);
    try capability.validateRelativePath(destination_path);
    try root.renamePreserve(source_path, root, destination_path, io);
}

pub fn deleteFileInDir(
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    relative_path: []const u8,
) !void {
    try capabilities.require(.filesystem_delete);
    try capability.validateRelativePath(relative_path);
    try root.deleteFile(io, relative_path);
}

pub fn searchNames(
    allocator: std.mem.Allocator,
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
    needle: []const u8,
    max_results: usize,
) !SearchResult {
    try capabilities.require(.filesystem_read);
    if (needle.len == 0 or max_results == 0) return error.InvalidSearch;

    var iterable_root = try root.openDir(io, ".", .{ .iterate = true, .follow_symlinks = false });
    defer iterable_root.close(io);

    var matches: std.ArrayList([]u8) = .empty;
    errdefer {
        for (matches.items) |path| allocator.free(path);
        matches.deinit(allocator);
    }
    var walker = try iterable_root.walk(allocator);
    defer walker.deinit();
    while (try walker.next(io)) |entry| {
        if (matches.items.len == max_results) break;
        if (std.mem.indexOf(u8, entry.basename, needle) == null) continue;
        try matches.append(allocator, try allocator.dupe(u8, entry.path));
    }
    return .{ .paths = try matches.toOwnedSlice(allocator) };
}

pub fn snapshotTree(
    allocator: std.mem.Allocator,
    io: std.Io,
    capabilities: capability.Set,
    root: std.Io.Dir,
) !TreeSnapshot {
    try capabilities.require(.filesystem_read);
    var iterable_root = try root.openDir(io, ".", .{ .iterate = true, .follow_symlinks = false });
    defer iterable_root.close(io);
    var result: TreeSnapshot = .{};
    var walker = try iterable_root.walk(allocator);
    defer walker.deinit();
    while (try walker.next(io)) |entry| {
        result.entry_count += 1;
        var entry_hash = std.hash.Wyhash.hash(0, entry.path);
        if (entry.kind == .file) {
            const stat = try entry.dir.statFile(io, entry.basename, .{ .follow_symlinks = false });
            result.total_file_bytes += stat.size;
            entry_hash ^= stat.size;
            entry_hash ^= std.hash.Wyhash.hash(1, std.mem.asBytes(&stat.mtime.nanoseconds));
        }
        result.fingerprint ^= entry_hash;
    }
    return result;
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

test "filesystem mutation, traversal, search, and watch snapshots compose" {
    const io = std.testing.io;
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    const rights = capability.Set.fromBits(
        capability.Set.one(.filesystem_read).bits |
            capability.Set.one(.filesystem_write).bits |
            capability.Set.one(.filesystem_delete).bits,
    );

    try writeAllInDir(io, rights, tmp.dir, "alpha.txt", "one");
    const before = try snapshotTree(std.testing.allocator, io, rights, tmp.dir);
    try copyInDir(io, rights, tmp.dir, "alpha.txt", "alpha-copy.txt");
    try moveInDir(io, rights, tmp.dir, "alpha-copy.txt", "alpha-moved.txt");

    const found = try searchNames(std.testing.allocator, io, rights, tmp.dir, "alpha", 10);
    defer found.deinit(std.testing.allocator);
    try std.testing.expectEqual(@as(usize, 2), found.paths.len);

    const content = try readAllInDir(std.testing.allocator, io, rights, tmp.dir, "alpha-moved.txt");
    defer std.testing.allocator.free(content);
    try std.testing.expectEqualStrings("one", content);
    const after = try snapshotTree(std.testing.allocator, io, rights, tmp.dir);
    try std.testing.expect(before.changed(after));

    try deleteFileInDir(io, rights, tmp.dir, "alpha-moved.txt");
    try std.testing.expectError(
        error.AccessDenied,
        deleteFileInDir(io, capability.Set.one(.filesystem_write), tmp.dir, "alpha.txt"),
    );
}
