//! Windows ConPTY: CreatePseudoConsole + CreateProcessW with
//! PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE. Parent keeps pipe ends; the child
//! sees a real console. Handles live in an opaque Session freed by `close`.

const std = @import("std");
const host = @import("host.zig");

const Error = host.Error;
const allocator = host.allocator;

const BOOL = c_int;
const DWORD = u32;
const WORD = u16;
const SHORT = i16;
const HRESULT = i32;
const HANDLE = *anyopaque;
const HPCON = *anyopaque;
const SIZE_T = usize;

const COORD = extern struct { X: SHORT, Y: SHORT };

const SECURITY_ATTRIBUTES = extern struct {
    nLength: DWORD,
    lpSecurityDescriptor: ?*anyopaque,
    bInheritHandle: BOOL,
};

const STARTUPINFOW = extern struct {
    cb: DWORD,
    lpReserved: ?[*:0]u16,
    lpDesktop: ?[*:0]u16,
    lpTitle: ?[*:0]u16,
    dwX: DWORD,
    dwY: DWORD,
    dwXSize: DWORD,
    dwYSize: DWORD,
    dwXCountChars: DWORD,
    dwYCountChars: DWORD,
    dwFillAttribute: DWORD,
    dwFlags: DWORD,
    wShowWindow: WORD,
    cbReserved2: WORD,
    lpReserved2: ?*u8,
    hStdInput: ?HANDLE,
    hStdOutput: ?HANDLE,
    hStdError: ?HANDLE,
};

const STARTUPINFOEXW = extern struct {
    StartupInfo: STARTUPINFOW,
    lpAttributeList: ?*anyopaque,
};

const PROCESS_INFORMATION = extern struct {
    hProcess: HANDLE,
    hThread: HANDLE,
    dwProcessId: DWORD,
    dwThreadId: DWORD,
};

const PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE: usize = 0x00020016;
const EXTENDED_STARTUPINFO_PRESENT: DWORD = 0x00080000;
const CREATE_UNICODE_ENVIRONMENT: DWORD = 0x00000400;
const STARTF_USESTDHANDLES: DWORD = 0x00000100;
const STILL_ACTIVE: DWORD = 259;

extern "kernel32" fn GetLastError() callconv(.winapi) DWORD;
extern "kernel32" fn Sleep(ms: DWORD) callconv(.winapi) void;
extern "kernel32" fn CloseHandle(handle: HANDLE) callconv(.winapi) BOOL;
extern "kernel32" fn GetModuleHandleW(name: ?[*:0]const u16) callconv(.winapi) ?*anyopaque;
extern "kernel32" fn GetProcAddress(module: *anyopaque, name: [*:0]const u8) callconv(.winapi) ?*anyopaque;
extern "kernel32" fn CreatePipe(
    read_pipe: *?HANDLE,
    write_pipe: *?HANDLE,
    attributes: ?*SECURITY_ATTRIBUTES,
    size: DWORD,
) callconv(.winapi) BOOL;
extern "kernel32" fn InitializeProcThreadAttributeList(
    list: ?*anyopaque,
    count: DWORD,
    flags: DWORD,
    size: *SIZE_T,
) callconv(.winapi) BOOL;
extern "kernel32" fn UpdateProcThreadAttribute(
    list: *anyopaque,
    flags: DWORD,
    attribute: usize,
    value: ?*anyopaque,
    size: SIZE_T,
    previous: ?*anyopaque,
    return_size: ?*SIZE_T,
) callconv(.winapi) BOOL;
extern "kernel32" fn DeleteProcThreadAttributeList(list: *anyopaque) callconv(.winapi) void;
extern "kernel32" fn CreateProcessW(
    application: ?[*:0]const u16,
    command_line: ?[*:0]u16,
    process_attributes: ?*anyopaque,
    thread_attributes: ?*anyopaque,
    inherit_handles: BOOL,
    creation_flags: DWORD,
    environment: ?*anyopaque,
    current_directory: ?[*:0]const u16,
    startup_info: *anyopaque,
    process_information: *PROCESS_INFORMATION,
) callconv(.winapi) BOOL;
extern "kernel32" fn TerminateProcess(process: HANDLE, exit_code: u32) callconv(.winapi) BOOL;
extern "kernel32" fn GetExitCodeProcess(process: HANDLE, code: *DWORD) callconv(.winapi) BOOL;
extern "kernel32" fn ReadFile(
    handle: HANDLE,
    buffer: [*]u8,
    to_read: DWORD,
    read: *DWORD,
    overlapped: ?*anyopaque,
) callconv(.winapi) BOOL;
extern "kernel32" fn WriteFile(
    handle: HANDLE,
    buffer: [*]const u8,
    to_write: DWORD,
    written: *DWORD,
    overlapped: ?*anyopaque,
) callconv(.winapi) BOOL;
extern "kernel32" fn PeekNamedPipe(
    pipe: HANDLE,
    buffer: ?[*]u8,
    buffer_size: DWORD,
    bytes_read: ?*DWORD,
    total_bytes_avail: ?*DWORD,
    bytes_left_this_message: ?*DWORD,
) callconv(.winapi) BOOL;

