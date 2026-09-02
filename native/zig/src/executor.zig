const std = @import("std");
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
        required,
        now_ms,
    );
    return .{ .grant = grant, .evidence = evidence };
}

test "executor requires policy and capability agreement" {
    const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
    const key = [_]u8{0x7c} ** Hmac.key_length;
    const nonce = [_]u8{0x33} ** 16;
    const token = try security.issue(
        &key,
        "agent:executor",
        "workspace:test",
        capability.Set.one(.process_spawn),
        1000,
        2000,
        nonce,
    );
    const rules = [_]policy.ProcessRule{.{ .executable = "safe-tool" }};
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();

    try std.testing.expectError(
        error.PolicyDenied,
        authorizeProcess(&verifier, &rules, &token, "agent:executor", "workspace:test", &.{"other-tool"}, true, 1500),
    );
    const authorization = try authorizeProcess(
        &verifier,
        &rules,
        &token,
        "agent:executor",
        "workspace:test",
        &.{"safe-tool"},
        true,
        1500,
    );
    try std.testing.expectEqual(policy.Decision.allow, authorization.evidence.decision);
}

test "owner confirmation boolean cannot bypass token proof" {
    const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
    const key = [_]u8{0x4d} ** Hmac.key_length;
    const rules = [_]policy.ProcessRule{.{ .executable = "send-tool", .owner_checkpoint = true }};
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();

    const weak_token = try security.issue(
        &key,
        "agent:sender",
        "workspace:test",
        capability.Set.one(.process_spawn),
        1000,
        2000,
        [_]u8{0x44} ** 16,
    );
    try std.testing.expectError(
        error.AccessDenied,
        authorizeProcess(&verifier, &rules, &weak_token, "agent:sender", "workspace:test", &.{"send-tool"}, true, 1500),
    );

    const approved_rights = capability.Set.one(.process_spawn).merged(capability.Set.one(.owner_checkpoint));
    const approved_token = try security.issue(
        &key,
        "agent:sender",
        "workspace:test",
        approved_rights,
        1000,
        2000,
        [_]u8{0x55} ** 16,
    );
    _ = try authorizeProcess(&verifier, &rules, &approved_token, "agent:sender", "workspace:test", &.{"send-tool"}, true, 1500);
}
