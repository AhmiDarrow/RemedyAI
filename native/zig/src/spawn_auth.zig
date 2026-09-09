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
const process = @import("process.zig");
const security = @import("security.zig");
const write_jail = @import("write_jail.zig");

const is_windows = builtin.os.tag == .windows;
const is_linux = builtin.os.tag == .linux;
const windows_host = if (is_windows) @import("host_windows.zig") else struct {};
const linux_host = if (is_linux) @import("host_linux.zig") else struct {};
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
/// Bound to argv alone: a token issued over this hash says nothing about the
/// environment the spawn will receive. Prefer `remedy_core_policy_hash_spawn`.
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

/// The environment overrides a spawn export will apply, in the exact form the
/// operation hash binds. `replace_request` is the out-of-band replacement flag
/// `remedy_core_policy_hash_spawn` accepts for the plain-object `env_json`
/// shape; the `{"env": ..., "replace_env": ...}` wrapper carries its own.
const SpawnEnv = struct {
    pairs: []const host.EnvPair = &.{},
    replace: bool = false,
};

fn spawnEnv(arena: std.mem.Allocator, env_json: []const u8, replace_request: bool) host.Error!SpawnEnv {
    const spec = (try host.parseEnvSpec(arena, env_json)) orelse
        return .{ .replace = replace_request };
    return .{ .pairs = spec.pairs, .replace = spec.replace or replace_request };
}

/// SHA-256 over argv plus the caller-supplied environment overrides and the
/// replacement flag — the operation hash an authorized spawn recomputes from
/// the environment it actually receives, so a token cannot be minted for one
/// environment and spent on another. `env_json` takes the same two shapes as
/// the spawn exports. With no environment and `replace_env` 0 the digest is
/// byte-identical to `remedy_core_policy_hash_argv`.
export fn remedy_core_policy_hash_spawn(
    argv_json: ?[*]const u8,
    argv_len: usize,
    env_json: ?[*]const u8,
    env_len: usize,
    replace_env: u8,
    out_hash: ?[*]u8,
    out_hash_len: usize,
) callconv(.c) i32 {
    if (out_hash == null or out_hash_len < 32) return invalid_status;
    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;
    const env = spawnEnv(arena.allocator(), slice(env_json, env_len), replace_env != 0) catch
        return invalid_status;
    const digest = policy.hashSpawn(argv, env.pairs, env.replace);
    @memcpy(out_hash.?[0..32], &digest);
    return ok_status;
}

