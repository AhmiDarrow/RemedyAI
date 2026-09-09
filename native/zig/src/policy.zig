const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");
const process = @import("process.zig");
const host = @import("host.zig");
const shell_ir = @import("shell_ir.zig");
const Sha256 = std.crypto.hash.sha2.Sha256;

pub const Decision = enum(u8) { allow, ask, deny };
pub const Reason = enum(u8) {
    approved,
    owner_checkpoint,
    malformed,
    not_allowlisted,
    argument_not_allowed,
    dangerous,
};

pub const ProcessRule = struct {
    executable: []const u8,
    /// Every argument after argv[0] must start with one of these prefixes.
    /// An empty list means the executable accepts no additional arguments,
    /// unless `allow_any_arguments` is set.
    argument_prefixes: []const []const u8 = &.{},
    /// When true, arguments are not constrained by `argument_prefixes`.
    allow_any_arguments: bool = false,
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

/// Basename denylist shared with Python `check_dangerous_command` (privilege /
/// disk / Windows system tools). Always denies before allowlist matching.
pub const dangerous_basenames = [_][]const u8{
    "sudo",     "su",      "chmod",   "chown",     "mkfs",     "dd",
    "fdisk",    "passwd",  "useradd", "usermod",   "groupadd", "reg",
    "takeown",  "icacls",  "net",     "wmic",      "sc",       "schtasks",
    "vssadmin", "bcdedit", "wevtutil","diskpart",  "cipher",   "format",
    "shutdown", "reboot",
};

/// Product default: any non-dangerous absolute executable, any arguments.
/// Capability tokens remain the spawn gate; this is not a toy allowlist.
pub const product_default_rules = [_]ProcessRule{
    .{ .executable = "*", .allow_any_arguments = true },
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
    if (isDangerousProcess(argv)) return .{
        .decision = .deny,
        .reason = .dangerous,
        .right = .process_spawn,
        .timestamp_ms = timestamp_ms,
        .operation_hash = operation_hash,
        .requires_owner_proof = false,
    };
    for (rules) |rule| {
        if (!executableMatches(rule.executable, argv[0])) continue;
        if (!rule.allow_any_arguments) {
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

// ---------------------------------------------------------------------------
// Dangerous-command classifier
// ---------------------------------------------------------------------------

/// Shell wrappers whose argv[0] is not the real privilege binary.
const shell_wrappers = [_][]const u8{
    "bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh", "env", "nice", "nohup", "sudo", "doas", "xargs",
};

const powershell_names = [_][]const u8{ "powershell", "pwsh" };

/// Nested privilege / wipe tokens inside `cmd /c` / `pwsh -Command` payloads
/// (basename denylist alone is not enough when argv[0] is the shell).
const nested_privilege_needles = [_][]const u8{
    "reg add",     "reg delete",  "reg import", "takeown",   "icacls",
    "net user",    "net localgroup", "wmic ",    "sc create", "sc delete",
    "schtasks /create", "vssadmin", "bcdedit",   "diskpart",  "format ",
    "shutdown /",  "shutdown -",  "mkfs.",      "mkfs ",     "useradd ",
    "usermod ",    "passwd ",
};

/// Scratch allocator for classifier joins; the classifier never hands memory
/// to callers, so the shared host allocator is used directly.
const scratch_allocator: std.mem.Allocator = std.heap.smp_allocator;

/// Joined argv longer than this is denied outright: a payload the classifier
/// cannot scan completely is treated as dangerous rather than truncated.
pub const max_classifier_bytes: usize = 4 * 1024 * 1024;

/// Nesting depth for encoded payloads that themselves contain an encoded
/// PowerShell invocation.
const max_encoded_depth: u8 = 3;

pub fn isDangerousProcess(argv: []const []const u8) bool {
    if (argv.len == 0) return false;
    const base = executableBasename(argv[0]);
    for (dangerous_basenames) |name| {
        if (eqlIgnoreCase(base, name)) {
            if (eqlIgnoreCase(base, "chmod") and isChmodPlusX(argv)) return false;
            return true;
        }
    }
    // Nested privilege inside shell wrappers (Python check_dangerous_command).
    var is_wrapper = false;
    for (shell_wrappers) |name| {
        if (eqlIgnoreCase(base, name)) {
            is_wrapper = true;
            break;
        }
    }
    if (!is_wrapper and argv.len < 2) return false;
    // Heap join of the full argv: an allocation failure or an over-long
    // payload is a deny, never a silently truncated scan.
    const text = joinNormalized(scratch_allocator, argv[1..]) catch return true;
    defer scratch_allocator.free(text);
    if (containsAnyNeedle(text)) return true;
    var is_powershell = false;
    for (powershell_names) |name| {
        if (eqlIgnoreCase(base, name)) {
            is_powershell = true;
            break;
        }
    }
    if (is_powershell or shell_ir.isEncodedPowershell(text)) {
        return encodedPayloadDangerous(scratch_allocator, text, 0);
    }
    return false;
}

fn containsAnyNeedle(text: []const u8) bool {
    for (nested_privilege_needles) |needle| {
        if (containsIgnoreCase(text, needle)) return true;
    }
    return false;
}

fn isClassifierSpace(c: u8) bool {
    return c == ' ' or c == '\t' or c == '\r' or c == '\n' or c == 0x0b or c == 0x0c;
}

/// Append `text` with whitespace runs collapsed to one space and cmd caret
/// escapes (`^`) removed, so `re^g   add` and `reg\tadd` match `reg add`.
fn appendNormalized(out: *std.ArrayList(u8), gpa: std.mem.Allocator, text: []const u8) error{ OutOfMemory, TooLong }!void {
    var pending_space = false;
    for (text) |c| {
        if (c == '^') continue;
        if (isClassifierSpace(c)) {
            pending_space = true;
            continue;
        }
        if (pending_space and out.items.len > 0) try out.append(gpa, ' ');
        pending_space = false;
        try out.append(gpa, c);
        if (out.items.len > max_classifier_bytes) return error.TooLong;
    }
    if (pending_space and out.items.len > 0) try out.append(gpa, ' ');
}

/// Join arguments into one normalized, heap-allocated scan buffer.
pub fn joinNormalized(gpa: std.mem.Allocator, args: []const []const u8) error{ OutOfMemory, TooLong }![]u8 {
    var out: std.ArrayList(u8) = .empty;
    errdefer out.deinit(gpa);
    for (args) |arg| {
        if (out.items.len > 0 and out.items[out.items.len - 1] != ' ') try out.append(gpa, ' ');
        try appendNormalized(&out, gpa, arg);
    }
    return out.toOwnedSlice(gpa);
}

/// Normalize free text (an already decoded payload) the same way.
fn normalizeText(gpa: std.mem.Allocator, text: []const u8) error{ OutOfMemory, TooLong }![]u8 {
    return joinNormalized(gpa, &.{text});
}

fn stripQuotes(token: []const u8) []const u8 {
    var t = token;
    while (t.len > 0 and (t[0] == '"' or t[0] == '\'')) t = t[1..];
    while (t.len > 0 and (t[t.len - 1] == '"' or t[t.len - 1] == '\'')) t = t[0 .. t.len - 1];
    return t;
}

/// PowerShell accepts `-EncodedCommand`, `-ec`, `-e`, and any unambiguous
/// prefix of EncodedCommand (`-en`, `-enc`, ...). `/` is accepted as the
/// switch character as well.
pub fn isEncodedCommandFlag(token: []const u8) bool {
    if (token.len < 2) return false;
    if (token[0] != '-' and token[0] != '/') return false;
    var body = token[1..];
    if (std.mem.indexOfScalar(u8, body, ':')) |i| body = body[0..i];
    if (body.len == 0 or body.len > "encodedcommand".len) return false;
    if (eqlIgnoreCase(body, "ec")) return true;
    return eqlIgnoreCase(body, "encodedcommand"[0..body.len]);
}

const DecodeError = error{ InvalidPayload, OutOfMemory };

/// Decode a `-EncodedCommand` operand: base64 (padded or not) of UTF-16LE.
pub fn decodeEncodedCommand(gpa: std.mem.Allocator, operand: []const u8) DecodeError![]u8 {
    const payload = stripQuotes(operand);
    if (payload.len == 0) return error.InvalidPayload;
    const size = std.base64.standard.Decoder.calcSizeForSlice(payload) catch
        (std.base64.standard_no_pad.Decoder.calcSizeForSlice(payload) catch return error.InvalidPayload);
    const raw = try gpa.alloc(u8, size);
    defer gpa.free(raw);
    std.base64.standard.Decoder.decode(raw, payload) catch {
        std.base64.standard_no_pad.Decoder.decode(raw, payload) catch return error.InvalidPayload;
    };
    if (raw.len % 2 != 0) return error.InvalidPayload;
    const units = try gpa.alloc(u16, raw.len / 2);
    defer gpa.free(units);
    var i: usize = 0;
    while (i < units.len) : (i += 1) {
        units[i] = std.mem.readInt(u16, raw[i * 2 ..][0..2], .little);
    }
    return std.unicode.wtf16LeToWtf8Alloc(gpa, units) catch return error.OutOfMemory;
}

/// Scan `text` for encoded PowerShell operands; decode and needle-match the
/// plaintext. A flag whose operand is missing or does not decode is a deny.
fn encodedPayloadDangerous(gpa: std.mem.Allocator, text: []const u8, depth: u8) bool {
    var it = std.mem.tokenizeScalar(u8, text, ' ');
    var found_flag = false;
    while (it.next()) |raw_token| {
        const token = stripQuotes(raw_token);
        if (!isEncodedCommandFlag(token)) continue;
        found_flag = true;
        const operand = it.next() orelse return true;
        const decoded = decodeEncodedCommand(gpa, operand) catch return true;
        defer gpa.free(decoded);
        const normalized = normalizeText(gpa, decoded) catch return true;
        defer gpa.free(normalized);
        if (containsAnyNeedle(normalized)) return true;
        const nested_dangerous = for (dangerous_basenames) |name| {
            if (startsWithWord(normalized, name)) break true;
        } else false;
        if (nested_dangerous) return true;
        if (shell_ir.isEncodedPowershell(normalized)) {
            if (depth + 1 >= max_encoded_depth) return true;
            if (encodedPayloadDangerous(gpa, normalized, depth + 1)) return true;
        }
    }
    // The text was flagged as encoded PowerShell but no operand could be
    // located in a form this classifier understands: fail closed.
    if (!found_flag and shell_ir.isEncodedPowershell(text)) return true;
    return false;
}

fn startsWithWord(text: []const u8, word: []const u8) bool {
    if (text.len < word.len) return false;
    if (!eqlIgnoreCase(text[0..word.len], word)) return false;
    return text.len == word.len or text[word.len] == ' ';
}

fn containsIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (needle.len == 0 or hay.len < needle.len) return false;
    var i: usize = 0;
    while (i + needle.len <= hay.len) : (i += 1) {
        if (eqlIgnoreCase(hay[i .. i + needle.len], needle)) return true;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Operation hash (argv, optionally bound to the supplied environment)
// ---------------------------------------------------------------------------

pub fn hashArguments(argv: []const []const u8) [Sha256.digest_length]u8 {
    var hasher = Sha256.init(.{});
    hashArgv(&hasher, argv);
    var digest: [Sha256.digest_length]u8 = undefined;
    hasher.final(&digest);
    return digest;
}

fn hashArgv(hasher: *Sha256, argv: []const []const u8) void {
    var length: [8]u8 = undefined;
    for (argv) |argument| {
        std.mem.writeInt(u64, &length, argument.len, .little);
        hasher.update(&length);
        hasher.update(argument);
    }
}

const env_hash_domain = "\x00remedy.spawn.env.v1\x00";

/// Operation hash for a spawn: argv plus the caller-supplied environment
/// overrides (sorted pairs and the replace flag). With no supplied
/// environment this is exactly `hashArguments(argv)`, so env-less tokens are
/// unchanged.
pub fn hashSpawn(argv: []const []const u8, env: []const host.EnvPair, replace_env: bool) [Sha256.digest_length]u8 {
    if (env.len == 0 and !replace_env) return hashArguments(argv);
    var hasher = Sha256.init(.{});
    hashArgv(&hasher, argv);
    hasher.update(env_hash_domain);
    hasher.update(&[_]u8{@intFromBool(replace_env)});
    var length: [8]u8 = undefined;
    std.mem.writeInt(u64, &length, env.len, .little);
    hasher.update(&length);
    for (env) |pair| {
        std.mem.writeInt(u64, &length, pair.key.len, .little);
        hasher.update(&length);
        hasher.update(pair.key);
        std.mem.writeInt(u64, &length, pair.value.len, .little);
        hasher.update(&length);
        hasher.update(pair.value);
    }
    var digest: [Sha256.digest_length]u8 = undefined;
    hasher.final(&digest);
    return digest;
}

// ---------------------------------------------------------------------------
// Environment policy
// ---------------------------------------------------------------------------

/// Loader / hook variables a caller may never supply: each one changes what
/// code runs before or instead of the approved argv.
pub const env_denied_exact = [_][]const u8{
    "LD_PRELOAD",      "LD_LIBRARY_PATH", "LD_AUDIT",     "GIT_SSH_COMMAND", "GIT_EXEC_PATH",
    "BROWSER",         "PYTHONSTARTUP",   "PERL5OPT",     "NODE_OPTIONS",    "RUBYOPT",
    "BASH_ENV",        "ENV",             "PROMPT_COMMAND",
};

pub const env_denied_prefixes = [_][]const u8{"DYLD_"};

/// System identity variables: a supplied value is accepted only when it is a
/// no-op (equal to the parent's value); overriding them redirects `cmd`,
/// executable lookup or the Windows directory itself.
pub const env_pinned_to_parent = [_][]const u8{ "COMSPEC", "PATHEXT", "SYSTEMROOT", "WINDIR" };

/// Not yet reachable from a spawn: see the note on `setEnvStrict`. Kept
/// because the rule is correct and the wiring is a scheduled follow-up, not an
/// abandoned idea — `checkEnv` is exercised by this file's tests.
pub const EnvPolicy = struct {
    /// Capability subject of the caller (`agent:remedy` by default). Subjects
    /// with the `runtime:` prefix are the product runtime spawning its own
    /// workers and may set PYTHONPATH.
    subject: []const u8 = "",
    /// Strict mode also requires the runtime subject for PYTHONPATH.
    strict: bool = false,
};

pub const runtime_subject_prefix = "runtime:";

var g_env_strict = std.atomic.Value(bool).init(false);

/// Strict environment binding: tokens must carry the env-bound operation hash
/// (see `remedy_core_policy_hash_spawn`). Off by default while callers migrate.
///
/// It does NOT gate PYTHONPATH: `checkEnv` holds that rule but no spawn path
/// calls it yet, because the product runtime sets PYTHONPATH when it spawns
/// its own Python worker and would have to carry a `runtime:` subject first.
/// Until then the model-facing boundary refuses interpreter-path variables
/// outright (`modelInterpreterPathEnv` in native/go/tools/zig_tools.go).
pub fn setEnvStrict(strict: bool) void {
    g_env_strict.store(strict, .release);
}

pub fn envStrict() bool {
    return g_env_strict.load(.acquire);
}

pub const EnvError = error{EnvDenied};

fn envKeyLoaderDenied(key: []const u8) bool {
    for (env_denied_exact) |name| {
        if (eqlIgnoreCase(key, name)) return true;
    }
    for (env_denied_prefixes) |prefix| {
        if (key.len >= prefix.len and eqlIgnoreCase(key[0..prefix.len], prefix)) return true;
    }
    return false;
}

fn envKeyPinned(key: []const u8) bool {
    for (env_pinned_to_parent) |name| {
        if (eqlIgnoreCase(key, name)) return true;
    }
    return false;
}

fn equalsParentValue(key: []const u8, value: []const u8) bool {
    const parent = host.parentEnvGet(scratch_allocator, key) orelse return false;
    defer scratch_allocator.free(parent);
    if (builtin.os.tag == .windows) return eqlIgnoreCase(parent, value);
    return std.mem.eql(u8, parent, value);
}

/// Subject-independent part of the environment policy. Every spawn primitive
/// applies this to the supplied overrides before building the child block.
pub fn checkEnvBase(pairs: []const host.EnvPair) EnvError!void {
    for (pairs) |pair| {
        if (envKeyLoaderDenied(pair.key)) return error.EnvDenied;
        if (envKeyPinned(pair.key) and !equalsParentValue(pair.key, pair.value)) return error.EnvDenied;
    }
}

/// Full environment policy for an export entry point that knows its caller.
pub fn checkEnv(pairs: []const host.EnvPair, env_policy: EnvPolicy) EnvError!void {
    try checkEnvBase(pairs);
    for (pairs) |pair| {
        if (!eqlIgnoreCase(pair.key, "PYTHONPATH")) continue;
        if (equalsParentValue(pair.key, pair.value)) continue;
        if (std.mem.startsWith(u8, env_policy.subject, runtime_subject_prefix)) continue;
        if (env_policy.strict) return error.EnvDenied;
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn executableMatches(rule_executable: []const u8, argv0: []const u8) bool {
    if (std.mem.eql(u8, rule_executable, "*")) return true;
    if (std.mem.eql(u8, rule_executable, argv0)) return true;
    if (std.fs.path.isAbsolute(rule_executable)) return false;
    if (std.mem.indexOfScalar(u8, rule_executable, '/') != null) return false;
    if (std.mem.indexOfScalar(u8, rule_executable, '\\') != null) return false;
    return eqlIgnoreCase(executableBasename(argv0), rule_executable);
}

fn executableBasename(path: []const u8) []const u8 {
    const base = std.fs.path.basename(path);
    if (builtin.os.tag == .windows and base.len >= 4) {
        if (eqlIgnoreCase(base[base.len - 4 ..], ".exe")) return base[0 .. base.len - 4];
    }
    return base;
}

fn isChmodPlusX(argv: []const []const u8) bool {
    if (argv.len < 2) return false;
    const mode = argv[1];
    return std.mem.eql(u8, mode, "+x") or std.mem.eql(u8, mode, "a+x") or
        std.mem.eql(u8, mode, "u+x") or std.mem.eql(u8, mode, "ug+x") or
        std.mem.eql(u8, mode, "ugo+x");
}

fn eqlIgnoreCase(a: []const u8, b: []const u8) bool {
    return std.ascii.eqlIgnoreCase(a, b);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

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

test "product defaults allow common tools and deny dangerous basenames" {
    const git = if (builtin.os.tag == .windows) "C:\\Program Files\\Git\\cmd\\git.exe" else "/usr/bin/git";
    const pytest_exe = if (builtin.os.tag == .windows) "C:\\Python\\Scripts\\pytest.exe" else "/usr/bin/pytest";
    const pwsh = if (builtin.os.tag == .windows) "C:\\Program Files\\PowerShell\\7\\pwsh.exe" else "/usr/bin/pwsh";
    const sudo = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\sudo.exe" else "/usr/bin/sudo";
    const format = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\format.exe" else "/usr/sbin/mkfs";

    try std.testing.expectEqual(
        Decision.allow,
        evaluateProcess(&product_default_rules, &.{ git, "status" }, false, 1).decision,
    );
    try std.testing.expectEqual(
        Decision.allow,
        evaluateProcess(&product_default_rules, &.{ pytest_exe, "-q" }, false, 2).decision,
    );
    try std.testing.expectEqual(
        Decision.allow,
        evaluateProcess(&product_default_rules, &.{ pwsh, "-File", "C:\\tmp\\run.ps1" }, false, 3).decision,
    );
    try std.testing.expectEqual(
        Reason.dangerous,
        evaluateProcess(&product_default_rules, &.{sudo}, false, 4).reason,
    );
    try std.testing.expectEqual(
        Reason.dangerous,
        evaluateProcess(&product_default_rules, &.{ format, "C:" }, false, 5).reason,
    );
}

test "basename rules match without requiring the absolute path string" {
    const tool = if (builtin.os.tag == .windows) "C:\\Tools\\rg.exe" else "/usr/bin/rg";
    const rules = [_]ProcessRule{.{ .executable = "rg", .allow_any_arguments = true }};
    try std.testing.expectEqual(Decision.allow, evaluateProcess(&rules, &.{ tool, "-n", "foo" }, false, 1).decision);
}

test "nested privilege inside cmd /c is dangerous" {
    const cmd = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/bash";
    const flag = if (builtin.os.tag == .windows) "/c" else "-c";
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, "reg add HKLM\\Software\\Pwn" }));
    try std.testing.expect(!isDangerousProcess(&.{ cmd, flag, "echo hello" }));
}

test "classifier normalizes whitespace and caret escapes and scans the full argv" {
    const cmd = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/bash";
    const flag = if (builtin.os.tag == .windows) "/c" else "-c";
    // Tabs / runs of spaces between the tokens.
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, "reg\t\tadd HKLM\\x" }));
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, "net     user pwn Passw0rd /add" }));
    // cmd caret escapes split the token for a naive scanner.
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, "re^g a^dd HKLM\\x" }));
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, "vss^admin delete shadows" }));
    // Payload beyond the old 4 KB stack window is still scanned.
    const filler = "x" ** 6000;
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, filler, "bcdedit /set testsigning on" }));
    try std.testing.expect(!isDangerousProcess(&.{ cmd, flag, filler, "echo done" }));
    // Argument words that merely resemble needles are still fine.
    try std.testing.expect(!isDangerousProcess(&.{ cmd, flag, "echo formatting text" }));
}

