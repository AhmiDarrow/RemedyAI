//! Win32 implementation of the host primitives declared in `host.zig`.
//!
//! Every function here returns `host.Error`; on an OS failure the
//! `GetLastError` code is recorded through `host.setOsError` before the error
//! is returned so the caller can surface it.

const std = @import("std");
const host = @import("host.zig");

const Error = host.Error;
const allocator = host.allocator;

// ---------------------------------------------------------------------------
// Win32 surface (declared here so the module does not depend on std's churn)
// ---------------------------------------------------------------------------

const BOOL = c_int;
const DWORD = u32;
const WORD = u16;
const UINT = c_uint;
const LONG = i32;
const HANDLE = *anyopaque;
const HWND = *opaque {};
const HDC = *opaque {};
const HMONITOR = *opaque {};
const HBITMAP = *opaque {};
const HGDIOBJ = *anyopaque;
const HGLOBAL = *anyopaque;
const HMODULE = *opaque {};
const LPARAM = isize;
const WPARAM = usize;
const HRESULT = i32;

const RECT = extern struct { left: LONG, top: LONG, right: LONG, bottom: LONG };

const MONITORINFO = extern struct {
    cbSize: DWORD,
    rcMonitor: RECT,
    rcWork: RECT,
    dwFlags: DWORD,
};

const BITMAPINFOHEADER = extern struct {
    biSize: DWORD,
    biWidth: LONG,
    biHeight: LONG,
    biPlanes: WORD,
    biBitCount: WORD,
    biCompression: DWORD,
    biSizeImage: DWORD,
    biXPelsPerMeter: LONG,
    biYPelsPerMeter: LONG,
    biClrUsed: DWORD,
    biClrImportant: DWORD,
};

const BITMAPINFO = extern struct {
    bmiHeader: BITMAPINFOHEADER,
    bmiColors: [3]DWORD,
};

const MOUSEINPUT = extern struct {
    dx: LONG,
    dy: LONG,
    mouseData: DWORD,
    dwFlags: DWORD,
    time: DWORD,
    dwExtraInfo: usize,
};

const KEYBDINPUT = extern struct {
    wVk: WORD,
    wScan: WORD,
    dwFlags: DWORD,
    time: DWORD,
    dwExtraInfo: usize,
};

const HARDWAREINPUT = extern struct {
    uMsg: DWORD,
    wParamL: WORD,
    wParamH: WORD,
};