const CreatePseudoConsoleFn = *const fn (
    size: COORD,
    input: HANDLE,
    output: HANDLE,
    flags: DWORD,
    pc: *HPCON,
) callconv(.winapi) HRESULT;
const ClosePseudoConsoleFn = *const fn (pc: HPCON) callconv(.winapi) void;

const Api = struct {
    create: CreatePseudoConsoleFn,
    close: ClosePseudoConsoleFn,
};

var api_cell: ?Api = null;
var api_lock: std.atomic.Mutex = .unlocked;

fn loadApi() ?Api {
    // Single-flight load; concurrent callers may race the GetProcAddress once.
    if (!api_lock.tryLock()) {
        // Another thread is loading; spin briefly on the cached cell.
        var spins: usize = 0;
        while (spins < 1000) : (spins += 1) {
            if (api_cell) |cached| return cached;
        }
        return api_cell;
    }
    defer api_lock.unlock();
    if (api_cell) |cached| return cached;
    const kernel32_name = std.unicode.utf8ToUtf16LeStringLiteral("kernel32");
    const module = GetModuleHandleW(kernel32_name) orelse return null;
    const create_ptr = GetProcAddress(module, "CreatePseudoConsole") orelse return null;
    const close_ptr = GetProcAddress(module, "ClosePseudoConsole") orelse return null;
    const loaded: Api = .{
        .create = @ptrCast(create_ptr),
        .close = @ptrCast(close_ptr),
    };
    api_cell = loaded;
    return loaded;
}

fn fail() Error {
    host.setOsError(GetLastError());
    return error.OperationFailed;
}

fn failHr(hr: HRESULT) Error {
    host.setOsError(@as(u32, @bitCast(hr)));
    return error.OperationFailed;
}

pub const Session = struct {
    process: ?HANDLE = null,
    pc: ?HPCON = null,
    stdin_write: ?HANDLE = null,
    stdout_read: ?HANDLE = null,
    pid: u32 = 0,
    exit_code: ?u32 = null,
};

pub const Spawned = struct { pid: u32, handle: u64 };

fn sessionFrom(handle: u64) Error!*Session {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(handle)));
}

pub fn available() bool {
    return loadApi() != null;
}

fn closeOpt(handle: ?HANDLE) void {
    if (handle) |h| _ = CloseHandle(h);
}

