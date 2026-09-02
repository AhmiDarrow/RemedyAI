const std = @import("std");
const capability = @import("capability.zig");
const process = @import("process.zig");

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
    operation_hash: u64,
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

fn hashArguments(argv: []const []const u8) u64 {
    var hash: u64 = 0;
    for (argv) |argument| hash = std.hash.Wyhash.hash(hash, argument);
    return hash;
}

test "process policy is default deny and preserves owner checkpoints" {
    const rules = [_]ProcessRule{
        .{ .executable = "read-tool", .argument_prefixes = &.{"--path="} },
        .{ .executable = "send-tool", .owner_checkpoint = true },
    };
    try std.testing.expectEqual(Decision.allow, evaluateProcess(&rules, &.{ "read-tool", "--path=safe" }, false, 1).decision);
    try std.testing.expectEqual(Reason.argument_not_allowed, evaluateProcess(&rules, &.{ "read-tool", "--delete" }, false, 1).reason);
    try std.testing.expectEqual(Decision.ask, evaluateProcess(&rules, &.{"send-tool"}, false, 2).decision);
    try std.testing.expectEqual(Decision.allow, evaluateProcess(&rules, &.{"send-tool"}, true, 3).decision);
    try std.testing.expectEqual(Decision.deny, evaluateProcess(&rules, &.{"unknown"}, true, 4).decision);
    try std.testing.expectEqual(Reason.malformed, evaluateProcess(&rules, &.{}, true, 5).reason);
}