const INPUT = extern struct {
    type: DWORD,
    u: extern union {
        mi: MOUSEINPUT,
        ki: KEYBDINPUT,
        hi: HARDWAREINPUT,
    },
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

const PROCESS_INFORMATION = extern struct {
    hProcess: HANDLE,
    hThread: HANDLE,
    dwProcessId: DWORD,
    dwThreadId: DWORD,
};

const JOBOBJECT_BASIC_LIMIT_INFORMATION = extern struct {
    PerProcessUserTimeLimit: i64,
    PerJobUserTimeLimit: i64,
    LimitFlags: DWORD,
    MinimumWorkingSetSize: usize,
    MaximumWorkingSetSize: usize,
    ActiveProcessLimit: DWORD,
    Affinity: usize,
    PriorityClass: DWORD,
    SchedulingClass: DWORD,
};

const IO_COUNTERS = extern struct {
    ReadOperationCount: u64,
    WriteOperationCount: u64,
    OtherOperationCount: u64,
    ReadTransferCount: u64,
    WriteTransferCount: u64,
    OtherTransferCount: u64,
};

const JOBOBJECT_EXTENDED_LIMIT_INFORMATION = extern struct {
    BasicLimitInformation: JOBOBJECT_BASIC_LIMIT_INFORMATION,
    IoInfo: IO_COUNTERS,
    ProcessMemoryLimit: usize,
    JobMemoryLimit: usize,
    PeakProcessMemoryUsed: usize,
    PeakJobMemoryUsed: usize,
};

const PROCESSENTRY32W = extern struct {
    dwSize: DWORD,
    cntUsage: DWORD,
    th32ProcessID: DWORD,
    th32DefaultHeapID: usize,
    th32ModuleID: DWORD,
    cntThreads: DWORD,
    th32ParentProcessID: DWORD,
    pcPriClassBase: LONG,
    dwFlags: DWORD,
    szExeFile: [260]u16,
};

const FILETIME = extern struct { dwLowDateTime: DWORD, dwHighDateTime: DWORD };

const WndEnumProc = *const fn (HWND, LPARAM) callconv(.winapi) BOOL;
const MonitorEnumProc = *const fn (HMONITOR, ?HDC, *RECT, LPARAM) callconv(.winapi) BOOL;
const DpiContextFn = *const fn (?*anyopaque) callconv(.winapi) BOOL;

extern "user32" fn SetProcessDPIAware() callconv(.winapi) BOOL;
extern "user32" fn GetSystemMetrics(index: c_int) callconv(.winapi) c_int;
extern "user32" fn EnumDisplayMonitors(hdc: ?HDC, clip: ?*const RECT, proc: MonitorEnumProc, data: LPARAM) callconv(.winapi) BOOL;
extern "user32" fn GetMonitorInfoW(monitor: HMONITOR, info: *MONITORINFO) callconv(.winapi) BOOL;
extern "user32" fn GetDC(hwnd: ?HWND) callconv(.winapi) ?HDC;
extern "user32" fn GetWindowDC(hwnd: HWND) callconv(.winapi) ?HDC;
extern "user32" fn ReleaseDC(hwnd: ?HWND, hdc: HDC) callconv(.winapi) c_int;
extern "user32" fn PrintWindow(hwnd: HWND, hdc: HDC, flags: UINT) callconv(.winapi) BOOL;
extern "user32" fn GetWindowRect(hwnd: HWND, rect: *RECT) callconv(.winapi) BOOL;
extern "user32" fn IsWindow(hwnd: ?HWND) callconv(.winapi) BOOL;
extern "user32" fn IsWindowVisible(hwnd: HWND) callconv(.winapi) BOOL;
extern "user32" fn IsIconic(hwnd: HWND) callconv(.winapi) BOOL;
extern "user32" fn GetWindowTextLengthW(hwnd: HWND) callconv(.winapi) c_int;
extern "user32" fn GetWindowTextW(hwnd: HWND, text: [*]u16, max: c_int) callconv(.winapi) c_int;
extern "user32" fn GetClassNameW(hwnd: HWND, text: [*]u16, max: c_int) callconv(.winapi) c_int;
extern "user32" fn EnumWindows(proc: WndEnumProc, lparam: LPARAM) callconv(.winapi) BOOL;
extern "user32" fn EnumChildWindows(parent: ?HWND, proc: WndEnumProc, lparam: LPARAM) callconv(.winapi) BOOL;
extern "user32" fn GetForegroundWindow() callconv(.winapi) ?HWND;
extern "user32" fn SetForegroundWindow(hwnd: HWND) callconv(.winapi) BOOL;
extern "user32" fn BringWindowToTop(hwnd: HWND) callconv(.winapi) BOOL;
extern "user32" fn GetAncestor(hwnd: HWND, flags: UINT) callconv(.winapi) ?HWND;
extern "user32" fn GetWindowThreadProcessId(hwnd: HWND, pid: ?*DWORD) callconv(.winapi) DWORD;
extern "user32" fn AttachThreadInput(attach: DWORD, to: DWORD, flag: BOOL) callconv(.winapi) BOOL;
extern "user32" fn ShowWindow(hwnd: HWND, cmd: c_int) callconv(.winapi) BOOL;
extern "user32" fn PostMessageW(hwnd: HWND, msg: UINT, wparam: WPARAM, lparam: LPARAM) callconv(.winapi) BOOL;
extern "user32" fn SetWindowPos(hwnd: HWND, after: ?HWND, x: c_int, y: c_int, cx: c_int, cy: c_int, flags: UINT) callconv(.winapi) BOOL;
extern "user32" fn SendInput(count: UINT, inputs: [*]const INPUT, size: c_int) callconv(.winapi) UINT;
extern "user32" fn VkKeyScanW(ch: u16) callconv(.winapi) i16;
extern "user32" fn OpenClipboard(owner: ?HWND) callconv(.winapi) BOOL;
extern "user32" fn CloseClipboard() callconv(.winapi) BOOL;
extern "user32" fn EmptyClipboard() callconv(.winapi) BOOL;
extern "user32" fn GetClipboardData(format: UINT) callconv(.winapi) ?HANDLE;
extern "user32" fn SetClipboardData(format: UINT, data: HANDLE) callconv(.winapi) ?HANDLE;

extern "shcore" fn SetProcessDpiAwareness(value: c_int) callconv(.winapi) HRESULT;
extern "shcore" fn GetDpiForMonitor(monitor: HMONITOR, kind: c_int, dpi_x: *UINT, dpi_y: *UINT) callconv(.winapi) HRESULT;

extern "gdi32" fn CreateCompatibleDC(hdc: ?HDC) callconv(.winapi) ?HDC;
extern "gdi32" fn CreateCompatibleBitmap(hdc: HDC, width: c_int, height: c_int) callconv(.winapi) ?HBITMAP;
extern "gdi32" fn SelectObject(hdc: HDC, object: HGDIOBJ) callconv(.winapi) ?HGDIOBJ;
extern "gdi32" fn BitBlt(dst: HDC, x: c_int, y: c_int, w: c_int, h: c_int, src: ?HDC, sx: c_int, sy: c_int, rop: DWORD) callconv(.winapi) BOOL;
extern "gdi32" fn GetDIBits(hdc: HDC, bitmap: HBITMAP, start: UINT, lines: UINT, bits: ?*anyopaque, info: *BITMAPINFO, usage: UINT) callconv(.winapi) c_int;
extern "gdi32" fn DeleteObject(object: HGDIOBJ) callconv(.winapi) BOOL;
extern "gdi32" fn DeleteDC(hdc: HDC) callconv(.winapi) BOOL;

extern "kernel32" fn GetLastError() callconv(.winapi) DWORD;
extern "kernel32" fn Sleep(ms: DWORD) callconv(.winapi) void;
extern "kernel32" fn GetCurrentThreadId() callconv(.winapi) DWORD;
extern "kernel32" fn CloseHandle(handle: HANDLE) callconv(.winapi) BOOL;
extern "kernel32" fn GetModuleHandleW(name: ?[*:0]const u16) callconv(.winapi) ?HMODULE;
extern "kernel32" fn GetProcAddress(module: HMODULE, name: [*:0]const u8) callconv(.winapi) ?*anyopaque;
extern "kernel32" fn GlobalAlloc(flags: UINT, bytes: usize) callconv(.winapi) ?HGLOBAL;
extern "kernel32" fn GlobalLock(mem: HGLOBAL) callconv(.winapi) ?*anyopaque;
extern "kernel32" fn GlobalUnlock(mem: HGLOBAL) callconv(.winapi) BOOL;
extern "kernel32" fn GlobalFree(mem: HGLOBAL) callconv(.winapi) ?HGLOBAL;
extern "kernel32" fn CreateProcessW(
    application: ?[*:0]const u16,
    command_line: ?[*:0]u16,
    process_attributes: ?*anyopaque,
    thread_attributes: ?*anyopaque,
    inherit_handles: BOOL,
    creation_flags: DWORD,
    environment: ?*anyopaque,
    current_directory: ?[*:0]const u16,
    startup_info: *STARTUPINFOW,
    process_information: *PROCESS_INFORMATION,
) callconv(.winapi) BOOL;
extern "kernel32" fn CreatePipe(
    read_pipe: *?HANDLE,
    write_pipe: *?HANDLE,
    attributes: ?*SECURITY_ATTRIBUTES,
    size: DWORD,
) callconv(.winapi) BOOL;
extern "kernel32" fn SetHandleInformation(handle: HANDLE, mask: DWORD, flags: DWORD) callconv(.winapi) BOOL;
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
extern "kernel32" fn CreateJobObjectW(attributes: ?*anyopaque, name: ?[*:0]const u16) callconv(.winapi) ?HANDLE;
extern "kernel32" fn SetInformationJobObject(job: HANDLE, class: c_int, info: *anyopaque, len: DWORD) callconv(.winapi) BOOL;
extern "kernel32" fn AssignProcessToJobObject(job: HANDLE, process: HANDLE) callconv(.winapi) BOOL;
extern "kernel32" fn ResumeThread(thread: HANDLE) callconv(.winapi) DWORD;
extern "kernel32" fn TerminateProcess(process: HANDLE, exit_code: UINT) callconv(.winapi) BOOL;
extern "kernel32" fn WaitForSingleObject(handle: HANDLE, ms: DWORD) callconv(.winapi) DWORD;
extern "kernel32" fn GetExitCodeProcess(process: HANDLE, code: *DWORD) callconv(.winapi) BOOL;
extern "kernel32" fn OpenProcess(access: DWORD, inherit: BOOL, pid: DWORD) callconv(.winapi) ?HANDLE;
extern "kernel32" fn GetProcessTimes(process: HANDLE, creation: *FILETIME, exit: *FILETIME, kernel: *FILETIME, user: *FILETIME) callconv(.winapi) BOOL;
extern "kernel32" fn CreateToolhelp32Snapshot(flags: DWORD, pid: DWORD) callconv(.winapi) ?HANDLE;
extern "kernel32" fn Process32FirstW(snapshot: HANDLE, entry: *PROCESSENTRY32W) callconv(.winapi) BOOL;
extern "kernel32" fn Process32NextW(snapshot: HANDLE, entry: *PROCESSENTRY32W) callconv(.winapi) BOOL;

const SM_CXSCREEN = 0;
const SM_CYSCREEN = 1;
const SM_XVIRTUALSCREEN = 76;
const SM_YVIRTUALSCREEN = 77;
const SM_CXVIRTUALSCREEN = 78;
const SM_CYVIRTUALSCREEN = 79;
const SRCCOPY: DWORD = 0x00CC0020;
const DIB_RGB_COLORS: UINT = 0;
const BI_RGB: DWORD = 0;
const PW_RENDERFULLCONTENT: UINT = 2;
const MONITORINFOF_PRIMARY: DWORD = 1;
const MDT_EFFECTIVE_DPI: c_int = 0;
const PROCESS_PER_MONITOR_DPI_AWARE: c_int = 2;
const DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2: isize = -4;

const INPUT_MOUSE: DWORD = 0;
const INPUT_KEYBOARD: DWORD = 1;
const MOUSEEVENTF_MOVE: DWORD = 0x0001;
const MOUSEEVENTF_LEFTDOWN: DWORD = 0x0002;
const MOUSEEVENTF_LEFTUP: DWORD = 0x0004;
const MOUSEEVENTF_RIGHTDOWN: DWORD = 0x0008;
const MOUSEEVENTF_RIGHTUP: DWORD = 0x0010;
const MOUSEEVENTF_MIDDLEDOWN: DWORD = 0x0020;
const MOUSEEVENTF_MIDDLEUP: DWORD = 0x0040;
const MOUSEEVENTF_WHEEL: DWORD = 0x0800;
const MOUSEEVENTF_HWHEEL: DWORD = 0x1000;
const MOUSEEVENTF_VIRTUALDESK: DWORD = 0x4000;
const MOUSEEVENTF_ABSOLUTE: DWORD = 0x8000;
const KEYEVENTF_KEYUP: DWORD = 0x0002;
const KEYEVENTF_UNICODE: DWORD = 0x0004;
const WHEEL_DELTA: i32 = 120;
const VK_RETURN: WORD = 0x0D;
const VK_MENU: WORD = 0x12;

const GA_ROOTOWNER: UINT = 3;
const SW_HIDE: WORD = 0;
const SW_MAXIMIZE: c_int = 3;
const SW_MINIMIZE: c_int = 6;
const SW_RESTORE: c_int = 9;
const WM_CLOSE: UINT = 0x0010;
const SWP_NOZORDER: UINT = 0x0004;
const SWP_NOACTIVATE: UINT = 0x0010;

const CF_UNICODETEXT: UINT = 13;
const GMEM_MOVEABLE: UINT = 0x0002;

const CREATE_SUSPENDED: DWORD = 0x00000004;
const CREATE_UNICODE_ENVIRONMENT: DWORD = 0x00000400;
const STARTF_USESTDHANDLES: DWORD = 0x00000100;
const HANDLE_FLAG_INHERIT: DWORD = 0x00000001;
const STILL_ACTIVE: DWORD = 259;

const SECURITY_ATTRIBUTES = extern struct {
    nLength: DWORD,
    lpSecurityDescriptor: ?*anyopaque,
    bInheritHandle: BOOL,
};
const CREATE_NO_WINDOW: DWORD = 0x08000000;
const STARTF_USESHOWWINDOW: DWORD = 0x00000001;
const JobObjectExtendedLimitInformation: c_int = 9;
const JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE: DWORD = 0x00002000;
const INFINITE: DWORD = 0xFFFFFFFF;
const WAIT_OBJECT_0: DWORD = 0;
const WAIT_TIMEOUT: DWORD = 0x00000102;
const PROCESS_TERMINATE: DWORD = 0x0001;
const PROCESS_QUERY_LIMITED_INFORMATION: DWORD = 0x1000;
const SYNCHRONIZE: DWORD = 0x00100000;
const TH32CS_SNAPPROCESS: DWORD = 0x00000002;
const ERROR_INVALID_PARAMETER: DWORD = 87;
const invalid_handle_value: HANDLE = @ptrFromInt(std.math.maxInt(usize));

fn fail() Error {
    host.setOsError(GetLastError());
    return error.OperationFailed;
}

fn hwndFrom(raw: u64) Error!HWND {
    if (raw == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(raw)));
}

fn hwndValue(hwnd: ?HWND) u64 {
    const window = hwnd orelse return 0;
    return @intFromPtr(window);
}

fn contextFrom(comptime T: type, lparam: LPARAM) *T {
    return @ptrFromInt(@as(usize, @bitCast(lparam)));
}

fn contextTo(pointer: anytype) LPARAM {
    return @bitCast(@intFromPtr(pointer));
}

// ---------------------------------------------------------------------------
// DPI and geometry
// ---------------------------------------------------------------------------

const DpiState = enum(u8) { pending, running, ready };
var dpi_state = std.atomic.Value(DpiState).init(.pending);