/// Strict environment binding, process-wide. When enabled, an authorized spawn
/// that supplies environment overrides requires a token whose operation hash
/// covers them (`remedy_core_policy_hash_spawn`); an argv-only hash is refused.
/// Off by default: callers still minting argv-only hashes keep working until
/// they migrate. Env-less spawns verify identically either way.
export fn remedy_core_policy_env_strict(enabled: u8) callconv(.c) i32 {
    policy.setEnvStrict(enabled != 0);
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

/// `env_json` is the same buffer the spawn will hand to `host.resolveEnvBlock`
/// / `host.resolveEnvMap`, so the expected operation hash is recomputed from
/// the environment the child actually gets; a mismatch is a denial.
fn authorizeLocked(
    arena: std.mem.Allocator,
    argv: []const []const u8,
    cwd: []const u8,
    env_json: []const u8,
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
    const env = try spawnEnv(arena, env_json, false);
    const verifier = &(g_verifier orelse return error.AccessDenied);
    _ = try executor.authorizeProcess(
        verifier,
        &policy.product_default_rules,
        token,
        subject,
        scope,
        argv,
        env.pairs,
        env.replace,
        owner_confirmed,
        now_ms,
        policy.envStrict(),
    );
}

/// Authorize argv for a HostSession open (same gates as authorized spawn).
pub fn authorize(
    argv: []const []const u8,
    cwd: []const u8,
    env_json: []const u8,
    token: []const u8,
    subject: []const u8,
    scope: []const u8,
    owner_confirmed: bool,
    now_ms: u64,
) !void {
    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    lock();
    defer unlock();
    try authorizeLocked(arena.allocator(), argv, cwd, env_json, token, subject, scope, owner_confirmed, now_ms);
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
    if (!is_windows and !is_linux) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    pid_slot.* = 0;
    handle_slot.* = 0;

    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;

    lock();
    const auth_result = authorizeLocked(
        arena.allocator(),
        argv,
        slice(cwd, cwd_len),
        slice(env_json, env_len),
        slice(token, token_len),
        subjectOrDefault(subject, subject_len),
        scopeOrDefault(scope, scope_len),
        owner_confirmed != 0,
        now_ms,
    );
    unlock();
    auth_result catch |err| return authStatus(err);

    const spawned = if (is_windows)
        windows_host.spawnHidden(
            slice(argv_json, argv_len),
            slice(cwd, cwd_len),
            slice(env_json, env_len),
        )
    else
        linux_host.spawnHidden(
            slice(argv_json, argv_len),
            slice(cwd, cwd_len),
            slice(env_json, env_len),
        );
    const result = spawned catch |err| return host.statusOf(err);
    pid_slot.* = result.pid;
    handle_slot.* = result.handle;
    return ok_status;
}

/// Authorize then interactive 3-pipe spawn. Parent owns stdin_write / stdout_read /
/// stderr_read OS handles (Windows HANDLE or POSIX fd as uint64). Process handle
/// is wait/kill/close via process_wait / process_kill_tree / process_close —
/// closing the process handle does not close the pipe ends.
export fn remedy_core_process_spawn_piped_authorized(
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
    out_stdin_write: ?*u64,
    out_stdout_read: ?*u64,
    out_stderr_read: ?*u64,
) callconv(.c) i32 {
    if (!is_windows and !is_linux) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    const stdin_slot = out_stdin_write orelse return invalid_status;
    const stdout_slot = out_stdout_read orelse return invalid_status;
    const stderr_slot = out_stderr_read orelse return invalid_status;
    pid_slot.* = 0;
    handle_slot.* = 0;
    stdin_slot.* = 0;
    stdout_slot.* = 0;
    stderr_slot.* = 0;

    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;

    lock();
    const auth_result = authorizeLocked(
        arena.allocator(),
        argv,
        slice(cwd, cwd_len),
        slice(env_json, env_len),
        slice(token, token_len),
        subjectOrDefault(subject, subject_len),
        scopeOrDefault(scope, scope_len),
        owner_confirmed != 0,
        now_ms,
    );
    unlock();
    auth_result catch |err| return authStatus(err);

    const spawned = if (is_windows)
        windows_host.spawnPiped3(
            slice(argv_json, argv_len),
            slice(cwd, cwd_len),
            slice(env_json, env_len),
        )
    else
        linux_host.spawnPiped3(
            slice(argv_json, argv_len),
            slice(cwd, cwd_len),
            slice(env_json, env_len),
        );
    const result = spawned catch |err| return host.statusOf(err);
    pid_slot.* = result.pid;
    handle_slot.* = result.handle;
    stdin_slot.* = result.stdin_write;
    stdout_slot.* = result.stdout_read;
    stderr_slot.* = result.stderr_read;
    return ok_status;
}

fn deliverCaptureOwned(
    stdout: []u8,
    stderr: []u8,
    out_stdout: ?*?[*]u8,
    out_stdout_len: ?*usize,
    out_stderr: ?*?[*]u8,
    out_stderr_len: ?*usize,
) i32 {
    const so_ptr = out_stdout orelse {
        host.allocator.free(stdout);
        host.allocator.free(stderr);
        return invalid_status;
    };
    const so_len = out_stdout_len orelse {
        host.allocator.free(stdout);
        host.allocator.free(stderr);
        return invalid_status;
    };
    const se_ptr = out_stderr orelse {
        host.allocator.free(stdout);
        host.allocator.free(stderr);
        return invalid_status;
    };
    const se_len = out_stderr_len orelse {
        host.allocator.free(stdout);
        host.allocator.free(stderr);
        return invalid_status;
    };
    so_ptr.* = if (stdout.len == 0) null else stdout.ptr;
    so_len.* = stdout.len;
    se_ptr.* = if (stderr.len == 0) null else stderr.ptr;
    se_len.* = stderr.len;
    if (stdout.len == 0) host.allocator.free(stdout);
    if (stderr.len == 0) host.allocator.free(stderr);
    return ok_status;
}

fn timeoutMs(ms: u64) std.Io.Timeout {
    return .{ .duration = .{
        .raw = std.Io.Duration.fromMilliseconds(@intCast(ms)),
        .clock = .awake,
    } };
}

/// Authorize then one-shot hidden spawn with stdout/stderr capture.
/// Windows: job-object spawn + kill-tree on timeout. Elsewhere: process.runCaptureSoft.
/// timeout_ms 0 defaults to 60000. On timeout: exit_code=1, timed_out=1 (Python signal-cli parity).
/// Caller frees stdout/stderr with remedy_core_free (NULL/0 when empty).
export fn remedy_core_process_exec_capture_authorized(
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
    timeout_ms: u32,
    out_exit_code: ?*u32,
    out_timed_out: ?*u8,
    out_stdout: ?*?[*]u8,
    out_stdout_len: ?*usize,
    out_stderr: ?*?[*]u8,
    out_stderr_len: ?*usize,
) callconv(.c) i32 {
    const exit_slot = out_exit_code orelse return invalid_status;
    const timed_slot = out_timed_out orelse return invalid_status;
    exit_slot.* = 1;
    timed_slot.* = 0;
    if (out_stdout) |p| p.* = null;
    if (out_stdout_len) |p| p.* = 0;
    if (out_stderr) |p| p.* = null;
    if (out_stderr_len) |p| p.* = 0;

    var arena = std.heap.ArenaAllocator.init(host.allocator);
    defer arena.deinit();
    const argv = host.parseArgv(arena.allocator(), slice(argv_json, argv_len)) catch return invalid_status;

    lock();
    const auth_result = authorizeLocked(
        arena.allocator(),
        argv,
        slice(cwd, cwd_len),
        slice(env_json, env_len),
        slice(token, token_len),
        subjectOrDefault(subject, subject_len),
        scopeOrDefault(scope, scope_len),
        owner_confirmed != 0,
        now_ms,
    );
    unlock();
    auth_result catch |err| return authStatus(err);

    const budget: u32 = if (timeout_ms == 0) 60_000 else timeout_ms;

    if (is_windows) {
        const captured = windows_host.execCapture(
            slice(argv_json, argv_len),
            slice(cwd, cwd_len),
            slice(env_json, env_len),
            budget,
            process.max_output_bytes,
        ) catch |err| return host.statusOf(err);
        exit_slot.* = captured.exit_code;
        timed_slot.* = @intFromBool(captured.timed_out);
        return deliverCaptureOwned(
            captured.stdout,
            captured.stderr,
            out_stdout,
            out_stdout_len,
            out_stderr,
            out_stderr_len,
        );
    } else {
        // Portable path: soft capture. init_single_threaded uses a failing
        // allocator and OOMs on spawn — use a real GPA like shell_chain.
        // cwd + env must reach runCaptureSoft (parity with Windows execCapture
        // and shell_chain); dropping them made Linux nested pytest / host_run
        // inherit the parent cwd.
        var env_map_storage: ?std.process.Environ.Map = null;
        defer if (env_map_storage) |*m| m.deinit();
        const env_map_ptr: ?*const std.process.Environ.Map = blk: {
            const pairs = (host.parseEnv(arena.allocator(), slice(env_json, env_len)) catch
                return invalid_status) orelse break :blk null;
            var map = std.process.Environ.Map.init(host.allocator);
            errdefer map.deinit();
            for (pairs) |pair| {
                map.put(pair.key, pair.value) catch return failed_status;
            }
            env_map_storage = map;
            break :blk &env_map_storage.?;
        };
        const parent_env: std.process.Environ = if (is_windows)
            .{ .block = .global }
        else
            .{ .block = .empty };
        var threaded = std.Io.Threaded.init(host.allocator, .{ .environ = parent_env });
        defer threaded.deinit();
        const io = threaded.io();
        const soft = process.runCaptureSoft(
            host.allocator,
            io,
            capability.Set.one(.process_spawn),
            .{
                .argv = argv,
                .timeout = timeoutMs(budget),
                .cwd = slice(cwd, cwd_len),
                .environ_map = env_map_ptr,
            },
        ) catch |err| return switch (err) {
            error.InvalidArguments => invalid_status,
            error.AccessDenied => denied_status,
            else => failed_status,
        };
        if (soft.timed_out) {
            exit_slot.* = 1;
            timed_slot.* = 1;
        } else {
            exit_slot.* = soft.exit_code;
            timed_slot.* = 0;
        }
        return deliverCaptureOwned(
            soft.stdout,
            soft.stderr,
            out_stdout,
            out_stdout_len,
            out_stderr,
            out_stderr_len,
        );
    }
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
        arena.allocator(),
        argv,
        slice(cwd, cwd_len),
        slice(env_json, env_len),
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
        &.{},
        false,
        false,
        1500,
        true,
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
            &.{},
            false,
            false,
            1500,
            true,
        ),
    );
}

