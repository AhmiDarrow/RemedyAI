//! macOS process control for the Desktop sidecar and authorized worker spawn.
//!
//! Windows uses a job object and Linux uses setpgid + /proc. Here, hidden
//! spawn leads a process group (pgid=0), wait/close use waitpid, and kill-tree
//! walks libproc parents then SIGKILLs the group and descendants.
//! Desktop input, capture, and Accessibility live in a follow-up change.

const std = @import("std");
const host = @import("host.zig");

const Error = host.Error;
const allocator = host.allocator;
const Spawned = host.Spawned;
const WaitOutcome = host.WaitOutcome;

const pid_t = i32;
const PROC_PIDTBSDINFO: c_int = 3;
const SIGKILL: c_int = 9;
const WNOHANG: c_int = 1;

extern fn proc_listallpids(buffer: ?*anyopaque, buffersize: c_int) callconv(.c) c_int;
extern fn proc_pidinfo(pid: c_int, flavor: c_int, arg: u64, buffer: ?*anyopaque, buffersize: c_int) callconv(.c) c_int;
extern fn waitpid(pid: pid_t, status: *c_int, options: c_int) callconv(.c) pid_t;
extern fn kill(pid: pid_t, sig: c_int) callconv(.c) c_int;

const ProcBsdInfo = extern struct {
    pbi_flags: u32 = 0,
    pbi_status: u32 = 0,
    pbi_xstatus: u32 = 0,
    pbi_pid: u32 = 0,
    pbi_ppid: u32 = 0,
    // remainder is unused; we only need ppid. Keep a pad large enough
    // for PROC_PIDTBSDINFO so the kernel write does not overflow.
    _pad: [256]u8 = undefined,
};

const Process = struct {
    pid: pid_t,
    pgid: pid_t,
    exited: bool = false,
    exit_code: u32 = 0,
};

fn processFrom(handle: u64) Error!*Process {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(handle);
}

fn sleepMs(ms: u32) void {
    if (ms == 0) return;
    std.Thread.sleep(@as(u64, ms) * std.time.ns_per_ms);
}

fn reapNonBlocking(process: *Process) Error!bool {
    if (process.exited) return true;
    var status: c_int = 0;
    const rc = waitpid(process.pid, &status, WNOHANG);
    if (rc < 0) {
        process.exited = true;
        return true;
    }
    if (rc == 0) return false;
    process.exited = true;
    if (status & 0x7F == 0) {
        process.exit_code = @as(u32, @intCast((status >> 8) & 0xFF));
    } else {
        process.exit_code = 0x100 | @as(u32, @intCast(status & 0x7F));
    }
    return true;
}

fn collectChildren(gpa: std.mem.Allocator, root_pid: u32) Error![]u32 {
    var pids: [4096]c_int = undefined;
    const bytes = proc_listallpids(@ptrCast(&pids), @intCast(pids.len * @sizeOf(c_int)));
    if (bytes <= 0) return gpa.dupe(u32, &[_]u32{root_pid}) catch return error.OutOfMemory;
    const n: usize = @intCast(@divTrunc(bytes, @sizeOf(c_int)));
    var victims: std.ArrayList(u32) = .empty;
    try victims.append(gpa, root_pid);
    var index: usize = 0;
    while (index < victims.items.len) : (index += 1) {
        const parent = victims.items[index];
        var i: usize = 0;
        while (i < n) : (i += 1) {
            const child: u32 = @intCast(pids[i]);
            if (child == 0 or child == parent) continue;
            if (std.mem.indexOfScalar(u32, victims.items, child) != null) continue;
            var info: ProcBsdInfo = .{};
            const got = proc_pidinfo(@intCast(child), PROC_PIDTBSDINFO, 0, &info, @sizeOf(ProcBsdInfo));
            if (got <= 0) continue;
            if (info.pbi_ppid == parent) {
                try victims.append(gpa, child);
            }
        }
    }
    return victims.toOwnedSlice(gpa) catch return error.OutOfMemory;
}