fn enableDpiAwarenessOnce() void {
    // Per-Monitor v2 (Windows 10 1703+) gives true physical pixels on every
    // display; resolved dynamically so the library still loads on older builds.
    if (GetModuleHandleW(std.unicode.utf8ToUtf16LeStringLiteral("user32.dll"))) |user32| {
        if (GetProcAddress(user32, "SetProcessDpiAwarenessContext")) |address| {
            const set_context: DpiContextFn = @ptrCast(address);
            if (set_context(@ptrFromInt(@as(usize, @bitCast(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)))) != 0) return;
        }
    }
    if (SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE) >= 0) return;
    _ = SetProcessDPIAware();
}

pub fn enableDpiAwareness() Error!void {
    if (dpi_state.cmpxchgStrong(.pending, .running, .acq_rel, .acquire) == null) {
        enableDpiAwarenessOnce();
        dpi_state.store(.ready, .release);
        return;
    }
    while (dpi_state.load(.acquire) != .ready) std.atomic.spinLoopHint();
}

pub fn virtualScreen() Error!host.VirtualScreen {
    try enableDpiAwareness();
    var screen = host.VirtualScreen{
        .left = GetSystemMetrics(SM_XVIRTUALSCREEN),
        .top = GetSystemMetrics(SM_YVIRTUALSCREEN),
        .width = GetSystemMetrics(SM_CXVIRTUALSCREEN),
        .height = GetSystemMetrics(SM_CYVIRTUALSCREEN),
    };
    if (screen.width <= 0 or screen.height <= 0) {
        screen = .{
            .left = 0,
            .top = 0,
            .width = GetSystemMetrics(SM_CXSCREEN),
            .height = GetSystemMetrics(SM_CYSCREEN),
        };
    }
    if (screen.width <= 0 or screen.height <= 0) return fail();
    return screen;
}

const MonitorCollector = struct {
    gpa: std.mem.Allocator,
    handles: std.ArrayList(HMONITOR) = .empty,
    rects: std.ArrayList(RECT) = .empty,
    failed: bool = false,

    fn callback(monitor: HMONITOR, _: ?HDC, rect: *RECT, lparam: LPARAM) callconv(.winapi) BOOL {
        const self = contextFrom(MonitorCollector, lparam);
        self.handles.append(self.gpa, monitor) catch {
            self.failed = true;
            return 0;
        };
        self.rects.append(self.gpa, rect.*) catch {
            self.failed = true;
            return 0;
        };
        return 1;
    }
};

pub fn listMonitorsJson() Error![]u8 {
    try enableDpiAwareness();
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    var collector = MonitorCollector{ .gpa = gpa };
    if (EnumDisplayMonitors(null, null, MonitorCollector.callback, contextTo(&collector)) == 0) return fail();
    if (collector.failed) return error.OutOfMemory;

    const entries = gpa.alloc(host.MonitorEntry, collector.handles.items.len) catch return error.OutOfMemory;
    for (collector.handles.items, collector.rects.items, 0..) |monitor, rect, index| {
        var info = std.mem.zeroes(MONITORINFO);
        info.cbSize = @sizeOf(MONITORINFO);
        const primary = GetMonitorInfoW(monitor, &info) != 0 and (info.dwFlags & MONITORINFOF_PRIMARY) != 0;
        var dpi_x: UINT = 96;
        var dpi_y: UINT = 96;
        const scale: f64 = if (GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &dpi_x, &dpi_y) >= 0 and dpi_x > 0)
            @as(f64, @floatFromInt(dpi_x)) / 96.0
        else
            1.0;
        entries[index] = .{
            .index = @intCast(index),
            .left = rect.left,
            .top = rect.top,
            .right = rect.right,
            .bottom = rect.bottom,
            .width = rect.right - rect.left,
            .height = rect.bottom - rect.top,
            .primary = primary,
            .scale = scale,
        };
    }
    return host.monitorsJson(allocator, entries);
}

// ---------------------------------------------------------------------------
// Capture
// ---------------------------------------------------------------------------

fn strideFor(width: i32, bytes_per_pixel: u32) usize {
    const row = @as(usize, @intCast(width)) * bytes_per_pixel;
    return (row + 3) & ~@as(usize, 3);
}

fn validateBpp(bytes_per_pixel: u32) Error!void {
    if (bytes_per_pixel != 3 and bytes_per_pixel != 4) return error.InvalidArgument;
}

const Surface = struct {
    memdc: HDC,
    bitmap: HBITMAP,
    previous: ?HGDIOBJ,

    fn create(source: HDC, width: i32, height: i32) Error!Surface {
        const memdc = CreateCompatibleDC(source) orelse return fail();
        errdefer _ = DeleteDC(memdc);
        const bitmap = CreateCompatibleBitmap(source, width, height) orelse return fail();
        errdefer _ = DeleteObject(bitmap);
        const previous = SelectObject(memdc, bitmap);
        return .{ .memdc = memdc, .bitmap = bitmap, .previous = previous };
    }

    fn readPixels(self: *Surface, width: i32, height: i32, bytes_per_pixel: u32) Error![]u8 {
        // GetDIBits requires the bitmap to be deselected from its DC.
        if (self.previous) |previous| _ = SelectObject(self.memdc, previous);
        self.previous = null;
        var info = std.mem.zeroes(BITMAPINFO);
        info.bmiHeader.biSize = @sizeOf(BITMAPINFOHEADER);
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = @intCast(bytes_per_pixel * 8);
        info.bmiHeader.biCompression = BI_RGB;
        const stride = strideFor(width, bytes_per_pixel);
        const bytes = allocator.alloc(u8, stride * @as(usize, @intCast(height))) catch return error.OutOfMemory;
        errdefer allocator.free(bytes);
        if (GetDIBits(self.memdc, self.bitmap, 0, @intCast(height), bytes.ptr, &info, DIB_RGB_COLORS) == 0) return fail();
        return bytes;
    }

    fn destroy(self: *Surface) void {
        if (self.previous) |previous| _ = SelectObject(self.memdc, previous);
        _ = DeleteObject(self.bitmap);
        _ = DeleteDC(self.memdc);
    }
};

fn captureFromScreen(left: i32, top: i32, width: i32, height: i32, bytes_per_pixel: u32) Error![]u8 {
    const screen_dc = GetDC(null) orelse return fail();
    defer _ = ReleaseDC(null, screen_dc);
    var surface = try Surface.create(screen_dc, width, height);
    defer surface.destroy();
    if (BitBlt(surface.memdc, 0, 0, width, height, screen_dc, left, top, SRCCOPY) == 0) return fail();
    return surface.readPixels(width, height, bytes_per_pixel);
}

pub fn captureVirtualScreen(bytes_per_pixel: u32) Error!host.Pixels {
    try validateBpp(bytes_per_pixel);
    const screen = try virtualScreen();
    const bytes = try captureFromScreen(screen.left, screen.top, screen.width, screen.height, bytes_per_pixel);
    return .{
        .bytes = bytes,
        .width = screen.width,
        .height = screen.height,
        .stride = strideFor(screen.width, bytes_per_pixel),
        .left = screen.left,
        .top = screen.top,
    };
}

pub fn captureRegion(left: i32, top: i32, width: i32, height: i32, bytes_per_pixel: u32) Error!host.Pixels {
    try validateBpp(bytes_per_pixel);
    if (width <= 0 or height <= 0) return error.InvalidArgument;
    try enableDpiAwareness();
    const bytes = try captureFromScreen(left, top, width, height, bytes_per_pixel);
    return .{
        .bytes = bytes,
        .width = width,
        .height = height,
        .stride = strideFor(width, bytes_per_pixel),
        .left = left,
        .top = top,
    };
}

pub fn printWindow(raw_hwnd: u64, bytes_per_pixel: u32) Error!host.Pixels {
    try validateBpp(bytes_per_pixel);
    const hwnd = try hwndFrom(raw_hwnd);
    try enableDpiAwareness();
    const rect = try windowRect(raw_hwnd);
    const width = rect.right - rect.left;
    const height = rect.bottom - rect.top;
    if (width < 2 or height < 2) return error.InvalidArgument;

    const window_dc = GetWindowDC(hwnd) orelse return fail();
    defer _ = ReleaseDC(hwnd, window_dc);
    var surface = try Surface.create(window_dc, width, height);
    defer surface.destroy();
    // PW_RENDERFULLCONTENT captures DirectComposition / WebView content;
    // the plain call is the fallback for windows that reject the flag.
    if (PrintWindow(hwnd, surface.memdc, PW_RENDERFULLCONTENT) == 0 and PrintWindow(hwnd, surface.memdc, 0) == 0) {
        return fail();
    }
    const bytes = try surface.readPixels(width, height, bytes_per_pixel);
    return .{
        .bytes = bytes,
        .width = width,
        .height = height,
        .stride = strideFor(width, bytes_per_pixel),
        .left = rect.left,
        .top = rect.top,
    };
}

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------

fn mouseInput(dx: i32, dy: i32, data: u32, flags: DWORD) INPUT {
    return .{ .type = INPUT_MOUSE, .u = .{ .mi = .{
        .dx = dx,
        .dy = dy,
        .mouseData = data,
        .dwFlags = flags,
        .time = 0,
        .dwExtraInfo = 0,
    } } };
}

fn keyInput(vk: WORD, scan: WORD, flags: DWORD) INPUT {
    return .{ .type = INPUT_KEYBOARD, .u = .{ .ki = .{
        .wVk = vk,
        .wScan = scan,
        .dwFlags = flags,
        .time = 0,
        .dwExtraInfo = 0,
    } } };
}