test "exec capture authorized echoes through job/soft path" {
    if (builtin.os.tag != .windows and builtin.os.tag != .linux) return error.SkipZigTest;

    const key = [_]u8{0xA5} ** Hmac.key_length;
    try std.testing.expectEqual(ok_status, remedy_core_security_set_signing_key(&key, key.len));
    defer _ = remedy_core_security_clear_signing_key();

    const argv: []const u8 = if (builtin.os.tag == .windows)
        "[\"C:\\\\Windows\\\\System32\\\\cmd.exe\",\"/d\",\"/c\",\"echo remedy-capture\"]"
    else
        "[\"/bin/sh\",\"-c\",\"printf remedy-capture\"]";
    const op = policy.hashArguments(if (builtin.os.tag == .windows)
        &.{ "C:\\Windows\\System32\\cmd.exe", "/d", "/c", "echo remedy-capture" }
    else
        &.{ "/bin/sh", "-c", "printf remedy-capture" });
    var token_buf: [security.token_size]u8 = undefined;
    try std.testing.expectEqual(ok_status, remedy_core_capability_issue(
        default_subject.ptr,
        default_subject.len,
        default_scope.ptr,
        default_scope.len,
        &op,
        op.len,
        capability.Set.one(.process_spawn).bits,
        1000,
        60_000,
        &([_]u8{0x22} ** 16),
        16,
        &token_buf,
        token_buf.len,
    ));

    var exit_code: u32 = 99;
    var timed_out: u8 = 1;
    var out_stdout: ?[*]u8 = null;
    var out_stdout_len: usize = 0;
    var out_stderr: ?[*]u8 = null;
    var out_stderr_len: usize = 0;
    const st = remedy_core_process_exec_capture_authorized(
        argv.ptr,
        argv.len,
        null,
        0,
        null,
        0,
        &token_buf,
        token_buf.len,
        null,
        0,
        null,
        0,
        0,
        1500,
        15_000,
        &exit_code,
        &timed_out,
        &out_stdout,
        &out_stdout_len,
        &out_stderr,
        &out_stderr_len,
    );
    defer if (out_stdout) |p| host.allocator.free(p[0..out_stdout_len]);
    defer if (out_stderr) |p| host.allocator.free(p[0..out_stderr_len]);
    try std.testing.expectEqual(ok_status, st);
    try std.testing.expectEqual(@as(u32, 0), exit_code);
    try std.testing.expectEqual(@as(u8, 0), timed_out);
    try std.testing.expect(out_stdout_len > 0);
    try std.testing.expect(std.mem.indexOf(u8, out_stdout.?[0..out_stdout_len], "remedy-capture") != null);
}