test "classifier denies when the joined argv exceeds the scan cap" {
    const cmd = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/bash";
    const flag = if (builtin.os.tag == .windows) "/c" else "-c";
    const huge = try std.testing.allocator.alloc(u8, max_classifier_bytes + 16);
    defer std.testing.allocator.free(huge);
    @memset(huge, 'a');
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, huge }));
}

fn encodeUtf16Base64(gpa: std.mem.Allocator, text: []const u8) ![]u8 {
    const wide = try std.unicode.utf8ToUtf16LeAlloc(gpa, text);
    defer gpa.free(wide);
    const bytes = std.mem.sliceAsBytes(wide);
    const out = try gpa.alloc(u8, std.base64.standard.Encoder.calcSize(bytes.len));
    _ = std.base64.standard.Encoder.encode(out, bytes);
    return out;
}

test "encoded powershell payloads are decoded before needle matching" {
    const gpa = std.testing.allocator;
    const pwsh = if (builtin.os.tag == .windows) "C:\\Program Files\\PowerShell\\7\\pwsh.exe" else "/usr/bin/pwsh";
    const cmd = if (builtin.os.tag == .windows) "C:\\Windows\\System32\\cmd.exe" else "/bin/bash";
    const flag = if (builtin.os.tag == .windows) "/c" else "-c";

    const bad = try encodeUtf16Base64(gpa, "reg add HKLM\\Software\\Pwn /v x /d y");
    defer gpa.free(bad);
    const good = try encodeUtf16Base64(gpa, "Get-ChildItem -Recurse | Measure-Object");
    defer gpa.free(good);

    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-NoProfile", "-EncodedCommand", bad }));
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-e", bad }));
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-enc", bad }));
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-ec", bad }));
    try std.testing.expect(!isDangerousProcess(&.{ pwsh, "-NoProfile", "-EncodedCommand", good }));
    try std.testing.expect(!isDangerousProcess(&.{ pwsh, "-NoProfile", "-Command", "Get-Date" }));

    // Encoded invocation nested in a cmd wrapper string.
    const nested = try std.fmt.allocPrint(gpa, "powershell -nop -enc {s}", .{bad});
    defer gpa.free(nested);
    try std.testing.expect(isDangerousProcess(&.{ cmd, flag, nested }));
    const nested_good = try std.fmt.allocPrint(gpa, "powershell -nop -enc {s}", .{good});
    defer gpa.free(nested_good);
    try std.testing.expect(!isDangerousProcess(&.{ cmd, flag, nested_good }));

    // A payload that does not decode is denied outright.
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-EncodedCommand", "not*base64*at*all" }));
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-EncodedCommand" }));
    // Odd byte count cannot be UTF-16.
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-EncodedCommand", "YWJj" }));

    // Double encoding: the decoded text itself runs an encoded command.
    const inner = try std.fmt.allocPrint(gpa, "pwsh -e {s}", .{bad});
    defer gpa.free(inner);
    const outer = try encodeUtf16Base64(gpa, inner);
    defer gpa.free(outer);
    try std.testing.expect(isDangerousProcess(&.{ pwsh, "-e", outer }));
}