fn sendInputs(inputs: []const INPUT) Error!void {
    const sent = SendInput(@intCast(inputs.len), inputs.ptr, @sizeOf(INPUT));
    if (sent != inputs.len) return fail();
}

fn buttonFlags(button: host.MouseButton) struct { down: DWORD, up: DWORD } {
    return switch (button) {
        .left => .{ .down = MOUSEEVENTF_LEFTDOWN, .up = MOUSEEVENTF_LEFTUP },
        .right => .{ .down = MOUSEEVENTF_RIGHTDOWN, .up = MOUSEEVENTF_RIGHTUP },
        .middle => .{ .down = MOUSEEVENTF_MIDDLEDOWN, .up = MOUSEEVENTF_MIDDLEUP },
    };
}

pub fn mouseMove(x: i32, y: i32) Error!void {
    const screen = try virtualScreen();
    const abs = host.absCoords(x, y, screen);
    try sendInputs(&.{mouseInput(abs.x, abs.y, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)});
}

pub fn mouseClick(x: i32, y: i32, button: host.MouseButton, clicks: u32) Error!void {
    try mouseMove(x, y);
    Sleep(20);
    const flags = buttonFlags(button);
    var remaining: u32 = @max(clicks, 1);
    while (remaining > 0) : (remaining -= 1) {
        try sendInputs(&.{ mouseInput(0, 0, 0, flags.down), mouseInput(0, 0, 0, flags.up) });
        Sleep(40);
    }
}

pub fn mouseButton(button: host.MouseButton, pressed: bool) Error!void {
    const flags = buttonFlags(button);
    try sendInputs(&.{mouseInput(0, 0, 0, if (pressed) flags.down else flags.up)});
}

pub fn mouseDrag(x1: i32, y1: i32, x2: i32, y2: i32, steps: u32) Error!void {
    // Press, move through intermediate points, pause, release: Explorer and
    // list views ignore an instant jump and need a beat before button-up.
    try mouseMove(x1, y1);
    Sleep(20);
    try sendInputs(&.{mouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN)});
    Sleep(50);
    const n: u32 = @max(steps, 2);
    var i: u32 = 1;
    while (i <= n) : (i += 1) {
        const t: f64 = @as(f64, @floatFromInt(i)) / @as(f64, @floatFromInt(n));
        const fx = @as(f64, @floatFromInt(x1)) + @as(f64, @floatFromInt(x2 - x1)) * t;
        const fy = @as(f64, @floatFromInt(y1)) + @as(f64, @floatFromInt(y2 - y1)) * t;
        try mouseMove(@intFromFloat(fx), @intFromFloat(fy));
        Sleep(12);
    }
    Sleep(120);
    try sendInputs(&.{mouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP)});
}

pub fn mouseScroll(x: i32, y: i32, dx: i32, dy: i32) Error!void {
    try mouseMove(x, y);
    Sleep(20);
    if (dy != 0) {
        const data: u32 = @bitCast(dy *% WHEEL_DELTA);
        try sendInputs(&.{mouseInput(0, 0, data, MOUSEEVENTF_WHEEL)});
    }
    if (dx != 0) {
        const data: u32 = @bitCast(dx *% WHEEL_DELTA);
        try sendInputs(&.{mouseInput(0, 0, data, MOUSEEVENTF_HWHEEL)});
    }
}

fn pressReturn() Error!void {
    try sendInputs(&.{ keyInput(VK_RETURN, 0, 0), keyInput(VK_RETURN, 0, KEYEVENTF_KEYUP) });
}