test "exec capture authorized honors cwd on linux soft path" {
    // Regression: Linux used to authorize cwd then drop it before runCaptureSoft,
    // so nested pytest / host_run inherited the parent cwd (repo root under WSL).
    if (builtin.os.tag != .linux) return error.SkipZigTest;

    const key = [_]u8{0xB7} ** Hmac.key_length;
    try std.testing.expectEqual(ok_status, remedy_core_security_set_signing_key(&key, key.len));
    defer _ = remedy_core_security_clear_signing_key();

    const io = std.testing.io;
    var tmp = std.testing.tmpDir(.{});
    defer tmp.cleanup();
    try tmp.dir.writeFile(io, .{ .sub_path = "marker.txt", .data = "cwd-ok" });
    var path_buf: [std.fs.max_path_bytes]u8 = undefined;
    const abs_len = try tmp.dir.realPath(io, &path_buf);
    const abs = path_buf[0..abs_len];

    const argv = "[\"/bin/sh\",\"-c\",\"pwd; ls\"]";
    const op = policy.hashArguments(&.{ "/bin/sh", "-c", "pwd; ls" });
    var token_buf: [security.token_size]u8 = undefined;
    try std.testing.expectEqual(ok_status, remedy_core_capability_issue(
        default_subject.ptr,
        default_subject.len,
        default_scope.ptr,
        default_scope.len,
        &op,
        op.len,
        capability.Set.one(.process_spawn).bits,
        1000,
        60_000,
        &([_]u8{0x33} ** 16),
        16,
        &token_buf,
        token_buf.len,
    ));

    var exit_code: u32 = 99;
    var timed_out: u8 = 1;
    var out_stdout: ?[*]u8 = null;
    var out_stdout_len: usize = 0;
    var out_stderr: ?[*]u8 = null;
    var out_stderr_len: usize = 0;
    const st = remedy_core_process_exec_capture_authorized(
        argv.ptr,
        argv.len,
        abs.ptr,
        abs.len,
        null,
        0,
        &token_buf,
        token_buf.len,
        null,
        0,
        null,
        0,
        0,
        1500,
        15_000,
        &exit_code,
        &timed_out,
        &out_stdout,
        &out_stdout_len,
        &out_stderr,
        &out_stderr_len,
    );
    defer if (out_stdout) |p| host.allocator.free(p[0..out_stdout_len]);
    defer if (out_stderr) |p| host.allocator.free(p[0..out_stderr_len]);
    try std.testing.expectEqual(ok_status, st);
    try std.testing.expectEqual(@as(u32, 0), exit_code);
    try std.testing.expectEqual(@as(u8, 0), timed_out);
    const out = out_stdout.?[0..out_stdout_len];
    try std.testing.expect(std.mem.indexOf(u8, out, abs) != null);
    try std.testing.expect(std.mem.indexOf(u8, out, "marker.txt") != null);
}