test "spawn hash binds supplied environment and is argv-only without one" {
    const tool = if (builtin.os.tag == .windows) "C:\\Remedy\\tool.exe" else "/opt/remedy/tool";
    const argv = [_][]const u8{ tool, "--flag" };
    try std.testing.expectEqual(hashArguments(&argv), hashSpawn(&argv, &.{}, false));
    const env_a = [_]host.EnvPair{.{ .key = "A", .value = "1" }};
    const env_b = [_]host.EnvPair{.{ .key = "A", .value = "2" }};
    const with_a = hashSpawn(&argv, &env_a, false);
    try std.testing.expect(!std.mem.eql(u8, &with_a, &hashArguments(&argv)));
    try std.testing.expect(!std.mem.eql(u8, &with_a, &hashSpawn(&argv, &env_b, false)));
    try std.testing.expect(!std.mem.eql(u8, &with_a, &hashSpawn(&argv, &env_a, true)));
    try std.testing.expectEqualSlices(u8, &with_a, &hashSpawn(&argv, &env_a, false));
}

test "environment policy rejects loader hooks and pinned system overrides" {
    const bad_keys = [_][]const u8{
        "LD_PRELOAD",   "ld_preload",  "LD_LIBRARY_PATH", "LD_AUDIT",       "DYLD_INSERT_LIBRARIES",
        "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "BROWSER",     "PYTHONSTARTUP",  "PERL5OPT",
        "NODE_OPTIONS", "RUBYOPT",     "BASH_ENV",        "ENV",            "PROMPT_COMMAND",
    };
    for (bad_keys) |key| {
        const pairs = [_]host.EnvPair{.{ .key = key, .value = "x" }};
        try std.testing.expectError(error.EnvDenied, checkEnvBase(&pairs));
        try std.testing.expectError(error.EnvDenied, checkEnv(&pairs, .{}));
    }
    const pinned = [_]host.EnvPair{.{ .key = "SystemRoot", .value = "C:\\Evil" }};
    try std.testing.expectError(error.EnvDenied, checkEnvBase(&pinned));
    const comspec = [_]host.EnvPair{.{ .key = "COMSPEC", .value = "C:\\Evil\\cmd.exe" }};
    try std.testing.expectError(error.EnvDenied, checkEnvBase(&comspec));
    const pathext = [_]host.EnvPair{.{ .key = "PATHEXT", .value = ".EVIL" }};
    try std.testing.expectError(error.EnvDenied, checkEnvBase(&pathext));

    const benign = [_]host.EnvPair{
        .{ .key = "PYTHONIOENCODING", .value = "utf-8" },
        .{ .key = "RUST_LOG", .value = "debug" },
        .{ .key = "ENVIRONMENT", .value = "dev" },
        .{ .key = "LD_DEBUG_OUTPUT_NOT_LISTED", .value = "x" },
    };
    try checkEnvBase(&benign);
    try checkEnv(&benign, .{ .strict = true });

    // A pinned variable equal to the parent's value is a no-op and allowed.
    if (host.parentEnvGet(std.testing.allocator, "SystemRoot")) |value| {
        defer std.testing.allocator.free(value);
        const same = [_]host.EnvPair{.{ .key = "SYSTEMROOT", .value = value }};
        try checkEnvBase(&same);
    }
}

test "PYTHONPATH is runtime-only in strict mode" {
    const pairs = [_]host.EnvPair{.{ .key = "PYTHONPATH", .value = "C:\\somewhere\\src" }};
    try checkEnv(&pairs, .{ .strict = false });
    try std.testing.expectError(error.EnvDenied, checkEnv(&pairs, .{ .strict = true }));
    try std.testing.expectError(error.EnvDenied, checkEnv(&pairs, .{ .strict = true, .subject = "agent:remedy" }));
    try checkEnv(&pairs, .{ .strict = true, .subject = "runtime:worker" });
    try checkEnvBase(&pairs);
}
