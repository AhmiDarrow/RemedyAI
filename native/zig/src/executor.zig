const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");
const policy = @import("policy.zig");
const security = @import("security.zig");

pub const Authorization = struct {
    grant: security.Grant,
    evidence: policy.Evidence,
};

/// Enforces deterministic policy and independently verifies the scoped token.
/// No process is created until both checks agree.
pub fn authorizeProcess(
    verifier: *security.Verifier,
    rules: []const policy.ProcessRule,
    encoded_token: []const u8,
    subject: []const u8,
    scope: []const u8,
    argv: []const []const u8,
    owner_confirmed: bool,
    now_ms: u64,
) !Authorization {
    const evidence = policy.evaluateProcess(rules, argv, owner_confirmed, now_ms);
    switch (evidence.decision) {
        .deny => return error.PolicyDenied,
        .ask => return error.OwnerConfirmationRequired,
        .allow => {},
    }
    var required = capability.Set.one(.process_spawn);
    if (evidence.requires_owner_proof) {
        required = required.merged(capability.Set.one(.owner_checkpoint));
    }
    const grant = try verifier.verifyAndConsume(
        encoded_token,
        subject,
        scope,
        evidence.operation_hash,
        required,
        now_ms,
    );
    return .{ .grant = grant, .evidence = evidence };
}

test "executor requires policy and capability agreement" {
    const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
    const key = [_]u8{0x7c} ** Hmac.key_length;
    const nonce = [_]u8{0x33} ** 16;
    const safe_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\safe-tool.exe" else "/opt/remedy/safe-tool";
    const other_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\other-tool.exe" else "/opt/remedy/other-tool";
    const token = try security.issue(
        &key,
        "agent:executor",
        "workspace:test",
        policy.hashArguments(&.{safe_tool}),
        capability.Set.one(.process_spawn),
        1000,
        2000,
        nonce,
    );
    const rules = [_]policy.ProcessRule{.{ .executable = safe_tool }};
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();

    try std.testing.expectError(
        error.PolicyDenied,
        authorizeProcess(&verifier, &rules, &token, "agent:executor", "workspace:test", &.{other_tool}, true, 1500),
    );
    const authorization = try authorizeProcess(
        &verifier,
        &rules,
        &token,
        "agent:executor",
        "workspace:test",
        &.{safe_tool},
        true,
        1500,
    );
    try std.testing.expectEqual(policy.Decision.allow, authorization.evidence.decision);
}

test "owner confirmation boolean cannot bypass token proof" {
    const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
    const key = [_]u8{0x4d} ** Hmac.key_length;
    const send_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\send-tool.exe" else "/opt/remedy/send-tool";
    const delete_tool = if (builtin.os.tag == .windows) "C:\\Remedy\\delete-tool.exe" else "/opt/remedy/delete-tool";
    const rules = [_]policy.ProcessRule{
        .{ .executable = send_tool, .owner_checkpoint = true },
        .{ .executable = delete_tool, .owner_checkpoint = true },
    };
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();

    const weak_token = try security.issue(
        &key,
        "agent:sender",
        "workspace:test",
        policy.hashArguments(&.{send_tool}),
        capability.Set.one(.process_spawn),
        1000,
        2000,
        [_]u8{0x44} ** 16,
    );
    try std.testing.expectError(
        error.AccessDenied,
        authorizeProcess(&verifier, &rules, &weak_token, "agent:sender", "workspace:test", &.{send_tool}, true, 1500),
    );

    const approved_rights = capability.Set.one(.process_spawn).merged(capability.Set.one(.owner_checkpoint));
    const approved_token = try security.issue(
        &key,
        "agent:sender",
        "workspace:test",
        policy.hashArguments(&.{send_tool}),
        approved_rights,
        1000,
        2000,
        [_]u8{0x55} ** 16,
    );
    try std.testing.expectError(
        error.OperationMismatch,
        authorizeProcess(&verifier, &rules, &approved_token, "agent:sender", "workspace:test", &.{delete_tool}, true, 1500),
    );
    _ = try authorizeProcess(&verifier, &rules, &approved_token, "agent:sender", "workspace:test", &.{send_tool}, true, 1500);
}