/// Spawn argv attached to a ConPTY. cols/rows of 0 default to 120x40.
pub fn spawn(
    argv_json: []const u8,
    cwd: []const u8,
    env_json: []const u8,
    cols: u16,
    rows: u16,
) Error!Spawned {
    const api = loadApi() orelse {
        host.setOsError(0);
        return error.Unsupported;
    };

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0)
        null
    else
        (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.resolveEnvBlock(gpa, env_json)) |block|
        @ptrCast(block.ptr)
    else
        null;

    var sa = SECURITY_ATTRIBUTES{
        .nLength = @sizeOf(SECURITY_ATTRIBUTES),
        .lpSecurityDescriptor = null,
        .bInheritHandle = 0,
    };

    var pty_in: ?HANDLE = null;
    var con_in: ?HANDLE = null;
    if (CreatePipe(&pty_in, &con_in, &sa, 0) == 0) return fail();

    var con_out: ?HANDLE = null;
    var pty_out: ?HANDLE = null;
    if (CreatePipe(&con_out, &pty_out, &sa, 0) == 0) {
        closeOpt(pty_in);
        closeOpt(con_in);
        return fail();
    }

    const size = COORD{
        .X = @intCast(if (cols == 0) 120 else cols),
        .Y = @intCast(if (rows == 0) 40 else rows),
    };
    var pc: HPCON = undefined;
    const hr = api.create(size, pty_in.?, pty_out.?, 0, &pc);
    // CreatePseudoConsole copies the PTY ends; parent must close its copies.
    closeOpt(pty_in);
    closeOpt(pty_out);
    pty_in = null;
    pty_out = null;
    if (hr != 0) {
        closeOpt(con_in);
        closeOpt(con_out);
        return failHr(hr);
    }

    var attr_size: SIZE_T = 0;
    _ = InitializeProcThreadAttributeList(null, 1, 0, &attr_size);
    if (attr_size == 0) {
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return fail();
    }
    const attr_buf = allocator.alloc(u8, attr_size) catch {
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return error.OutOfMemory;
    };
    defer allocator.free(attr_buf);
    @memset(attr_buf, 0);
    if (InitializeProcThreadAttributeList(attr_buf.ptr, 1, 0, &attr_size) == 0) {
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return fail();
    }
    defer DeleteProcThreadAttributeList(attr_buf.ptr);

    if (UpdateProcThreadAttribute(
        attr_buf.ptr,
        0,
        PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
        pc,
        @sizeOf(HPCON),
        null,
        null,
    ) == 0) {
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return fail();
    }

    var siex = std.mem.zeroes(STARTUPINFOEXW);
    siex.StartupInfo.cb = @sizeOf(STARTUPINFOEXW);
    // NULL std handles + STARTF_USESTDHANDLES: the pseudoconsole is the only
    // stdio. Without this, a redirected launcher/service stdio leaks into the
    // child and every session read times out.
    siex.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    siex.StartupInfo.hStdInput = null;
    siex.StartupInfo.hStdOutput = null;
    siex.StartupInfo.hStdError = null;
    siex.lpAttributeList = attr_buf.ptr;

    var info: PROCESS_INFORMATION = undefined;
    const flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT;
    if (CreateProcessW(
        null,
        command_line.ptr,
        null,
        null,
        0,
        flags,
        environment,
        directory,
        &siex,
        &info,
    ) == 0) {
        const err = fail();
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return err;
    }
    _ = CloseHandle(info.hThread);

    const session = allocator.create(Session) catch {
        _ = TerminateProcess(info.hProcess, 1);
        _ = CloseHandle(info.hProcess);
        api.close(pc);
        closeOpt(con_in);
        closeOpt(con_out);
        return error.OutOfMemory;
    };
    session.* = .{
        .process = info.hProcess,
        .pc = pc,
        .stdin_write = con_in,
        .stdout_read = con_out,
        .pid = info.dwProcessId,
        .exit_code = null,
    };
    return .{ .pid = info.dwProcessId, .handle = @intFromPtr(session) };
}

pub fn write(handle: u64, data: []const u8) Error!usize {
    const session = try sessionFrom(handle);
    const pipe = session.stdin_write orelse return error.InvalidArgument;
    if (data.len == 0) return 0;
    var written: DWORD = 0;
    if (WriteFile(pipe, data.ptr, @intCast(data.len), &written, null) == 0) return fail();
    return written;
}

pub fn read(handle: u64, buf: []u8) Error!usize {
    const session = try sessionFrom(handle);
    const pipe = session.stdout_read orelse {
        return 0;
    };
    if (buf.len == 0) return 0;
    var got: DWORD = 0;
    if (ReadFile(pipe, buf.ptr, @intCast(buf.len), &got, null) == 0) {
        // Broken pipe / closed end → EOF for the session reader.
        return 0;
    }
    return got;
}

/// Non-blocking drain: PeekNamedPipe then ReadFile only when bytes are waiting.
/// Production streaming keeps blocking `read`; tests and poll loops use this so a
/// quiet ConPTY cannot stall the caller forever.
pub fn readAvailable(handle: u64, buf: []u8) Error!usize {
    const session = try sessionFrom(handle);
    const pipe = session.stdout_read orelse return 0;
    if (buf.len == 0) return 0;
    var avail: DWORD = 0;
    if (PeekNamedPipe(pipe, null, 0, null, &avail, null) == 0) {
        // Broken/closed pipe → treat as EOF for the drain path.
        return 0;
    }
    if (avail == 0) return 0;
    const want: DWORD = @intCast(@min(buf.len, @as(usize, @intCast(avail))));
    var got: DWORD = 0;
    if (ReadFile(pipe, buf.ptr, want, &got, null) == 0) return 0;
    return got;
}

