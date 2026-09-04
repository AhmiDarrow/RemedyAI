//! Classify host/shell failures so the model gets a rewrite, not a wall of stderr.
//! Owns the logic previously in Python `execution/host/diagnose.py`.

const std = @import("std");
const root = @import("root.zig");
const host = @import("host.zig");

const Status = root.Status;
const Error = host.Error;
const allocator = host.allocator;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);

const prompt_markers = [_][]const u8{
    "password:",
    "[y/n]",
    "(y/n)",
    "are you sure",
    "press any key",
    "enter passphrase",
    "overwrite?",
    "confirm",
};

const interactive_tokens = [_][]const u8{
    "read-host",
    "pause",
    "more.com",
    " ssh ",
    "scp ",
    "vim ",
    "nano ",
    "less ",
};

const posix_tools = [_][]const u8{
    "grep",  "head",  "tail", "cat", "ls",  "rm",  "mkdir", "find",
    "test",  "wc",    "rg",   "ripgrep", "awk", "sed",
};

pub const Diagnosis = struct {
    code: []const u8,
    message: []const u8,
    rewritten: []const u8 = "",
    hint: []const u8 = "",
};

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

fn deliverBytes(result: Error![]u8, out_ptr: ?*?[*]u8, out_len: ?*usize) i32 {
    const ptr_slot = out_ptr orelse return invalid_status;
    const len_slot = out_len orelse return invalid_status;
    const bytes = result catch |err| {
        ptr_slot.* = null;
        len_slot.* = 0;
        return host.statusOf(err);
    };
    ptr_slot.* = bytes.ptr;
    len_slot.* = bytes.len;
    return ok_status;
}

fn containsIgnoreCase(hay: []const u8, needle: []const u8) bool {
    if (needle.len == 0 or hay.len < needle.len) return false;
    var i: usize = 0;
    while (i + needle.len <= hay.len) : (i += 1) {
        if (std.ascii.eqlIgnoreCase(hay[i..][0..needle.len], needle)) return true;
    }
    return false;
}

fn looksInteractive(command: []const u8) bool {
    var lower_buf: [512]u8 = undefined;
    const n = @min(command.len, lower_buf.len);
    for (command[0..n], 0..) |c, i| lower_buf[i] = std.ascii.toLower(c);
    const low = lower_buf[0..n];
    for (interactive_tokens) |tok| {
        if (std.mem.indexOf(u8, low, tok) != null) return true;
    }
    return false;
}

fn extractMissing(blob: []const u8, command: []const u8, buf: []u8) []const u8 {
    // "'foo' is not recognized"
    if (std.mem.indexOf(u8, blob, "'")) |start| {
        const after = blob[start + 1 ..];
        if (std.mem.indexOf(u8, after, "'")) |end| {
            const name = after[0..end];
            if (name.len > 0 and name.len < buf.len and std.mem.indexOf(u8, after[end..], "is not recognized") != null) {
                @memcpy(buf[0..name.len], name);
                return buf[0..name.len];
            }
        }
    }
    // "foo: command not found" / "foo: not found"
    var line_it = std.mem.splitScalar(u8, blob, '\n');
    while (line_it.next()) |line| {
        const trimmed = std.mem.trim(u8, line, " \t\r");
        if (std.mem.indexOf(u8, trimmed, ": ")) |colon| {
            const head = trimmed[0..colon];
            const rest = trimmed[colon + 2 ..];
            if (containsIgnoreCase(rest, "not found") or containsIgnoreCase(rest, "command not found")) {
                const name = std.mem.trim(u8, head, " \t'`\"");
                if (name.len > 0 and name.len < buf.len) {
                    @memcpy(buf[0..name.len], name);
                    return buf[0..name.len];
                }
            }
        }
    }
    var tok_it = std.mem.tokenizeAny(u8, std.mem.trim(u8, command, " \t"), " \t");
    if (tok_it.next()) |tok| {
        if (tok.len > 0 and tok.len < buf.len) {
            @memcpy(buf[0..tok.len], tok);
            return buf[0..tok.len];
        }
    }
    return "";
}

fn isPosixTool(name: []const u8) bool {
    for (posix_tools) |t| {
        if (std.ascii.eqlIgnoreCase(name, t)) return true;
    }
    return false;
}

fn notFoundHint(missing: []const u8) []const u8 {
    if (std.ascii.eqlIgnoreCase(missing, "rg") or std.ascii.eqlIgnoreCase(missing, "ripgrep")) {
        return "Use repo_search instead of calling rg via host_run. The bundled ripgrep is for Remedy's search tool.";
    }
    if (std.ascii.eqlIgnoreCase(missing, "wc")) {
        return "'wc' is POSIX. Prefer file_read, or host_run a python one-liner to count lines. The host bridge rewrites `wc -l file` when it can.";
    }
    return "Use host_which to resolve the binary, or host_run with a full path.";
}