pub fn typeText(utf8: []const u8, per_char_delay_ms: u32) Error!void {
    const view = std.unicode.Utf8View.init(utf8) catch return error.InvalidArgument;
    var iterator = view.iterator();
    while (iterator.nextCodepoint()) |codepoint| {
        if (codepoint == '\r') {
            // "\r\n" is one line break; the '\n' sends it.
            if (iterator.i < utf8.len and utf8[iterator.i] == '\n') continue;
            try pressReturn();
        } else if (codepoint == '\n') {
            // Many apps ignore U+000A as a character; send a real Return.
            try pressReturn();
        } else {
            const units = host.codepointToUtf16(codepoint);
            if (units.count() == 1) {
                try sendInputs(&.{
                    keyInput(0, units.high, KEYEVENTF_UNICODE),
                    keyInput(0, units.high, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                });
            } else {
                try sendInputs(&.{
                    keyInput(0, units.high, KEYEVENTF_UNICODE),
                    keyInput(0, units.low, KEYEVENTF_UNICODE),
                    keyInput(0, units.high, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                    keyInput(0, units.low, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                });
            }
        }
        if (per_char_delay_ms > 0) Sleep(per_char_delay_ms);
    }
}

pub fn keyCombo(vks: []const u16) Error!void {
    for (vks) |vk| try sendInputs(&.{keyInput(vk, 0, 0)});
    var index = vks.len;
    while (index > 0) {
        index -= 1;
        try sendInputs(&.{keyInput(vks[index], 0, KEYEVENTF_KEYUP)});
    }
}

pub fn keyHold(vk: u16, hold_ms: u32) Error!void {
    try sendInputs(&.{keyInput(vk, 0, 0)});
    defer sendInputs(&.{keyInput(vk, 0, KEYEVENTF_KEYUP)}) catch {};
    Sleep(hold_ms);
}

pub fn vkKeyScan(codepoint: u32) Error!i32 {
    if (codepoint > 0xFFFF) return -1;
    return VkKeyScanW(@intCast(codepoint));
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

fn windowText(gpa: std.mem.Allocator, hwnd: HWND) Error![]u8 {
    const length = GetWindowTextLengthW(hwnd);
    if (length <= 0) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    const wide = gpa.alloc(u16, @as(usize, @intCast(length)) + 1) catch return error.OutOfMemory;
    const copied = GetWindowTextW(hwnd, wide.ptr, length + 1);
    if (copied <= 0) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    return host.utf16ToUtf8(gpa, wide[0..@intCast(copied)]);
}

fn className(gpa: std.mem.Allocator, hwnd: HWND) Error![]u8 {
    var wide: [256]u16 = undefined;
    const copied = GetClassNameW(hwnd, &wide, wide.len);
    if (copied <= 0) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    return host.utf16ToUtf8(gpa, wide[0..@intCast(copied)]);
}

pub fn windowRect(raw_hwnd: u64) Error!host.Rect {
    const hwnd = try hwndFrom(raw_hwnd);
    var rect: RECT = undefined;
    if (GetWindowRect(hwnd, &rect) == 0) return fail();
    return .{ .left = rect.left, .top = rect.top, .right = rect.right, .bottom = rect.bottom };
}

pub fn windowClass(raw_hwnd: u64) Error![]u8 {
    const hwnd = try hwndFrom(raw_hwnd);
    return className(allocator, hwnd);
}

const WindowCollector = struct {
    gpa: std.mem.Allocator,
    limit: usize,
    entries: std.ArrayList(host.WindowEntry) = .empty,
    err: ?Error = null,

    fn callback(hwnd: HWND, lparam: LPARAM) callconv(.winapi) BOOL {
        const self = contextFrom(WindowCollector, lparam);
        if (self.entries.items.len >= self.limit) return 0;
        self.consider(hwnd) catch |err| {
            self.err = err;
            return 0;
        };
        return 1;
    }

    fn consider(self: *WindowCollector, hwnd: HWND) Error!void {
        if (IsWindowVisible(hwnd) == 0) return;
        const raw_title = try windowText(self.gpa, hwnd);
        const title = host.trimWhitespace(raw_title);
        if (title.len == 0) return;
        var rect: RECT = undefined;
        if (GetWindowRect(hwnd, &rect) == 0) return;
        const width = rect.right - rect.left;
        const height = rect.bottom - rect.top;
        if (width < 8 or height < 8) return;
        var pid: DWORD = 0;
        _ = GetWindowThreadProcessId(hwnd, &pid);
        self.entries.append(self.gpa, .{
            .hwnd = hwndValue(hwnd),
            .title = host.truncateCodepoints(title, 200),
            .class = try className(self.gpa, hwnd),
            .pid = pid,
            .bounds = .{ .left = rect.left, .top = rect.top, .right = rect.right, .bottom = rect.bottom },
            .width = width,
            .height = height,
            .visible = true,
            .minimized = IsIconic(hwnd) != 0,
        }) catch return error.OutOfMemory;
    }
};

pub fn listWindowsJson(limit: u32) Error![]u8 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    var collector = WindowCollector{ .gpa = arena.allocator(), .limit = @max(limit, 1) };
    _ = EnumWindows(WindowCollector.callback, contextTo(&collector));
    if (collector.err) |err| return err;
    return host.windowsJson(allocator, collector.entries.items);
}

pub const ForegroundInfo = struct { hwnd: u64, title: []u8 };

pub fn foregroundWindow() Error!ForegroundInfo {
    const hwnd = GetForegroundWindow() orelse return .{
        .hwnd = 0,
        .title = allocator.dupe(u8, "") catch return error.OutOfMemory,
    };
    return .{ .hwnd = hwndValue(hwnd), .title = try windowText(allocator, hwnd) };
}

fn foregroundIs(hwnd: HWND) bool {
    const fg = GetForegroundWindow() orelse return false;
    if (fg == hwnd) return true;
    // Owned/child relationship either way counts (dialogs, WebView hosts).
    return GetAncestor(fg, GA_ROOTOWNER) == GetAncestor(hwnd, GA_ROOTOWNER);
}

pub fn focusWindow(raw_hwnd: u64) Error!bool {
    const hwnd = try hwndFrom(raw_hwnd);
    _ = ShowWindow(hwnd, SW_RESTORE);
    _ = SetForegroundWindow(hwnd);
    if (foregroundIs(hwnd)) return true;
    // Foreground lock: attach our thread's input state to the foreground thread.
    if (GetForegroundWindow()) |fg| {
        const fg_thread = GetWindowThreadProcessId(fg, null);
        const my_thread = GetCurrentThreadId();
        if (fg_thread != 0 and fg_thread != my_thread and AttachThreadInput(my_thread, fg_thread, 1) != 0) {
            _ = BringWindowToTop(hwnd);
            _ = SetForegroundWindow(hwnd);
            _ = AttachThreadInput(my_thread, fg_thread, 0);
        }
    }
    if (foregroundIs(hwnd)) return true;
    // Last resort: a brief ALT tap releases the foreground lock for us.
    sendInputs(&.{ keyInput(VK_MENU, 0, 0), keyInput(VK_MENU, 0, KEYEVENTF_KEYUP) }) catch {};
    _ = SetForegroundWindow(hwnd);
    Sleep(50);
    return foregroundIs(hwnd);
}

pub fn manageWindow(raw_hwnd: u64, action: host.WindowAction, x: i32, y: i32, width: i32, height: i32) Error!void {
    const hwnd = try hwndFrom(raw_hwnd);
    if (IsWindow(hwnd) == 0) return error.InvalidArgument;
    switch (action) {
        .minimize => _ = ShowWindow(hwnd, SW_MINIMIZE),
        .maximize => _ = ShowWindow(hwnd, SW_MAXIMIZE),
        .restore => _ = ShowWindow(hwnd, SW_RESTORE),
        .close => if (PostMessageW(hwnd, WM_CLOSE, 0, 0) == 0) return fail(),
        .move_resize => {
            if (width <= 0 or height <= 0) return error.InvalidArgument;
            if (SetWindowPos(hwnd, null, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE) == 0) return fail();
        },
    }
}

const ChildFinder = struct {
    gpa: std.mem.Allocator,
    class_substr: []const u8,
    title_substr: []const u8,
    found: ?HWND = null,
    err: ?Error = null,

    fn callback(hwnd: HWND, lparam: LPARAM) callconv(.winapi) BOOL {
        const self = contextFrom(ChildFinder, lparam);
        const matched = self.matches(hwnd) catch |err| {
            self.err = err;
            return 0;
        };
        if (!matched) return 1;
        self.found = hwnd;
        return 0;
    }

    fn matches(self: *ChildFinder, hwnd: HWND) Error!bool {
        if (self.class_substr.len != 0) {
            const class = try className(self.gpa, hwnd);
            if (!host.containsIgnoreCase(class, self.class_substr)) return false;
        }
        if (self.title_substr.len != 0) {
            const title = try windowText(self.gpa, hwnd);
            if (!host.containsIgnoreCase(title, self.title_substr)) return false;
        }
        return true;
    }
};

pub fn findChildHwnd(raw_parent: u64, class_substr: []const u8, title_substr: []const u8) Error!u64 {
    const parent = try hwndFrom(raw_parent);
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    var finder = ChildFinder{
        .gpa = arena.allocator(),
        .class_substr = class_substr,
        .title_substr = title_substr,
    };
    _ = EnumChildWindows(parent, ChildFinder.callback, contextTo(&finder));
    if (finder.err) |err| return err;
    return hwndValue(finder.found);
}

// ---------------------------------------------------------------------------
// Clipboard
// ---------------------------------------------------------------------------

fn openClipboardRetry() Error!void {
    // The clipboard can be briefly held by another app.
    var attempt: u32 = 0;
    while (attempt < 5) : (attempt += 1) {
        if (OpenClipboard(null) != 0) return;
        Sleep(20);
    }
    return fail();
}

pub fn clipboardGetText() Error![]u8 {
    try openClipboardRetry();
    defer _ = CloseClipboard();
    const handle = GetClipboardData(CF_UNICODETEXT) orelse return allocator.dupe(u8, "") catch return error.OutOfMemory;
    const locked = GlobalLock(handle) orelse return allocator.dupe(u8, "") catch return error.OutOfMemory;
    defer _ = GlobalUnlock(handle);
    const wide: [*:0]const u16 = @ptrCast(@alignCast(locked));
    return host.utf16ToUtf8(allocator, wide[0..std.mem.len(wide)]);
}

pub fn clipboardSetText(utf8: []const u8) Error!void {
    const wide = try host.utf8ToUtf16Z(allocator, utf8);
    defer allocator.free(wide);
    try openClipboardRetry();
    defer _ = CloseClipboard();
    if (EmptyClipboard() == 0) return fail();
    const bytes = (wide.len + 1) * @sizeOf(u16);
    const handle = GlobalAlloc(GMEM_MOVEABLE, bytes) orelse return fail();
    const locked = GlobalLock(handle) orelse {
        const err = fail();
        _ = GlobalFree(handle);
        return err;
    };
    const destination: [*]u8 = @ptrCast(locked);
    @memcpy(destination[0..bytes], @as([*]const u8, @ptrCast(wide.ptr))[0..bytes]);
    _ = GlobalUnlock(handle);
    if (SetClipboardData(CF_UNICODETEXT, handle) == null) {
        const err = fail();
        _ = GlobalFree(handle);
        return err;
    }
}

// ---------------------------------------------------------------------------
// Processes
// ---------------------------------------------------------------------------

const Process = struct {
    process: HANDLE,
    job: HANDLE,
    pid: u32,
};

pub const Spawned = struct { pid: u32, handle: u64 };
pub const WaitOutcome = struct { exited: bool, exit_code: u32 };

fn processFrom(handle: u64) Error!*Process {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(handle)));
}

/// Fire-and-forget hidden CreateProcess with no job object. The child keeps
/// running after this returns (installer UAC flows). Returns only the pid.
pub fn spawnDetached(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!u32 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0) null else (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.parseEnv(gpa, env_json)) |pairs|
        @ptrCast((try host.envBlock(gpa, pairs)).ptr)
    else
        null;

    var startup = std.mem.zeroes(STARTUPINFOW);
    startup.cb = @sizeOf(STARTUPINFOW);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    var info: PROCESS_INFORMATION = undefined;
    const flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT;
    if (CreateProcessW(null, command_line.ptr, null, null, 0, flags, environment, directory, &startup, &info) == 0) {
        return fail();
    }
    _ = CloseHandle(info.hThread);
    _ = CloseHandle(info.hProcess);
    return info.dwProcessId;
}

/// Create a hidden process inside a job that kills every descendant when the
/// job handle closes, so `uv.exe -> python.exe` style trees never outlive us.
pub fn spawnHidden(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!Spawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0) null else (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.parseEnv(gpa, env_json)) |pairs|
        @ptrCast((try host.envBlock(gpa, pairs)).ptr)
    else
        null;

    const job = CreateJobObjectW(null, null) orelse return fail();
    errdefer _ = CloseHandle(job);
    var limits = std.mem.zeroes(JOBOBJECT_EXTENDED_LIMIT_INFORMATION);
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, @sizeOf(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)) == 0) {
        return fail();
    }

    var startup = std.mem.zeroes(STARTUPINFOW);
    startup.cb = @sizeOf(STARTUPINFOW);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    var info: PROCESS_INFORMATION = undefined;
    const flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    if (CreateProcessW(null, command_line.ptr, null, null, 0, flags, environment, directory, &startup, &info) == 0) {
        return fail();
    }
    errdefer {
        _ = TerminateProcess(info.hProcess, 1);
        _ = CloseHandle(info.hThread);
        _ = CloseHandle(info.hProcess);
    }
    if (AssignProcessToJobObject(job, info.hProcess) == 0) return fail();
    if (ResumeThread(info.hThread) == std.math.maxInt(DWORD)) return fail();
    _ = CloseHandle(info.hThread);

    const process = allocator.create(Process) catch return error.OutOfMemory;
    process.* = .{ .process = info.hProcess, .job = job, .pid = info.dwProcessId };
    return .{ .pid = info.dwProcessId, .handle = @intFromPtr(process) };
}

pub fn processWait(handle: u64, timeout_ms: u32) Error!WaitOutcome {
    const process = try processFrom(handle);
    switch (WaitForSingleObject(process.process, timeout_ms)) {
        WAIT_OBJECT_0 => {
            var code: DWORD = 0;
            if (GetExitCodeProcess(process.process, &code) == 0) return fail();
            return .{ .exited = true, .exit_code = code };
        },
        WAIT_TIMEOUT => return .{ .exited = false, .exit_code = 0 },
        else => return fail(),
    }
}

pub fn processClose(handle: u64) Error!void {
    const process = try processFrom(handle);
    _ = CloseHandle(process.process);
    // Closing the last job handle terminates whatever is still running in it.
    _ = CloseHandle(process.job);
    allocator.destroy(process);
}

// ---------------------------------------------------------------------------
// Interactive 3-pipe spawn (stdin / stdout / stderr separate; Python owns pipes)
// ---------------------------------------------------------------------------

