const std = @import("std");
const builtin = @import("builtin");
const capability = @import("capability.zig");
const process = @import("process.zig");
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

pub fn isDangerousProcess(argv: []const []const u8) bool {
    if (argv.len == 0) return false;
    const base = executableBasename(argv[0]);
    for (dangerous_basenames) |name| {
        if (eqlIgnoreCase(base, name)) {
            if (eqlIgnoreCase(base, "chmod") and isChmodPlusX(argv)) return false;
            return true;
        }
    }
    return false;
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