fn posixHintMessage(missing: []const u8, buf: []u8) []const u8 {
    if (!isPosixTool(missing)) return notFoundHint(missing);
    if (std.ascii.eqlIgnoreCase(missing, "rg") or std.ascii.eqlIgnoreCase(missing, "ripgrep") or
        std.ascii.eqlIgnoreCase(missing, "wc"))
    {
        return notFoundHint(missing);
    }
    // Match Python: f"'{missing}' is POSIX. Prefer host_mkdir / host_run / repo_search, ..."
    return std.fmt.bufPrint(buf, "'{s}' is POSIX. Prefer host_mkdir / host_run / repo_search, or let the host bridge rewrite the command.", .{missing}) catch notFoundHint(missing);
}

pub fn diagnose(
    arena: std.mem.Allocator,
    command: []const u8,
    stdout: []const u8,
    stderr: []const u8,
    exit_code: i32,
    translated: []const u8,
    timed_out: bool,
    host_name: []const u8,
) Error!Diagnosis {
    const blob = try std.fmt.allocPrint(arena, "{s}\n{s}", .{ stderr, stdout });
    var low_buf: [8192]u8 = undefined;
    const low_n = @min(blob.len, low_buf.len);
    for (blob[0..low_n], 0..) |c, i| low_buf[i] = std.ascii.toLower(c);
    const low = low_buf[0..low_n];
    const cmd = std.mem.trim(u8, command, " \t\r\n");

    if (timed_out) {
        var interactive = looksInteractive(cmd);
        if (!interactive) {
            for (prompt_markers) |m| {
                if (std.mem.indexOf(u8, low, m) != null) {
                    interactive = true;
                    break;
                }
            }
        }
        if (interactive) {
            return .{
                .code = "HOST_INTERACTIVE",
                .message = "Command waited on an interactive prompt and was killed.",
                .hint = "Use non-interactive flags (-y, --yes, --noconfirm) or host_script.",
            };
        }
        return .{
            .code = "HOST_TIMEOUT",
            .message = "Command timed out.",
            .hint = "Raise timeout_seconds, or run a narrower command.",
        };
    }

    const not_recognized = std.mem.indexOf(u8, low, "is not recognized as an internal or external command") != null or
        std.mem.indexOf(u8, low, "command not found") != null or
        (std.mem.indexOf(u8, blob, "'") != null and containsIgnoreCase(blob, "is not recognized"));
    if (not_recognized) {
        var miss_buf: [128]u8 = undefined;
        const missing = extractMissing(blob, cmd, &miss_buf);
        var hint_buf: [256]u8 = undefined;
        const hint = posixHintMessage(missing, &hint_buf);
        const msg = if (missing.len != 0)
            try std.fmt.allocPrint(arena, "Command not found on this host: {s}.", .{missing})
        else
            try arena.dupe(u8, "Command not found on this host: unknown.");
        // Heap-dup hint when it lives in stack buf
        const hint_owned = try arena.dupe(u8, hint);
        return .{
            .code = "HOST_NOT_FOUND",
            .message = msg,
            .rewritten = translated,
            .hint = hint_owned,
        };
    }

    if (std.mem.indexOf(u8, low, "positional parameter cannot be found") != null or
        std.mem.indexOf(u8, low, "a parameter cannot be found") != null)
    {
        return .{
            .code = "HOST_DIALECT",
            .message = "PowerShell rejected POSIX flags (often mkdir -p / rm -rf).",
            .rewritten = translated,
            .hint = "Do not wrap this in powershell.exe. Use host_mkdir or bash_exec (cmd host).",
        };
    }

    if (std.mem.indexOf(u8, low, "parsererror") != null or
        std.mem.indexOf(u8, low, "missing closing") != null or
        std.mem.indexOf(u8, low, "unexpected token") != null)
    {
        return .{
            .code = "HOST_QUOTING",
            .message = "The host shell could not parse the command (quoting).",
            .hint = "Prefer host_run(argv=[...]) or host_script — never nest quotes in a string.",
        };
    }

    if (std.mem.indexOf(u8, low, "execution of scripts is disabled") != null or
        std.mem.indexOf(u8, low, "running scripts is disabled") != null)
    {
        return .{
            .code = "HOST_POLICY",
            .message = "PowerShell script execution policy blocked the file.",
            .hint = "Host bridge runs pwsh -File with -NoProfile. Use host_script(lang=pwsh).",
        };
    }

    if (exit_code != 0 and translated.len != 0 and !std.mem.eql(u8, translated, cmd)) {
        return .{
            .code = "HOST_TRANSLATED_FAIL",
            .message = "Translated POSIX command still failed.",
            .rewritten = translated,
            .hint = "Read stderr, or switch to host_run(argv=...) / host_script.",
        };
    }

    if (exit_code != 0) {
        const msg = try std.fmt.allocPrint(arena, "exit_code={d} on host={s}.", .{ exit_code, if (host_name.len != 0) host_name else "cmd" });
        const rewritten = if (translated.len != 0 and !std.mem.eql(u8, translated, cmd)) translated else "";
        return .{
            .code = "HOST_EXIT",
            .message = msg,
            .rewritten = rewritten,
            .hint = "Read stderr, fix flags/paths/cwd, or use a structured host_* tool.",
        };
    }

    return .{ .code = "HOST_OK", .message = "ok" };
}