fn hashSpawnAbi(argv_json: []const u8, env_json: []const u8, replace_env: u8) ![32]u8 {
    var digest: [32]u8 = undefined;
    try std.testing.expectEqual(ok_status, remedy_core_policy_hash_spawn(
        argv_json.ptr,
        argv_json.len,
        if (env_json.len == 0) null else env_json.ptr,
        env_json.len,
        replace_env,
        &digest,
        digest.len,
    ));
    return digest;
}

fn issueSpawnToken(operation_hash: [32]u8, nonce_byte: u8) ![security.token_size]u8 {
    var token: [security.token_size]u8 = undefined;
    try std.testing.expectEqual(ok_status, remedy_core_capability_issue(
        default_subject.ptr,
        default_subject.len,
        default_scope.ptr,
        default_scope.len,
        &operation_hash,
        operation_hash.len,
        capability.Set.one(.process_spawn).bits,
        1000,
        60_000,
        &([_]u8{nonce_byte} ** 16),
        16,
        &token,
        token.len,
    ));
    return token;
}

fn execCaptureAbi(argv_json: []const u8, env_json: []const u8, token: []const u8) i32 {
    var exit_code: u32 = 99;
    var timed_out: u8 = 1;
    var out_stdout: ?[*]u8 = null;
    var out_stdout_len: usize = 0;
    var out_stderr: ?[*]u8 = null;
    var out_stderr_len: usize = 0;
    const status = remedy_core_process_exec_capture_authorized(
        argv_json.ptr,
        argv_json.len,
        null,
        0,
        if (env_json.len == 0) null else env_json.ptr,
        env_json.len,
        token.ptr,
        token.len,
        null,
        0,
        null,
        0,
        0,
        1500,
        15_000,
        &exit_code,
        &timed_out,
        &out_stdout,
        &out_stdout_len,
        &out_stderr,
        &out_stderr_len,
    );
    if (out_stdout) |p| host.allocator.free(p[0..out_stdout_len]);
    if (out_stderr) |p| host.allocator.free(p[0..out_stderr_len]);
    return status;
}

