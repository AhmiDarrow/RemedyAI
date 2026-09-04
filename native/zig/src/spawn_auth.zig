//! Authorized process spawn C ABI (additive on ABI 5).
//!
//! Every production spawn goes through policy + a short-lived HMAC capability
//! token before CreateProcess / ConPTY. The signing key is supplied at runtime
//! (`remedy_core_security_set_signing_key`); nothing is compiled into the tree.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");
const capability = @import("capability.zig");
const executor = @import("executor.zig");
const policy = @import("policy.zig");
const security = @import("security.zig");
const write_jail = @import("write_jail.zig");

const is_windows = builtin.os.tag == .windows;
const windows_host = if (is_windows) @import("host_windows.zig") else struct {};
const windows_conpty = if (is_windows) @import("conpty_windows.zig") else struct {};

const Status = root.Status;
const Hmac = std.crypto.auth.hmac.sha2.HmacSha256;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const denied_status: i32 = @intFromEnum(Status.access_denied);
const failed_status: i32 = @intFromEnum(Status.operation_failed);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

const default_subject = "agent:remedy";
const default_scope = "workspace:local";

var g_mutex: std.atomic.Mutex = .unlocked;
var g_key: [Hmac.key_length]u8 = undefined;
var g_key_set: bool = false;
var g_verifier: ?security.Verifier = null;

fn lock() void {
    while (!g_mutex.tryLock()) std.atomic.spinLoopHint();
}

fn unlock() void {
    g_mutex.unlock();
}

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn authStatus(err: anyerror) i32 {
    return switch (err) {
        error.PolicyDenied,
        error.OwnerConfirmationRequired,
        error.AccessDenied,
        error.InvalidToken,
        error.Expired,
        error.NotYetValid,
        error.Replayed,
        error.SubjectMismatch,
        error.ScopeMismatch,
        error.OperationMismatch,
        => denied_status,
        error.InvalidArgument,
        error.InvalidArguments,
        error.InvalidGrant,
        error.InvalidPath,
        => invalid_status,
        error.Unsupported => unsupported_status,
        error.OutOfMemory => failed_status,
        else => failed_status,
    };
}

fn requireKeyLocked() !void {
    if (!g_key_set or g_verifier == null) return error.AccessDenied;
}

/// Install the HMAC signing key used to issue and verify capability tokens.
/// `len` must be at least 32. Replaces any previous key and resets replay state.
export fn remedy_core_security_set_signing_key(key: ?[*]const u8, len: usize) callconv(.c) i32 {
    const raw = key orelse return invalid_status;
    if (len < Hmac.key_length) return invalid_status;
    lock();
    defer unlock();
    @memcpy(g_key[0..Hmac.key_length], raw[0..Hmac.key_length]);
    if (g_verifier) |*verifier| verifier.deinit();
    g_verifier = security.Verifier.init(host.allocator, g_key[0..]);
    g_key_set = true;
    return ok_status;
}

/// Forget the signing key and verifier state.
export fn remedy_core_security_clear_signing_key() callconv(.c) i32 {
    lock();
    defer unlock();
    if (g_verifier) |*verifier| verifier.deinit();
    g_verifier = null;
    g_key_set = false;
    @memset(&g_key, 0);
    return ok_status;
}

/// SHA-256 over argv_json parsed as a JSON string array (same framing as spawn).
export fn remedy_core_policy_hash_argv(
    argv_json: ?[*]const u8,
    argv_len: usize,
    out_hash: ?[*]u8,
    out_hash_len: usize,
) callconv(.c) i32 {
    if (out_hash == null or out_hash_len < 32) return invalid_status;
    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;
    const digest = policy.hashArguments(argv);
    @memcpy(out_hash.?[0..32], &digest);
    return ok_status;
}

/// Issue a v2 capability token. `operation_hash` is 32 bytes; `nonce` is 16;
/// `out_token` must have room for `security.token_size` (169) bytes.
export fn remedy_core_capability_issue(
    subject: ?[*]const u8,
    subject_len: usize,
    scope: ?[*]const u8,
    scope_len: usize,
    operation_hash: ?[*]const u8,
    operation_hash_len: usize,
    rights_bits: u64,
    issued_at_ms: u64,
    expires_at_ms: u64,
    nonce: ?[*]const u8,
    nonce_len: usize,
    out_token: ?[*]u8,
    out_token_len: usize,
) callconv(.c) i32 {
    if (operation_hash == null or operation_hash_len != 32) return invalid_status;
    if (nonce == null or nonce_len != 16) return invalid_status;
    if (out_token == null or out_token_len < security.token_size) return invalid_status;

    lock();
    defer unlock();
    requireKeyLocked() catch return denied_status;

    var op: [32]u8 = undefined;
    @memcpy(&op, operation_hash.?[0..32]);
    var nonce_buf: [16]u8 = undefined;
    @memcpy(&nonce_buf, nonce.?[0..16]);

    const rights = capability.Set.fromBits(rights_bits);
    const encoded = security.issue(
        g_key[0..],
        slice(subject, subject_len),
        slice(scope, scope_len),
        op,
        rights,
        issued_at_ms,
        expires_at_ms,
        nonce_buf,
    ) catch |err| return authStatus(err);
    @memcpy(out_token.?[0..security.token_size], &encoded);
    return ok_status;
}