const DiagnoseIn = struct {
    command: []const u8 = "",
    stdout: []const u8 = "",
    stderr: []const u8 = "",
    exit_code: i32 = 1,
    translated: []const u8 = "",
    timed_out: bool = false,
    host: []const u8 = "cmd",
};

fn diagnoseToOwnedJson(input: []const u8) Error![]u8 {
    var arena_state = std.heap.ArenaAllocator.init(allocator);
    defer arena_state.deinit();
    const arena = arena_state.allocator();

    const parsed = std.json.parseFromSliceLeaky(DiagnoseIn, arena, input, .{
        .ignore_unknown_fields = true,
        .allocate = .alloc_if_needed,
    }) catch return error.InvalidArgument;

    const d = try diagnose(
        arena,
        parsed.command,
        parsed.stdout,
        parsed.stderr,
        parsed.exit_code,
        parsed.translated,
        parsed.timed_out,
        parsed.host,
    );
    // jsonAlloc uses host.allocator; strings must outlive arena — dup into result via stringify of owned copies
    const code = try allocator.dupe(u8, d.code);
    errdefer allocator.free(code);
    const message = try allocator.dupe(u8, d.message);
    errdefer allocator.free(message);
    const rewritten = try allocator.dupe(u8, d.rewritten);
    errdefer allocator.free(rewritten);
    const hint = try allocator.dupe(u8, d.hint);
    errdefer allocator.free(hint);
    const out = try host.jsonAlloc(.{
        .code = code,
        .message = message,
        .rewritten = rewritten,
        .hint = hint,
        .notes = [_][]const u8{},
    });
    allocator.free(code);
    allocator.free(message);
    allocator.free(rewritten);
    allocator.free(hint);
    return out;
}

/// Classify a failed host command. Input JSON:
/// `{command, stdout?, stderr?, exit_code?, translated?, timed_out?, host?}`.
/// Output JSON: `{code, message, rewritten, hint, notes}`. Caller frees.
export fn remedy_core_diagnose_host_failure(
    json_in: ?[*]const u8,
    json_in_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const input = slice(json_in, json_in_len);
    if (input.len == 0) return invalid_status;
    return deliverBytes(diagnoseToOwnedJson(input), out_json, out_len);
}

test "diagnose mkdir powershell dialect" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const d = try diagnose(
        arena_state.allocator(),
        "mkdir -p a",
        "",
        "mkdir: A positional parameter cannot be found that accepts argument '-p'.",
        1,
        "if not exist \"a\\.\" mkdir \"a\"",
        false,
        "cmd",
    );
    try std.testing.expectEqualStrings("HOST_DIALECT", d.code);
    try std.testing.expect(d.rewritten.len > 0);
}

test "diagnose not found grep" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const d = try diagnose(
        arena_state.allocator(),
        "grep -n foo bar.py",
        "",
        "'grep' is not recognized as an internal or external command",
        1,
        "",
        false,
        "cmd",
    );
    try std.testing.expectEqualStrings("HOST_NOT_FOUND", d.code);
    try std.testing.expect(std.mem.indexOf(u8, d.message, "grep") != null);
    try std.testing.expect(std.mem.indexOf(u8, d.hint, "POSIX") != null or std.mem.indexOf(u8, d.hint, "grep") != null);
}

test "diagnose timeout interactive" {
    var arena_state = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena_state.deinit();
    const d = try diagnose(
        arena_state.allocator(),
        "Read-Host pw",
        "Password:",
        "",
        1,
        "",
        true,
        "pwsh",
    );
    try std.testing.expectEqualStrings("HOST_INTERACTIVE", d.code);
}

test "diagnose C ABI" {
    const input =
        \\{"command":"grep x","stderr":"'grep' is not recognized as an internal or external command","exit_code":1}
    ;
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const status = remedy_core_diagnose_host_failure(input.ptr, input.len, &out_ptr, &out_len);
    try std.testing.expectEqual(ok_status, status);
    defer host.allocator.free(out_ptr.?[0..out_len]);
    try std.testing.expect(std.mem.indexOf(u8, out_ptr.?[0..out_len], "HOST_NOT_FOUND") != null);
}