pub const PipedSpawned = struct {
    pid: u32,
    handle: u64, // Process* — wait/close via processWait/processClose
    stdin_write: u64,
    stdout_read: u64,
    stderr_read: u64,
};

/// Hidden CreateProcess with separate stdin/stdout/stderr pipes inside a
/// kill-on-close job. Parent ends are non-inheritable and transferred to the
/// caller (not closed by `processClose`). Equal-or-better than Popen pipes.
pub fn spawnPiped3(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!PipedSpawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0) null else (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.parseEnv(gpa, env_json)) |pairs|
        @ptrCast((try host.envBlock(gpa, pairs)).ptr)
    else
        null;

    var sa = SECURITY_ATTRIBUTES{
        .nLength = @sizeOf(SECURITY_ATTRIBUTES),
        .lpSecurityDescriptor = null,
        .bInheritHandle = 1,
    };

    var child_stdin: ?HANDLE = null;
    var parent_stdin: ?HANDLE = null;
    if (CreatePipe(&child_stdin, &parent_stdin, &sa, 0) == 0) return fail();
    if (SetHandleInformation(parent_stdin.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }

    var parent_stdout: ?HANDLE = null;
    var child_stdout: ?HANDLE = null;
    if (CreatePipe(&parent_stdout, &child_stdout, &sa, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }
    if (SetHandleInformation(parent_stdout.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    }

    var parent_stderr: ?HANDLE = null;
    var child_stderr: ?HANDLE = null;
    if (CreatePipe(&parent_stderr, &child_stderr, &sa, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    }
    if (SetHandleInformation(parent_stderr.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        return fail();
    }

    const job = CreateJobObjectW(null, null) orelse {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        return fail();
    };
    var limits = std.mem.zeroes(JOBOBJECT_EXTENDED_LIMIT_INFORMATION);
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, @sizeOf(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        _ = CloseHandle(job);
        return fail();
    }

    var startup = std.mem.zeroes(STARTUPINFOW);
    startup.cb = @sizeOf(STARTUPINFOW);
    startup.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES;
    startup.wShowWindow = SW_HIDE;
    startup.hStdInput = child_stdin;
    startup.hStdOutput = child_stdout;
    startup.hStdError = child_stderr;
    var info: PROCESS_INFORMATION = undefined;
    const flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    if (CreateProcessW(null, command_line.ptr, null, null, 1, flags, environment, directory, &startup, &info) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        _ = CloseHandle(job);
        return fail();
    }
    closeOptHandle(child_stdin);
    closeOptHandle(child_stdout);
    closeOptHandle(child_stderr);
    errdefer {
        _ = TerminateProcess(info.hProcess, 1);
        _ = CloseHandle(info.hThread);
        _ = CloseHandle(info.hProcess);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(parent_stderr);
        _ = CloseHandle(job);
    }
    if (AssignProcessToJobObject(job, info.hProcess) == 0) return fail();
    if (ResumeThread(info.hThread) == std.math.maxInt(DWORD)) return fail();
    _ = CloseHandle(info.hThread);

    const process = allocator.create(Process) catch return error.OutOfMemory;
    process.* = .{ .process = info.hProcess, .job = job, .pid = info.dwProcessId };
    return .{
        .pid = info.dwProcessId,
        .handle = @intFromPtr(process),
        .stdin_write = @intFromPtr(parent_stdin.?),
        .stdout_read = @intFromPtr(parent_stdout.?),
        .stderr_read = @intFromPtr(parent_stderr.?),
    };
}

// ---------------------------------------------------------------------------
// Interactive piped spawn (stdin/stdout for HostSession; stderr→stdout)
// ---------------------------------------------------------------------------

const Piped = struct {
    process: HANDLE,
    job: HANDLE,
    stdin_write: ?HANDLE = null,
    stdout_read: ?HANDLE = null,
    pid: u32,
    exit_code: ?u32 = null,
};

fn pipedFrom(handle: u64) Error!*Piped {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(handle)));
}

fn closeOptHandle(handle: ?HANDLE) void {
    if (handle) |h| _ = CloseHandle(h);
}

/// Hidden CreateProcess with redirected stdin/stdout (stderr merged), job-kill
/// on close. Parent ends are non-inheritable. Handle is a Piped* freed by
/// `pipedClose`.
pub fn spawnPiped(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!Spawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0) null else (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.parseEnv(gpa, env_json)) |pairs|
        @ptrCast((try host.envBlock(gpa, pairs)).ptr)
    else
        null;

    var sa = SECURITY_ATTRIBUTES{
        .nLength = @sizeOf(SECURITY_ATTRIBUTES),
        .lpSecurityDescriptor = null,
        .bInheritHandle = 1,
    };

    var child_stdin: ?HANDLE = null;
    var parent_stdin: ?HANDLE = null;
    if (CreatePipe(&child_stdin, &parent_stdin, &sa, 0) == 0) return fail();
    if (SetHandleInformation(parent_stdin.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }

    var parent_stdout: ?HANDLE = null;
    var child_stdout: ?HANDLE = null;
    if (CreatePipe(&parent_stdout, &child_stdout, &sa, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }
    if (SetHandleInformation(parent_stdout.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    }

    const job = CreateJobObjectW(null, null) orelse {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    };
    var limits = std.mem.zeroes(JOBOBJECT_EXTENDED_LIMIT_INFORMATION);
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, @sizeOf(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        _ = CloseHandle(job);
        return fail();
    }

    var startup = std.mem.zeroes(STARTUPINFOW);
    startup.cb = @sizeOf(STARTUPINFOW);
    startup.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES;
    startup.wShowWindow = SW_HIDE;
    startup.hStdInput = child_stdin;
    startup.hStdOutput = child_stdout;
    startup.hStdError = child_stdout;
    var info: PROCESS_INFORMATION = undefined;
    const flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    if (CreateProcessW(null, command_line.ptr, null, null, 1, flags, environment, directory, &startup, &info) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        _ = CloseHandle(job);
        return fail();
    }
    // Child inherited its ends; parent must not keep them.
    closeOptHandle(child_stdin);
    closeOptHandle(child_stdout);
    errdefer {
        _ = TerminateProcess(info.hProcess, 1);
        _ = CloseHandle(info.hThread);
        _ = CloseHandle(info.hProcess);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        _ = CloseHandle(job);
    }
    if (AssignProcessToJobObject(job, info.hProcess) == 0) return fail();
    if (ResumeThread(info.hThread) == std.math.maxInt(DWORD)) return fail();
    _ = CloseHandle(info.hThread);

    const piped = allocator.create(Piped) catch return error.OutOfMemory;
    piped.* = .{
        .process = info.hProcess,
        .job = job,
        .stdin_write = parent_stdin,
        .stdout_read = parent_stdout,
        .pid = info.dwProcessId,
        .exit_code = null,
    };
    return .{ .pid = info.dwProcessId, .handle = @intFromPtr(piped) };
}

pub fn pipedWrite(handle: u64, data: []const u8) Error!usize {
    const piped = try pipedFrom(handle);
    const pipe = piped.stdin_write orelse return error.InvalidArgument;
    if (data.len == 0) return 0;
    var written: DWORD = 0;
    if (WriteFile(pipe, data.ptr, @intCast(data.len), &written, null) == 0) return fail();
    return written;
}

pub fn pipedReadAvailable(handle: u64, buf: []u8) Error!usize {
    const piped = try pipedFrom(handle);
    const pipe = piped.stdout_read orelse return 0;
    if (buf.len == 0) return 0;
    var avail: DWORD = 0;
    if (PeekNamedPipe(pipe, null, 0, null, &avail, null) == 0) return 0;
    if (avail == 0) return 0;
    const want: DWORD = @intCast(@min(buf.len, @as(usize, @intCast(avail))));
    var got: DWORD = 0;
    if (ReadFile(pipe, buf.ptr, want, &got, null) == 0) return 0;
    return got;
}

pub fn pipedPoll(handle: u64) Error!WaitOutcome {
    const piped = try pipedFrom(handle);
    if (piped.exit_code) |code| return .{ .exited = true, .exit_code = code };
    var code: DWORD = 0;
    if (GetExitCodeProcess(piped.process, &code) == 0) return fail();
    if (code == STILL_ACTIVE) return .{ .exited = false, .exit_code = 0 };
    piped.exit_code = code;
    return .{ .exited = true, .exit_code = code };
}

pub fn pipedKill(handle: u64) Error!void {
    const piped = try pipedFrom(handle);
    _ = TerminateProcess(piped.process, 1);
    if (piped.exit_code == null) piped.exit_code = 1;
    try killTree(piped.pid);
}

pub fn pipedClose(handle: u64) Error!void {
    const piped = try pipedFrom(handle);
    closeOptHandle(piped.stdin_write);
    piped.stdin_write = null;
    closeOptHandle(piped.stdout_read);
    piped.stdout_read = null;
    _ = CloseHandle(piped.process);
    _ = CloseHandle(piped.job);
    allocator.destroy(piped);
}

// ---------------------------------------------------------------------------
// One-shot capture (stdout + stderr pipes, job kill-tree on close / timeout)
// ---------------------------------------------------------------------------

pub const CaptureResult = struct {
    exit_code: u32,
    timed_out: bool,
    stdout: []u8,
    stderr: []u8,
};

fn drainPipe(pipe: HANDLE, buf: *std.ArrayList(u8), max_bytes: usize) Error!void {
    var scratch: [8192]u8 = undefined;
    while (true) {
        var avail: DWORD = 0;
        if (PeekNamedPipe(pipe, null, 0, null, &avail, null) == 0) return;
        if (avail == 0) return;
        if (buf.items.len >= max_bytes) {
            // Discard remaining so the child does not block on a full pipe.
            var dump: DWORD = 0;
            _ = ReadFile(pipe, &scratch, @intCast(@min(scratch.len, @as(usize, avail))), &dump, null);
            continue;
        }
        const room = max_bytes - buf.items.len;
        const want: DWORD = @intCast(@min(scratch.len, @min(room, @as(usize, avail))));
        var got: DWORD = 0;
        if (ReadFile(pipe, &scratch, want, &got, null) == 0 or got == 0) return;
        buf.appendSlice(allocator, scratch[0..got]) catch return error.OutOfMemory;
    }
}

/// Hidden CreateProcess with separate stdout/stderr pipes inside a kill-on-close
/// job. Waits up to `timeout_ms` (0 → 60s). On timeout: TerminateProcess +
/// killTree, exit_code=1, timed_out=true. Caller owns stdout/stderr via
/// `allocator` (same as other host buffers).
pub fn execCapture(
    argv_json: []const u8,
    cwd: []const u8,
    env_json: []const u8,
    timeout_ms: u32,
    max_bytes: usize,
) Error!CaptureResult {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    const command_line = try host.commandLine(gpa, argv);
    const directory: ?[*:0]const u16 = if (cwd.len == 0) null else (try host.utf8ToUtf16Z(gpa, cwd)).ptr;
    const environment: ?*anyopaque = if (try host.parseEnv(gpa, env_json)) |pairs|
        @ptrCast((try host.envBlock(gpa, pairs)).ptr)
    else
        null;

    var sa = SECURITY_ATTRIBUTES{
        .nLength = @sizeOf(SECURITY_ATTRIBUTES),
        .lpSecurityDescriptor = null,
        .bInheritHandle = 1,
    };

    var child_stdin: ?HANDLE = null;
    var parent_stdin: ?HANDLE = null;
    if (CreatePipe(&child_stdin, &parent_stdin, &sa, 0) == 0) return fail();
    if (SetHandleInformation(parent_stdin.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }

    var parent_stdout: ?HANDLE = null;
    var child_stdout: ?HANDLE = null;
    if (CreatePipe(&parent_stdout, &child_stdout, &sa, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        return fail();
    }
    if (SetHandleInformation(parent_stdout.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    }

    var parent_stderr: ?HANDLE = null;
    var child_stderr: ?HANDLE = null;
    if (CreatePipe(&parent_stderr, &child_stderr, &sa, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        return fail();
    }
    if (SetHandleInformation(parent_stderr.?, HANDLE_FLAG_INHERIT, 0) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        return fail();
    }

    const job = CreateJobObjectW(null, null) orelse {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        return fail();
    };
    var limits = std.mem.zeroes(JOBOBJECT_EXTENDED_LIMIT_INFORMATION);
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, @sizeOf(JOBOBJECT_EXTENDED_LIMIT_INFORMATION)) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        _ = CloseHandle(job);
        return fail();
    }

    var startup = std.mem.zeroes(STARTUPINFOW);
    startup.cb = @sizeOf(STARTUPINFOW);
    startup.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES;
    startup.wShowWindow = SW_HIDE;
    startup.hStdInput = child_stdin;
    startup.hStdOutput = child_stdout;
    startup.hStdError = child_stderr;
    var info: PROCESS_INFORMATION = undefined;
    const flags = CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED;
    if (CreateProcessW(null, command_line.ptr, null, null, 1, flags, environment, directory, &startup, &info) == 0) {
        closeOptHandle(child_stdin);
        closeOptHandle(parent_stdin);
        closeOptHandle(parent_stdout);
        closeOptHandle(child_stdout);
        closeOptHandle(parent_stderr);
        closeOptHandle(child_stderr);
        _ = CloseHandle(job);
        return fail();
    }
    closeOptHandle(child_stdin);
    closeOptHandle(child_stdout);
    closeOptHandle(child_stderr);
    // Child stdin gets EOF immediately (one-shot; no interactive input).
    closeOptHandle(parent_stdin);
    parent_stdin = null;

    errdefer {
        _ = TerminateProcess(info.hProcess, 1);
        _ = CloseHandle(info.hThread);
        _ = CloseHandle(info.hProcess);
        closeOptHandle(parent_stdout);
        closeOptHandle(parent_stderr);
        _ = CloseHandle(job);
    }
    if (AssignProcessToJobObject(job, info.hProcess) == 0) return fail();
    if (ResumeThread(info.hThread) == std.math.maxInt(DWORD)) return fail();
    _ = CloseHandle(info.hThread);

    const budget: u32 = if (timeout_ms == 0) 60_000 else timeout_ms;
    const limit = if (max_bytes == 0) process_max_output else max_bytes;
    var stdout_buf: std.ArrayList(u8) = .empty;
    errdefer stdout_buf.deinit(allocator);
    var stderr_buf: std.ArrayList(u8) = .empty;
    errdefer stderr_buf.deinit(allocator);

    var timed_out = false;
    var exited = false;
    var exit_code: u32 = 1;
    var waited: u32 = 0;
    const slice_ms: u32 = 50;
    while (!exited) {
        const remaining = if (waited >= budget) 0 else budget - waited;
        const slice = if (remaining == 0) @as(u32, 0) else @min(slice_ms, remaining);
        const wait = WaitForSingleObject(info.hProcess, slice);
        try drainPipe(parent_stdout.?, &stdout_buf, limit);
        try drainPipe(parent_stderr.?, &stderr_buf, limit);
        if (wait == WAIT_OBJECT_0) {
            var code: DWORD = 0;
            if (GetExitCodeProcess(info.hProcess, &code) == 0) return fail();
            exit_code = code;
            exited = true;
            break;
        }
        if (wait != WAIT_TIMEOUT) return fail();
        waited += slice;
        if (waited >= budget) {
            timed_out = true;
            _ = TerminateProcess(info.hProcess, 1);
            killTree(info.dwProcessId) catch {};
            _ = WaitForSingleObject(info.hProcess, 5000);
            try drainPipe(parent_stdout.?, &stdout_buf, limit);
            try drainPipe(parent_stderr.?, &stderr_buf, limit);
            exit_code = 1;
            exited = true;
            break;
        }
    }

    closeOptHandle(parent_stdout);
    closeOptHandle(parent_stderr);
    _ = CloseHandle(info.hProcess);
    _ = CloseHandle(job);

    return .{
        .exit_code = exit_code,
        .timed_out = timed_out,
        .stdout = try stdout_buf.toOwnedSlice(allocator),
        .stderr = try stderr_buf.toOwnedSlice(allocator),
    };
}

const process_max_output: usize = 1024 * 1024;

const ProcessRecord = struct { pid: u32, parent: u32 };

fn creationTime(pid: u32) ?u64 {
    const handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) orelse return null;
    defer _ = CloseHandle(handle);
    var creation: FILETIME = undefined;
    var exit: FILETIME = undefined;
    var kernel: FILETIME = undefined;
    var user: FILETIME = undefined;
    if (GetProcessTimes(handle, &creation, &exit, &kernel, &user) == 0) return null;
    return (@as(u64, creation.dwHighDateTime) << 32) | creation.dwLowDateTime;
}

/// Terminate one process. A process that is already gone counts as success.
fn terminatePid(pid: u32) Error!void {
    const handle = OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, 0, pid) orelse {
        const code = GetLastError();
        if (code == ERROR_INVALID_PARAMETER) return;
        host.setOsError(code);
        return error.OperationFailed;
    };
    defer _ = CloseHandle(handle);
    if (TerminateProcess(handle, 1) != 0) return;
    const code = GetLastError();
    if (WaitForSingleObject(handle, 0) == WAIT_OBJECT_0) return;
    host.setOsError(code);
    return error.OperationFailed;
}

pub fn snapshotProcesses(gpa: std.mem.Allocator) Error![]ProcessRecord {
    const snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0) orelse return fail();
    if (snapshot == invalid_handle_value) return fail();
    defer _ = CloseHandle(snapshot);
    var records: std.ArrayList(ProcessRecord) = .empty;
    var entry = std.mem.zeroes(PROCESSENTRY32W);
    entry.dwSize = @sizeOf(PROCESSENTRY32W);
    if (Process32FirstW(snapshot, &entry) == 0) return fail();
    while (true) {
        records.append(gpa, .{ .pid = entry.th32ProcessID, .parent = entry.th32ParentProcessID }) catch return error.OutOfMemory;
        if (Process32NextW(snapshot, &entry) == 0) break;
    }
    return records.toOwnedSlice(gpa) catch return error.OutOfMemory;
}

/// Descendants of `root` in breadth-first order, guarded against PID reuse:
/// a child must have been created after the parent it claims.
pub fn descendants(gpa: std.mem.Allocator, root: u32) Error![]u32 {
    var arena = std.heap.ArenaAllocator.init(gpa);
    defer arena.deinit();
    const records = try snapshotProcesses(arena.allocator());
    var victims: std.ArrayList(u32) = .empty;
    errdefer victims.deinit(gpa);
    victims.append(gpa, root) catch return error.OutOfMemory;
    var index: usize = 0;
    while (index < victims.items.len) : (index += 1) {
        const parent = victims.items[index];
        const parent_created = creationTime(parent);
        for (records) |record| {
            if (record.parent != parent or record.pid == parent) continue;
            if (std.mem.indexOfScalar(u32, victims.items, record.pid) != null) continue;
            if (parent_created) |after| {
                const child_created = creationTime(record.pid) orelse continue;
                if (child_created < after) continue;
            }
            victims.append(gpa, record.pid) catch return error.OutOfMemory;
        }
    }
    return victims.toOwnedSlice(gpa) catch return error.OutOfMemory;
}

/// Kill `pid` and every descendant, deepest first, so nothing is re-parented
/// and left running. Replaces `taskkill /T /F`.
pub fn killTree(pid: u32) Error!void {
    if (pid == 0) return error.InvalidArgument;
    const victims = try descendants(allocator, pid);
    defer allocator.free(victims);
    var index = victims.len;
    while (index > 1) {
        index -= 1;
        terminatePid(victims[index]) catch {};
    }
    try terminatePid(victims[0]);
}

// ---------------------------------------------------------------------------
// Tests (Windows only; nothing here injects input or touches the clipboard)
// ---------------------------------------------------------------------------

test "monitors and the virtual screen are consistent" {
    const json = try listMonitorsJson();
    defer allocator.free(json);
    const parsed = try std.json.parseFromSlice([]const host.MonitorEntry, std.testing.allocator, json, .{});
    defer parsed.deinit();
    try std.testing.expect(parsed.value.len >= 1);
    var primaries: usize = 0;
    for (parsed.value) |monitor| {
        try std.testing.expect(monitor.width > 0 and monitor.height > 0);
        try std.testing.expect(monitor.scale >= 1.0 and monitor.scale <= 4.0);
        if (monitor.primary) primaries += 1;
    }
    try std.testing.expectEqual(@as(usize, 1), primaries);
    const screen = try virtualScreen();
    try std.testing.expect(screen.width > 0 and screen.height > 0);
}

test "virtual screen capture has the reported geometry in both layouts" {
    const bgr = try captureVirtualScreen(3);
    defer allocator.free(bgr.bytes);
    try std.testing.expectEqual(strideFor(bgr.width, 3), bgr.stride);
    try std.testing.expectEqual(bgr.stride * @as(usize, @intCast(bgr.height)), bgr.bytes.len);
    const bgra = try captureVirtualScreen(4);
    defer allocator.free(bgra.bytes);
    try std.testing.expectEqual(@as(usize, @intCast(bgra.width)) * 4, bgra.stride);
    try std.testing.expectEqual(bgr.width, bgra.width);
    try std.testing.expectEqual(bgr.height, bgra.height);
    const region = try captureRegion(bgr.left, bgr.top, 16, 8, 3);
    defer allocator.free(region.bytes);
    try std.testing.expectEqual(@as(usize, 48), region.stride);
    try std.testing.expectEqual(@as(usize, 48 * 8), region.bytes.len);
    try std.testing.expectError(error.InvalidArgument, captureRegion(0, 0, 0, 8, 3));
    try std.testing.expectError(error.InvalidArgument, captureVirtualScreen(2));
}

test "window enumeration yields well-formed entries and a class per window" {
    const json = try listWindowsJson(200);
    defer allocator.free(json);
    const parsed = try std.json.parseFromSlice([]const host.WindowEntry, std.testing.allocator, json, .{});
    defer parsed.deinit();
    for (parsed.value) |entry| {
        try std.testing.expect(entry.hwnd != 0);
        try std.testing.expect(entry.title.len > 0);
        try std.testing.expect(entry.width >= 8 and entry.height >= 8);
        try std.testing.expect(entry.visible);
        const class = try windowClass(entry.hwnd);
        defer allocator.free(class);
        try std.testing.expectEqualStrings(entry.class, class);
        const rect = try windowRect(entry.hwnd);
        try std.testing.expectEqual(entry.bounds, rect);
    }
    try std.testing.expectError(error.InvalidArgument, windowRect(0));
    try std.testing.expectError(error.InvalidArgument, findChildHwnd(0, "x", ""));
    try std.testing.expectError(error.InvalidArgument, manageWindow(0, .minimize, 0, 0, 0, 0));
    const fg = try foregroundWindow();
    defer allocator.free(fg.title);
}

// `timeout.exe` refuses to run without console stdin, which a hidden process
// does not have; `ping -n` waits the same way and keeps the tree alive.
const long_running_tree = "[\"cmd\", \"/c\", \"cmd /c ping -n 40 127.0.0.1 > nul\"]";
const long_running_child = "[\"cmd\", \"/c\", \"ping -n 40 127.0.0.1 > nul\"]";

test "hidden spawn builds a job and kill-tree reaches the grandchild" {
    const spawned = try spawnHidden(long_running_tree, "", "");
    defer processClose(spawned.handle) catch {};
    // The nested cmd needs a moment to start its own child.
    var tree: []u32 = &.{};
    var attempts: u32 = 0;
    while (attempts < 100) : (attempts += 1) {
        tree = try descendants(std.testing.allocator, spawned.pid);
        if (tree.len >= 3) break;
        std.testing.allocator.free(tree);
        tree = &.{};
        Sleep(50);
    }
    defer std.testing.allocator.free(tree);
    try std.testing.expect(tree.len >= 3);
    const before = try processWait(spawned.handle, 0);
    try std.testing.expect(!before.exited);

    try killTree(spawned.pid);
    const after = try processWait(spawned.handle, 5000);
    try std.testing.expect(after.exited);
    // Every descendant must be gone, not merely re-parented.
    for (tree[1..]) |pid| {
        var settle: u32 = 0;
        while (settle < 100) : (settle += 1) {
            const handle = OpenProcess(SYNCHRONIZE, 0, pid) orelse break;
            defer _ = CloseHandle(handle);
            if (WaitForSingleObject(handle, 0) == WAIT_OBJECT_0) break;
            Sleep(20);
        }
        try std.testing.expect(settle < 100);
    }
    // Killing something that is already gone is not an error.
    try killTree(spawned.pid);
    try std.testing.expectError(error.InvalidArgument, killTree(0));
    try std.testing.expectError(error.InvalidArgument, spawnHidden("[]", "", ""));
}

test "closing the job handle kills a running child" {
    const spawned = try spawnHidden(long_running_child, "", "");
    const pid = spawned.pid;
    try processClose(spawned.handle);
    var settle: u32 = 0;
    while (settle < 100) : (settle += 1) {
        const handle = OpenProcess(SYNCHRONIZE, 0, pid) orelse break;
        defer _ = CloseHandle(handle);
        if (WaitForSingleObject(handle, 0) == WAIT_OBJECT_0) break;
        Sleep(20);
    }
    try std.testing.expect(settle < 100);
}

test "spawn honours cwd and environment" {
    const spawned = try spawnHidden(
        "[\"cmd\", \"/c\", \"exit %REMEDY_HOST_TEST%\"]",
        "C:\\",
        "{\"REMEDY_HOST_TEST\": \"7\", \"SystemRoot\": \"C:\\\\Windows\"}",
    );
    defer processClose(spawned.handle) catch {};
    const outcome = try processWait(spawned.handle, 10000);
    try std.testing.expect(outcome.exited);
    try std.testing.expectEqual(@as(u32, 7), outcome.exit_code);
    try std.testing.expectError(error.InvalidArgument, processWait(0, 0));
}

test "spawnPiped3 delivers stdout on a separate pipe" {
    // Echo-only proves the parent stdout handle works; Python tests cover
    // interactive stdin round-trip (JSON-RPC) through the authorized ABI.
    const spawned = try spawnPiped3(
        "[\"cmd\", \"/c\", \"echo piped-ok\"]",
        "",
        "",
    );
    const stdin_h: ?HANDLE = @ptrFromInt(spawned.stdin_write);
    const stdout_h: ?HANDLE = @ptrFromInt(spawned.stdout_read);
    const stderr_h: ?HANDLE = @ptrFromInt(spawned.stderr_read);
    defer {
        closeOptHandle(stdin_h);
        closeOptHandle(stdout_h);
        closeOptHandle(stderr_h);
        processClose(spawned.handle) catch {};
    }
    try std.testing.expect(spawned.pid > 0);

    var buf: [256]u8 = undefined;
    var got: usize = 0;
    var spins: u32 = 0;
    while (spins < 200) : (spins += 1) {
        var avail: DWORD = 0;
        if (PeekNamedPipe(stdout_h.?, null, 0, null, &avail, null) != 0 and avail > 0) {
            var n: DWORD = 0;
            const want: DWORD = @intCast(@min(buf.len - got, @as(usize, avail)));
            if (ReadFile(stdout_h.?, buf[got..].ptr, want, &n, null) != 0) {
                got += n;
            }
        }
        if (std.mem.indexOf(u8, buf[0..got], "piped-ok") != null) break;
        const wait = try processWait(spawned.handle, 20);
        if (wait.exited and avail == 0) break;
        Sleep(20);
    }
    try std.testing.expect(std.mem.indexOf(u8, buf[0..got], "piped-ok") != null);
}