test "spawn hash export matches the argv-only export when no env is supplied" {
    const argv_json: []const u8 = "[\"/opt/remedy/tool\",\"--flag\"]";
    var argv_only: [32]u8 = undefined;
    try std.testing.expectEqual(ok_status, remedy_core_policy_hash_argv(
        argv_json.ptr,
        argv_json.len,
        &argv_only,
        argv_only.len,
    ));
    // Env-less tokens stay byte-identical, so existing issuers are unaffected.
    try std.testing.expectEqualSlices(u8, &argv_only, &try hashSpawnAbi(argv_json, "", 0));
    try std.testing.expectEqualSlices(u8, &argv_only, &try hashSpawnAbi(argv_json, "null", 0));
    // Supplying an environment, or asking for replacement, changes the hash.
    const with_env = try hashSpawnAbi(argv_json, "{\"REMEDY_ENV_BIND\":\"a\"}", 0);
    try std.testing.expect(!std.mem.eql(u8, &argv_only, &with_env));
    try std.testing.expect(!std.mem.eql(u8, &with_env, &try hashSpawnAbi(argv_json, "{\"REMEDY_ENV_BIND\":\"b\"}", 0)));
    try std.testing.expect(!std.mem.eql(u8, &argv_only, &try hashSpawnAbi(argv_json, "", 1)));
    // The wrapper shape and the out-of-band flag agree.
    try std.testing.expectEqualSlices(
        u8,
        &try hashSpawnAbi(argv_json, "{\"REMEDY_ENV_BIND\":\"a\"}", 1),
        &try hashSpawnAbi(argv_json, "{\"env\":{\"REMEDY_ENV_BIND\":\"a\"},\"replace_env\":true}", 0),
    );
}

test "authorized spawn refuses a token minted for a different environment" {
    if (builtin.os.tag != .windows and builtin.os.tag != .linux) return error.SkipZigTest;

    const key = [_]u8{0xC3} ** Hmac.key_length;
    try std.testing.expectEqual(ok_status, remedy_core_security_set_signing_key(&key, key.len));
    defer _ = remedy_core_security_clear_signing_key();
    defer _ = remedy_core_policy_env_strict(0);

    const argv_json: []const u8 = if (builtin.os.tag == .windows)
        "[\"C:\\\\Windows\\\\System32\\\\cmd.exe\",\"/d\",\"/c\",\"echo remedy-env-bind\"]"
    else
        "[\"/bin/sh\",\"-c\",\"printf remedy-env-bind\"]";
    const env_a = "{\"REMEDY_ENV_BIND\":\"a\"}";
    const env_b = "{\"REMEDY_ENV_BIND\":\"b\"}";

    // Minted for argv + env A, spent with env B: the verifier recomputes the
    // hash from the environment it received, so the replay is denied.
    const bound_a = try issueSpawnToken(try hashSpawnAbi(argv_json, env_a, 0), 0x41);
    try std.testing.expectEqual(denied_status, execCaptureAbi(argv_json, env_b, &bound_a));
    // Dropping the environment entirely is the same replay and also denied.
    try std.testing.expectEqual(denied_status, execCaptureAbi(argv_json, "", &bound_a));
    // The nonce survives a denial, so the honest spawn still works.
    try std.testing.expectEqual(ok_status, execCaptureAbi(argv_json, env_a, &bound_a));

    // An env-less token spawns env-less exactly as before the env binding.
    const plain = try issueSpawnToken(try hashSpawnAbi(argv_json, "", 0), 0x42);
    try std.testing.expectEqual(ok_status, execCaptureAbi(argv_json, "", &plain));

    // Argv-only token plus an environment: accepted while callers migrate,
    // refused once strict binding is on.
    const legacy = try issueSpawnToken(try hashSpawnAbi(argv_json, "", 0), 0x43);
    try std.testing.expectEqual(ok_status, execCaptureAbi(argv_json, env_a, &legacy));
    try std.testing.expectEqual(ok_status, remedy_core_policy_env_strict(1));
    const legacy_strict = try issueSpawnToken(try hashSpawnAbi(argv_json, "", 0), 0x44);
    try std.testing.expectEqual(denied_status, execCaptureAbi(argv_json, env_a, &legacy_strict));
    const bound_strict = try issueSpawnToken(try hashSpawnAbi(argv_json, env_a, 0), 0x45);
    try std.testing.expectEqual(ok_status, execCaptureAbi(argv_json, env_a, &bound_strict));
}
