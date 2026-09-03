//! Host Command IR — Zig mirror of `remedy.execution.host.ir.HostOp`.
//! Phase 3 groundwork: parse/roundtrip only. No spawn, no ConPTY, no C ABI.

const std = @import("std");

pub const OpKind = enum {
    run,
    mkdir,
    which,
    env,
    script,
    raw,
    chain,

    pub fn fromName(raw: []const u8) OpKind {
        inline for (@typeInfo(OpKind).@"enum".fields) |field| {
            if (std.mem.eql(u8, field.name, raw)) return @field(OpKind, field.name);
        }
        return .raw;
    }

    pub fn asText(self: OpKind) []const u8 {
        return @tagName(self);
    }
};

pub const HostOp = struct {
    kind: OpKind = .raw,
    argv: []const []const u8 = &.{},
    paths: []const []const u8 = &.{},
    name: []const u8 = "",
    lang: []const u8 = "",
    body: []const u8 = "",
    host: []const u8 = "",
    text: []const u8 = "",
    cwd: []const u8 = "",
    env: []const EnvPair = &.{},
    ops: []const HostOp = &.{},

    pub const EnvPair = struct { key: []const u8, value: []const u8 };
};

/// Parse one HostOp from a JSON object (Python `HostOp.from_dict` semantics).
/// Non-object / missing kind → `.raw` with empty text. Result lives in `arena`.
pub fn fromJsonValue(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}!HostOp {
    const object = switch (value) {
        .object => |object| object,
        else => return .{},
    };

    var op: HostOp = .{};
    if (object.get("kind")) |kind_v| {
        op.kind = switch (kind_v) {
            .string => |s| OpKind.fromName(s),
            else => .raw,
        };
    }

    if (object.get("argv")) |argv_v| {
        op.argv = try stringList(arena, argv_v);
    }
    if (object.get("paths")) |paths_v| {
        op.paths = try stringList(arena, paths_v);
    }
    if (object.get("name")) |v| op.name = try asString(arena, v);
    if (object.get("lang")) |v| op.lang = try asString(arena, v);
    if (object.get("body")) |v| op.body = try asString(arena, v);
    if (object.get("host")) |v| op.host = try asString(arena, v);
    if (object.get("text")) |v| op.text = try asString(arena, v);
    if (object.get("cwd")) |v| op.cwd = try asString(arena, v);

    if (object.get("env")) |env_v| {
        op.env = try envPairs(arena, env_v);
    }
    if (object.get("ops")) |ops_v| {
        op.ops = try childOps(arena, ops_v);
    }
    return op;
}

pub fn parse(arena: std.mem.Allocator, json: []const u8) error{ OutOfMemory, InvalidJson }!HostOp {
    const parsed = std.json.parseFromSliceLeaky(std.json.Value, arena, json, .{}) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return error.InvalidJson,
    };
    return fromJsonValue(arena, parsed);
}

/// Serialize like Python `HostOp.to_dict` (omit empty fields). Caller frees.
pub fn toJson(gpa: std.mem.Allocator, op: HostOp) error{OutOfMemory}![]u8 {
    var list: std.ArrayList(u8) = .empty;
    errdefer list.deinit(gpa);
    try writeDict(&list, gpa, op);
    return try list.toOwnedSlice(gpa);
}

fn writeDict(list: *std.ArrayList(u8), gpa: std.mem.Allocator, op: HostOp) error{OutOfMemory}!void {
    try list.append(gpa, '{');
    var first = true;
    try writeKey(list, gpa, &first, "kind");
    try writeString(list, gpa, op.kind.asText());

    if (op.argv.len != 0) {
        try writeKey(list, gpa, &first, "argv");
        try writeStringArray(list, gpa, op.argv);
    }
    if (op.paths.len != 0) {
        try writeKey(list, gpa, &first, "paths");
        try writeStringArray(list, gpa, op.paths);
    }
    if (op.name.len != 0) {
        try writeKey(list, gpa, &first, "name");
        try writeString(list, gpa, op.name);
    }
    if (op.lang.len != 0) {
        try writeKey(list, gpa, &first, "lang");
        try writeString(list, gpa, op.lang);
    }
    if (op.body.len != 0) {
        try writeKey(list, gpa, &first, "body");
        try writeString(list, gpa, op.body);
    }
    if (op.host.len != 0) {
        try writeKey(list, gpa, &first, "host");
        try writeString(list, gpa, op.host);
    }
    if (op.text.len != 0) {
        try writeKey(list, gpa, &first, "text");
        try writeString(list, gpa, op.text);
    }
    if (op.cwd.len != 0) {
        try writeKey(list, gpa, &first, "cwd");
        try writeString(list, gpa, op.cwd);
    }
    if (op.env.len != 0) {
        try writeKey(list, gpa, &first, "env");
        try list.append(gpa, '{');
        var env_first = true;
        for (op.env) |pair| {
            if (!env_first) try list.append(gpa, ',');
            env_first = false;
            try writeString(list, gpa, pair.key);
            try list.append(gpa, ':');
            try writeString(list, gpa, pair.value);
        }
        try list.append(gpa, '}');
    }
    if (op.ops.len != 0) {
        try writeKey(list, gpa, &first, "ops");
        try list.append(gpa, '[');
        for (op.ops, 0..) |child, i| {
            if (i != 0) try list.append(gpa, ',');
            try writeDict(list, gpa, child);
        }
        try list.append(gpa, ']');
    }
    try list.append(gpa, '}');
}