pub fn killTree(pid: u32) Error!void {
    if (pid == 0 or pid == 1) return error.InvalidArgument;
    const victims = try collectChildren(allocator, pid);
    defer allocator.free(victims);
    _ = kill(-@as(pid_t, @intCast(pid)), SIGKILL);
    var index = victims.len;
    while (index > 0) {
        index -= 1;
        _ = kill(@intCast(victims[index]), SIGKILL);
    }
}

fn threadedIo() std.Io.Threaded {
    const parent_env: std.process.Environ = .{ .block = .empty };
    return std.Io.Threaded.init(allocator, .{ .environ = parent_env });
}

pub fn spawnHidden(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!Spawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    const argv = try host.parseArgv(gpa, argv_json);
    if (argv.len == 0 or !std.fs.path.isAbsolute(argv[0])) return error.InvalidArgument;

    var env_map_storage: ?std.process.Environ.Map = null;
    defer if (env_map_storage) |*m| m.deinit();
    env_map_storage = try host.resolveEnvMap(allocator, env_json);
    const env_map_ptr: ?*const std.process.Environ.Map = if (env_map_storage) |*m| m else null;
    const cwd_opt: std.process.Child.Cwd = if (cwd.len == 0)
        .inherit
    else
        .{ .path = cwd };

    var threaded = threadedIo();
    defer threaded.deinit();
    const io = threaded.io();
    var child = std.process.spawn(io, .{
        .argv = argv,
        .cwd = cwd_opt,
        .environ_map = env_map_ptr,
        .stdin = .ignore,
        .stdout = .ignore,
        .stderr = .ignore,
        .pgid = 0,
    }) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        error.InvalidWtf8, error.InvalidUserId, error.InvalidProcessGroupId, error.InvalidExe, error.InvalidName, error.InvalidBatchScriptArg => return error.InvalidArgument,
        else => {
            host.setOsError(1);
            return error.OperationFailed;
        },
    };
    const pid = child.id orelse {
        host.setOsError(1);
        return error.OperationFailed;
    };
    child.id = null;
    const process = allocator.create(Process) catch {
        _ = kill(pid, SIGKILL);
        var status: c_int = 0;
        _ = waitpid(pid, &status, 0);
        return error.OutOfMemory;
    };
    process.* = .{ .pid = pid, .pgid = pid };
    return .{ .pid = @intCast(pid), .handle = @intFromPtr(process) };
}

pub fn processWait(handle: u64, timeout_ms: u32) Error!WaitOutcome {
    const process = try processFrom(handle);
    if (try reapNonBlocking(process)) {
        return .{ .exited = true, .exit_code = process.exit_code };
    }
    if (timeout_ms == 0) return .{ .exited = false, .exit_code = 0 };
    const forever = timeout_ms == std.math.maxInt(u32);
    const start = std.time.milliTimestamp();
    const deadline: i64 = if (forever) std.math.maxInt(i64) else start + @as(i64, @intCast(timeout_ms));
    while (forever or std.time.milliTimestamp() < deadline) {
        sleepMs(10);
        if (try reapNonBlocking(process)) {
            return .{ .exited = true, .exit_code = process.exit_code };
        }
    }
    return .{ .exited = false, .exit_code = 0 };
}

pub fn processClose(handle: u64) Error!void {
    const process = try processFrom(handle);
    if (!process.exited) {
        _ = kill(-process.pgid, SIGKILL);
        _ = kill(process.pid, SIGKILL);
        var status: c_int = 0;
        var spins: u32 = 0;
        while (spins < 200) : (spins += 1) {
            const rc = waitpid(process.pid, &status, WNOHANG);
            if (rc != 0) break;
            sleepMs(10);
        }
    }
    allocator.destroy(process);
}

test "killTree rejects pid 0 and 1" {
    try std.testing.expectError(error.InvalidArgument, killTree(0));
    try std.testing.expectError(error.InvalidArgument, killTree(1));
}

test "spawnHidden rejects a relative argv" {
    try std.testing.expectError(error.InvalidArgument, spawnHidden("[\"sleep\",\"1\"]", "", ""));
}

test "spawnHidden rejects an empty argv" {
    try std.testing.expectError(error.InvalidArgument, spawnHidden("[]", "", ""));
}
