//! ConPTY C ABI (ABI 5). Windows implementation in `conpty_windows.zig`;
//! other targets export the same symbols as `unsupported`.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");

pub const is_windows = builtin.os.tag == .windows;
const windows = if (is_windows) @import("conpty_windows.zig") else struct {};

const Status = root.Status;
const Error = host.Error;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

fn statusOf(err: Error) i32 {
    return host.statusOf(err);
}

fn statusOfVoid(result: Error!void) i32 {
    result catch |err| return statusOf(err);
    return ok_status;
}

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

export fn remedy_core_conpty_available(out_available: ?*u8) callconv(.c) i32 {
    const output = out_available orelse return invalid_status;
    if (!is_windows) {
        output.* = 0;
        return unsupported_status;
    }
    output.* = @intFromBool(windows.available());
    return ok_status;
}

export fn remedy_core_conpty_spawn(
    argv_json: ?[*]const u8,
    argv_len: usize,
    cwd: ?[*]const u8,
    cwd_len: usize,
    env_json: ?[*]const u8,
    env_len: usize,
    cols: u16,
    rows: u16,
    out_pid: ?*u32,
    out_handle: ?*u64,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    const spawned = windows.spawn(
        slice(argv_json, argv_len),
        slice(cwd, cwd_len),
        slice(env_json, env_len),
        cols,
        rows,
    ) catch |err| {
        pid_slot.* = 0;
        handle_slot.* = 0;
        return statusOf(err);
    };
    pid_slot.* = spawned.pid;
    handle_slot.* = spawned.handle;
    return ok_status;
}

export fn remedy_core_conpty_write(
    handle: u64,
    data: ?[*]const u8,
    len: usize,
    out_written: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const written_slot = out_written orelse return invalid_status;
    const bytes = slice(data, len);
    const n = windows.write(handle, bytes) catch |err| {
        written_slot.* = 0;
        return statusOf(err);
    };
    written_slot.* = n;
    return ok_status;
}

export fn remedy_core_conpty_read(
    handle: u64,
    buf: ?[*]u8,
    max_len: usize,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const len_slot = out_len orelse return invalid_status;
    const raw = buf orelse return invalid_status;
    if (max_len == 0) {
        len_slot.* = 0;
        return ok_status;
    }
    const n = windows.read(handle, raw[0..max_len]) catch |err| {
        len_slot.* = 0;
        return statusOf(err);
    };
    len_slot.* = n;
    return ok_status;
}

export fn remedy_core_conpty_poll(
    handle: u64,
    out_exited: ?*u8,
    out_exit_code: ?*u32,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const exited_slot = out_exited orelse return invalid_status;
    const code_slot = out_exit_code orelse return invalid_status;
    const outcome = windows.poll(handle) catch |err| return statusOf(err);
    exited_slot.* = @intFromBool(outcome.exited);
    code_slot.* = outcome.exit_code;
    return ok_status;
}

export fn remedy_core_conpty_kill(handle: u64) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return statusOfVoid(windows.kill(handle));
}

export fn remedy_core_conpty_close_pipe(handle: u64, which: u32) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    const pipe: windows.PipeWhich = switch (which) {
        0 => .stdin,
        1 => .stdout,
        else => return invalid_status,
    };
    return statusOfVoid(windows.closePipe(handle, pipe));
}

export fn remedy_core_conpty_close(handle: u64) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return statusOfVoid(windows.close(handle));
}

test {
    if (is_windows) _ = windows;
}