fn authorizeLocked(
    argv: []const []const u8,
    cwd: []const u8,
    token: []const u8,
    subject: []const u8,
    scope: []const u8,
    owner_confirmed: bool,
    now_ms: u64,
) !void {
    try requireKeyLocked();
    // Workdir / write-root jail + auth refuse (equal-or-better than Python
    // sandbox workdir gate). Runs before token consume so a denied spawn
    // does not burn the nonce.
    try write_jail.checkSpawn(host.allocator, argv, cwd);
    const verifier = &(g_verifier orelse return error.AccessDenied);
    _ = try executor.authorizeProcess(
        verifier,
        &policy.product_default_rules,
        token,
        subject,
        scope,
        argv,
        owner_confirmed,
        now_ms,
    );
}

fn subjectOrDefault(ptr: ?[*]const u8, len: usize) []const u8 {
    const value = slice(ptr, len);
    return if (value.len == 0) default_subject else value;
}

fn scopeOrDefault(ptr: ?[*]const u8, len: usize) []const u8 {
    const value = slice(ptr, len);
    return if (value.len == 0) default_scope else value;
}

/// Authorize then hidden-spawn. argv[0] must already be an absolute path.
export fn remedy_core_process_spawn_authorized(
    argv_json: ?[*]const u8,
    argv_len: usize,
    cwd: ?[*]const u8,
    cwd_len: usize,
    env_json: ?[*]const u8,
    env_len: usize,
    token: ?[*]const u8,
    token_len: usize,
    subject: ?[*]const u8,
    subject_len: usize,
    scope: ?[*]const u8,
    scope_len: usize,
    owner_confirmed: u8,
    now_ms: u64,
    out_pid: ?*u32,
    out_handle: ?*u64,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    pid_slot.* = 0;
    handle_slot.* = 0;

    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;

    lock();
    const auth_result = authorizeLocked(
        argv,
        slice(cwd, cwd_len),
        slice(token, token_len),
        subjectOrDefault(subject, subject_len),
        scopeOrDefault(scope, scope_len),
        owner_confirmed != 0,
        now_ms,
    );
    unlock();
    auth_result catch |err| return authStatus(err);

    const spawned = windows_host.spawnHidden(
        slice(argv_json, argv_len),
        slice(cwd, cwd_len),
        slice(env_json, env_len),
    ) catch |err| return host.statusOf(err);
    pid_slot.* = spawned.pid;
    handle_slot.* = spawned.handle;
    return ok_status;
}

/// Authorize then ConPTY-spawn. argv[0] must already be an absolute path.
export fn remedy_core_conpty_spawn_authorized(
    argv_json: ?[*]const u8,
    argv_len: usize,
    cwd: ?[*]const u8,
    cwd_len: usize,
    env_json: ?[*]const u8,
    env_len: usize,
    cols: u16,
    rows: u16,
    token: ?[*]const u8,
    token_len: usize,
    subject: ?[*]const u8,
    subject_len: usize,
    scope: ?[*]const u8,
    scope_len: usize,
    owner_confirmed: u8,
    now_ms: u64,
    out_pid: ?*u32,
    out_handle: ?*u64,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    pid_slot.* = 0;
    handle_slot.* = 0;

    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;

    lock();
    const auth_result = authorizeLocked(
        argv,
        slice(cwd, cwd_len),
        slice(token, token_len),
        subjectOrDefault(subject, subject_len),
        scopeOrDefault(scope, scope_len),
        owner_confirmed != 0,
        now_ms,
    );
    unlock();
    auth_result catch |err| return authStatus(err);

    const spawned = windows_conpty.spawn(
        slice(argv_json, argv_len),
        slice(cwd, cwd_len),
        slice(env_json, env_len),
        cols,
        rows,
    ) catch |err| return host.statusOf(err);
    pid_slot.* = spawned.pid;
    handle_slot.* = spawned.handle;
    return ok_status;
}

test "product rules allow git and deny sudo through authorizeProcess" {
    const key = [_]u8{0x91} ** Hmac.key_length;
    const git = if (builtin.os.tag == .windows) "C:\\Program Files\\Git\\cmd\\git.exe" else "/usr/bin/git";
    const sudo = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\sudo.exe" else "/usr/bin/sudo";
    var verifier = security.Verifier.init(std.testing.allocator, &key);
    defer verifier.deinit();

    const token = try security.issue(
        &key,
        default_subject,
        default_scope,
        policy.hashArguments(&.{ git, "status" }),
        capability.Set.one(.process_spawn),
        1000,
        2000,
        [_]u8{0x10} ** 16,
    );
    _ = try executor.authorizeProcess(
        &verifier,
        &policy.product_default_rules,
        &token,
        default_subject,
        default_scope,
        &.{ git, "status" },
        false,
        1500,
    );
    try std.testing.expectError(
        error.PolicyDenied,
        executor.authorizeProcess(
            &verifier,
            &policy.product_default_rules,
            &token,
            default_subject,
            default_scope,
            &.{sudo},
            false,
            1500,
        ),
    );
}
