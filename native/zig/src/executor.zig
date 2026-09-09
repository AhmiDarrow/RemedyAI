const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");
const host = @import("host.zig");
const policy = @import("policy.zig");
const security = @import("security.zig");

pub const Authorization = struct {
    grant: security.Grant,
    evidence: policy.Evidence,
};

/// Enforces deterministic policy and independently verifies the scoped token.
/// No process is created until both checks agree.
///
/// The operation hash covers argv plus the caller-supplied environment
/// (`policy.hashSpawn`), so a token cannot be spent on a spawn that carries a
/// different environment. When `strict_env` is false, a token issued over the
/// argv-only hash is still accepted for a spawn that supplies env overrides —
/// the transitional contract for issuers that have not adopted
/// `remedy_core_policy_hash_spawn` yet. Env-less spawns hash identically in
/// both modes.
pub fn authorizeProcess(
    verifier: *security.Verifier,
    rules: []const policy.ProcessRule,
    encoded_token: []const u8,
    subject: []const u8,
    scope: []const u8,
    argv: []const []const u8,
    env: []const host.EnvPair,
    env_replace: bool,
    owner_confirmed: bool,
    now_ms: u64,
    strict_env: bool,
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
    const bound_hash = policy.hashSpawn(argv, env, env_replace);
    const grant = verifier.verifyAndConsume(
        encoded_token,
        subject,
        scope,
        bound_hash,
        required,
        now_ms,
    ) catch |err| switch (err) {
        error.OperationMismatch => blk: {
            const env_bound = env.len > 0 or env_replace;
            if (strict_env or !env_bound) return err;
            // The verifier checks the operation hash before recording the
            // nonce, so this retry cannot burn a token that then fails.
            break :blk try verifier.verifyAndConsume(
                encoded_token,
                subject,
                scope,
                evidence.operation_hash,
                required,
                now_ms,
            );
        },
        else => return err,
    };
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
        authorizeProcess(&verifier, &rules, &token, "agent:executor", "workspace:test", &.{other_tool}, &.{}, false, true, 1500, true),
    );
    const authorization = try authorizeProcess(
        &verifier,
        &rules,
        &token,
        "agent:executor",
        "workspace:test",
        &.{safe_tool},
        &.{},
        false,
        true,
        1500,
        true,
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
        authorizeProcess(&verifier, &rules, &weak_token, "agent:sender", "workspace:test", &.{send_tool}, &.{}, false, true, 1500, true),
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
        authorizeProcess(&verifier, &rules, &approved_token, "agent:sender", "workspace:test", &.{delete_tool}, &.{}, false, true, 1500, true),
    );
    _ = try authorizeProcess(&verifier, &rules, &approved_token, "agent:sender", "workspace:test", &.{send_tool}, &.{}, false, true, 1500, true);
}

test "environment is bound into the operation hash; argv-only tokens are transitional" {
    const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;
    const key = [_]u8{0x5e} ** Hmac.key_length;
    const tool = if (builtin.os.tag == .windows) "C:\\Remedy\\tool.exe" else "/opt/remedy/tool";
    const rules = [_]policy.ProcessRule{.{ .executable = tool, .allow_any_arguments = true }};
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();
    const env = [_]host.EnvPair{.{ .key = "RUST_LOG", .value = "debug" }};
    const other_env = [_]host.EnvPair{.{ .key = "RUST_LOG", .value = "trace" }};

    // Env-bound token: accepted for the same env in strict mode, refused for
    // a different env, and refused for a spawn that drops the env.
    const bound = try security.issue(&key, "agent:x", "ws", policy.hashSpawn(&.{tool}, &env, false), capability.Set.one(.process_spawn), 1000, 2000, [_]u8{0x61} ** 16);
    try std.testing.expectError(
        error.OperationMismatch,
        authorizeProcess(&verifier, &rules, &bound, "agent:x", "ws", &.{tool}, &other_env, false, false, 1500, true),
    );
    try std.testing.expectError(
        error.OperationMismatch,
        authorizeProcess(&verifier, &rules, &bound, "agent:x", "ws", &.{tool}, &.{}, false, false, 1500, false),
    );
    _ = try authorizeProcess(&verifier, &rules, &bound, "agent:x", "ws", &.{tool}, &env, false, false, 1500, true);
    // Consumed: the nonce is burned only by the successful verification.
    try std.testing.expectError(
        error.Replayed,
        authorizeProcess(&verifier, &rules, &bound, "agent:x", "ws", &.{tool}, &env, false, false, 1500, true),
    );

    // Argv-only token with env overrides: transitional accept, strict refuse.
    const legacy = try security.issue(&key, "agent:x", "ws", policy.hashArguments(&.{tool}), capability.Set.one(.process_spawn), 1000, 2000, [_]u8{0x62} ** 16);
    try std.testing.expectError(
        error.OperationMismatch,
        authorizeProcess(&verifier, &rules, &legacy, "agent:x", "ws", &.{tool}, &env, false, false, 1500, true),
    );
    _ = try authorizeProcess(&verifier, &rules, &legacy, "agent:x", "ws", &.{tool}, &env, false, false, 1500, false);
    // Env-less spawn hashes identically in both modes.
    const plain = try security.issue(&key, "agent:x", "ws", policy.hashArguments(&.{tool}), capability.Set.one(.process_spawn), 1000, 2000, [_]u8{0x63} ** 16);
    _ = try authorizeProcess(&verifier, &rules, &plain, "agent:x", "ws", &.{tool}, &.{}, false, false, 1500, true);
}