fn writeKey(list: *std.ArrayList(u8), gpa: std.mem.Allocator, first: *bool, key: []const u8) error{OutOfMemory}!void {
    if (!first.*) try list.append(gpa, ',');
    first.* = false;
    try writeString(list, gpa, key);
    try list.append(gpa, ':');
}

fn writeString(list: *std.ArrayList(u8), gpa: std.mem.Allocator, s: []const u8) error{OutOfMemory}!void {
    // Compact JSON string with minimal escapes (fixture values are ASCII-safe).
    try list.append(gpa, '"');
    for (s) |c| {
        switch (c) {
            '"' => try list.appendSlice(gpa, "\\\""),
            '\\' => try list.appendSlice(gpa, "\\\\"),
            '\n' => try list.appendSlice(gpa, "\\n"),
            '\r' => try list.appendSlice(gpa, "\\r"),
            '\t' => try list.appendSlice(gpa, "\\t"),
            else => try list.append(gpa, c),
        }
    }
    try list.append(gpa, '"');
}

fn writeStringArray(list: *std.ArrayList(u8), gpa: std.mem.Allocator, items: []const []const u8) error{OutOfMemory}!void {
    try list.append(gpa, '[');
    for (items, 0..) |item, i| {
        if (i != 0) try list.append(gpa, ',');
        try writeString(list, gpa, item);
    }
    try list.append(gpa, ']');
}

fn asString(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const u8 {
    return switch (value) {
        .string => |s| try arena.dupe(u8, s),
        .integer => |n| try std.fmt.allocPrint(arena, "{d}", .{n}),
        .float => |n| try std.fmt.allocPrint(arena, "{d}", .{n}),
        .bool => |b| if (b) "true" else "false",
        .null => "",
        else => "",
    };
}

fn stringList(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const []const u8 {
    const array = switch (value) {
        .array => |a| a,
        else => return &.{},
    };
    var out = try arena.alloc([]const u8, array.items.len);
    var n: usize = 0;
    for (array.items) |item| {
        const s = try asString(arena, item);
        if (s.len == 0) continue;
        out[n] = s;
        n += 1;
    }
    return out[0..n];
}

fn envPairs(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const HostOp.EnvPair {
    const object = switch (value) {
        .object => |o| o,
        else => return &.{},
    };
    var out = try arena.alloc(HostOp.EnvPair, object.count());
    var i: usize = 0;
    var it = object.iterator();
    while (it.next()) |entry| : (i += 1) {
        out[i] = .{
            .key = try arena.dupe(u8, entry.key_ptr.*),
            .value = try asString(arena, entry.value_ptr.*),
        };
    }
    return out;
}

fn childOps(arena: std.mem.Allocator, value: std.json.Value) error{OutOfMemory}![]const HostOp {
    const array = switch (value) {
        .array => |a| a,
        else => return &.{},
    };
    var out = try arena.alloc(HostOp, array.items.len);
    var n: usize = 0;
    for (array.items) |item| {
        if (item != .object) continue;
        out[n] = try fromJsonValue(arena, item);
        n += 1;
    }
    return out[0..n];
}

fn expectFieldEql(op: HostOp, other: HostOp) !void {
    try std.testing.expectEqual(op.kind, other.kind);
    try std.testing.expectEqual(op.argv.len, other.argv.len);
    for (op.argv, other.argv) |a, b| try std.testing.expectEqualStrings(a, b);
    try std.testing.expectEqual(op.paths.len, other.paths.len);
    for (op.paths, other.paths) |a, b| try std.testing.expectEqualStrings(a, b);
    try std.testing.expectEqualStrings(op.name, other.name);
    try std.testing.expectEqualStrings(op.lang, other.lang);
    try std.testing.expectEqualStrings(op.body, other.body);
    try std.testing.expectEqualStrings(op.host, other.host);
    try std.testing.expectEqualStrings(op.text, other.text);
    try std.testing.expectEqualStrings(op.cwd, other.cwd);
    try std.testing.expectEqual(op.env.len, other.env.len);
    try std.testing.expectEqual(op.ops.len, other.ops.len);
    for (op.ops, other.ops) |a, b| try expectFieldEql(a, b);
}

test "shell_ir roundtrips run_pytest fixture case" {
    // From tests/fixtures/host_ir/ir_roundtrip.json id=run_pytest
    const fixture =
        \\{"kind":"run","argv":["python","-m","pytest","-q"],"cwd":"."}
    ;
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const op = try parse(arena, fixture);
    try std.testing.expectEqual(OpKind.run, op.kind);
    try std.testing.expectEqual(@as(usize, 4), op.argv.len);
    try std.testing.expectEqualStrings("python", op.argv[0]);
    try std.testing.expectEqualStrings("-q", op.argv[3]);
    try std.testing.expectEqualStrings(".", op.cwd);

    const encoded = try toJson(std.testing.allocator, op);
    defer std.testing.allocator.free(encoded);

    var arena2 = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena2.deinit();
    const back = try parse(arena2.allocator(), encoded);
    try expectFieldEql(op, back);
}

test "shell_ir coerces unknown kind to raw" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const op = try parse(arena_state.allocator(), "{\"kind\":\"nope\",\"argv\":[1,2]}");
    try std.testing.expectEqual(OpKind.raw, op.kind);
    try std.testing.expectEqual(@as(usize, 2), op.argv.len);
    try std.testing.expectEqualStrings("1", op.argv[0]);
}
