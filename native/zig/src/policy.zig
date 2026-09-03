const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");
const process = @import("process.zig");
const Sha256 = std.crypto.hash.sha2.Sha256;

pub const Decision = enum(u8) { allow, ask, deny };
pub const Reason = enum(u8) { approved, owner_checkpoint, malformed, not_allowlisted, argument_not_allowed };

pub const ProcessRule = struct {
    executable: []const u8,
    /// Every argument after argv[0] must start with one of these prefixes.
    /// An empty list means the executable accepts no additional arguments.
    argument_prefixes: []const []const u8 = &.{},
    owner_checkpoint: bool = false,
};

pub const Evidence = struct {
    decision: Decision,
    reason: Reason,
    right: capability.Right,
    timestamp_ms: u64,
    operation_hash: [Sha256.digest_length]u8,
    requires_owner_proof: bool,
};

pub fn evaluateProcess(
    rules: []const ProcessRule,
    argv: []const []const u8,
    owner_confirmed: bool,
    timestamp_ms: u64,
) Evidence {
    const operation_hash = hashArguments(argv);
    process.validateArguments(argv) catch return .{
        .decision = .deny,
        .reason = .malformed,
        .right = .process_spawn,
        .timestamp_ms = timestamp_ms,
        .operation_hash = operation_hash,
        .requires_owner_proof = false,
    };
    for (rules) |rule| {
        if (!std.mem.eql(u8, rule.executable, argv[0])) continue;
        for (argv[1..]) |argument| {
            var accepted = false;
            for (rule.argument_prefixes) |prefix| {
                if (std.mem.startsWith(u8, argument, prefix)) {
                    accepted = true;
                    break;
                }
            }
            if (!accepted) return .{
                .decision = .deny,
                .reason = .argument_not_allowed,
                .right = .process_spawn,
                .timestamp_ms = timestamp_ms,
                .operation_hash = operation_hash,
                .requires_owner_proof = false,
            };
        }
        if (rule.owner_checkpoint and !owner_confirmed) return .{
            .decision = .ask,
            .reason = .owner_checkpoint,
            .right = .process_spawn,
            .timestamp_ms = timestamp_ms,
            .operation_hash = operation_hash,
            .requires_owner_proof = true,
        };
        return .{
            .decision = .allow,
            .reason = .approved,
            .right = .process_spawn,
            .timestamp_ms = timestamp_ms,
            .operation_hash = operation_hash,
            .requires_owner_proof = rule.owner_checkpoint,
        };
    }
    return .{
        .decision = .deny,
        .reason = .not_allowlisted,
        .right = .process_spawn,
        .timestamp_ms = timestamp_ms,
        .operation_hash = operation_hash,
        .requires_owner_proof = false,
    };
}

pub fn hashArguments(argv: []const []const u8) [Sha256.digest_length]u8 {
    var hasher = Sha256.init(.{});
    var length: [8]u8 = undefined;
    for (argv) |argument| {
        std.mem.writeInt(u64, &length, argument.len, .little);
        hasher.update(&length);
        hasher.update(argument);
    }
    var digest: [Sha256.digest_length]u8 = undefined;
    hasher.final(&digest);
    return digest;
}

test "process policy is default deny and preserves owner checkpoints" {
    const read_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\read-tool.exe" else "/opt/remedy/read-tool";
    const send_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\send-tool.exe" else "/opt/remedy/send-tool";
    const unknown_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\unknown.exe" else "/opt/remedy/unknown";
    const rules = [_]ProcessRule{
        .{ .executable = read_tool, .argument_prefixes = &.{"--path="} },
        .{ .executable = send_tool, .owner_checkpoint = true },
    };
    try std.testing.expectEqual(Decision.allow, evaluateProcess(&rules, &.{ read_tool, "--path=safe" }, false, 1).decision);
    try std.testing.expectEqual(Reason.argument_not_allowed, evaluateProcess(&rules, &.{ read_tool, "--delete" }, false, 1).reason);
    try std.testing.expectEqual(Decision.ask, evaluateProcess(&rules, &.{send_tool}, false, 2).decision);
    try std.testing.expectEqual(Decision.allow, evaluateProcess(&rules, &.{send_tool}, true, 3).decision);
    try std.testing.expectEqual(Decision.deny, evaluateProcess(&rules, &.{unknown_tool}, true, 4).decision);
    try std.testing.expectEqual(Reason.malformed, evaluateProcess(&rules, &.{"read-tool"}, true, 4).reason);
    try std.testing.expectEqual(Reason.malformed, evaluateProcess(&rules, &.{}, true, 5).reason);
}