pub const PollOutcome = struct { exited: bool, exit_code: u32 };

pub fn poll(handle: u64) Error!PollOutcome {
    const session = try sessionFrom(handle);
    if (session.exit_code) |code| return .{ .exited = true, .exit_code = code };
    const process = session.process orelse return .{ .exited = true, .exit_code = session.exit_code orelse 1 };
    var code: DWORD = 0;
    if (GetExitCodeProcess(process, &code) == 0) return fail();
    if (code == STILL_ACTIVE) return .{ .exited = false, .exit_code = 0 };
    session.exit_code = code;
    return .{ .exited = true, .exit_code = code };
}

pub fn kill(handle: u64) Error!void {
    const session = try sessionFrom(handle);
    if (session.process) |process| {
        _ = TerminateProcess(process, 1);
    }
    if (session.exit_code == null) session.exit_code = 1;
}

pub const PipeWhich = enum(u32) { stdin = 0, stdout = 1 };

pub fn closePipe(handle: u64, which: PipeWhich) Error!void {
    const session = try sessionFrom(handle);
    switch (which) {
        .stdin => {
            if (session.stdin_write) |h| {
                _ = CloseHandle(h);
                session.stdin_write = null;
            }
        },
        .stdout => {
            if (session.stdout_read) |h| {
                _ = CloseHandle(h);
                session.stdout_read = null;
            }
        },
    }
}

pub fn close(handle: u64) Error!void {
    const session = try sessionFrom(handle);
    if (session.stdin_write) |h| {
        _ = CloseHandle(h);
        session.stdin_write = null;
    }
    if (session.stdout_read) |h| {
        _ = CloseHandle(h);
        session.stdout_read = null;
    }
    if (session.pc) |pc| {
        if (loadApi()) |api| api.close(pc);
        session.pc = null;
    }
    if (session.process) |process| {
        _ = CloseHandle(process);
        session.process = null;
    }
    allocator.destroy(session);
}

test "conpty available matches CreatePseudoConsole export" {
    try std.testing.expect(available());
}

test "conpty spawn echo round-trip then close" {
    const argv = "[\"C:\\\\Windows\\\\System32\\\\cmd.exe\", \"/d\", \"/c\", \"echo remedy-conpty\"]";
    const spawned = try spawn(argv, "", "", 80, 25);
    defer {
        // Always tear down: CloseHandle on the pipe unblocks any waiter and
        // keeps `zig build test` from hanging the native prepush lane.
        kill(spawned.handle) catch {};
        close(spawned.handle) catch {};
    }

    try std.testing.expect(spawned.pid > 0);

    var buf: [4096]u8 = undefined;
    var total: std.ArrayList(u8) = .empty;
    defer total.deinit(std.testing.allocator);

    var spins: usize = 0;
    while (spins < 400) : (spins += 1) {
        const n = try readAvailable(spawned.handle, buf[0..]);
        if (n > 0) try total.appendSlice(std.testing.allocator, buf[0..n]);
        if (std.mem.indexOf(u8, total.items, "remedy-conpty") != null) break;
        const outcome = try poll(spawned.handle);
        if (outcome.exited) {
            // Drain whatever ConPTY still holds after the child exits.
            var drain_spins: usize = 0;
            while (drain_spins < 50) : (drain_spins += 1) {
                const m = try readAvailable(spawned.handle, buf[0..]);
                if (m == 0) {
                    Sleep(10);
                    const m2 = try readAvailable(spawned.handle, buf[0..]);
                    if (m2 == 0) break;
                    try total.appendSlice(std.testing.allocator, buf[0..m2]);
                    continue;
                }
                try total.appendSlice(std.testing.allocator, buf[0..m]);
            }
            break;
        }
        Sleep(10);
    }
    try std.testing.expect(std.mem.indexOf(u8, total.items, "remedy-conpty") != null);
}
