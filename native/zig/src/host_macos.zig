//! macOS host primitives: CGEvent input, CGWindowList capture, AX tree.
//!
//! Fail-closed: input and AX require Accessibility (AXIsProcessTrusted);
//! screenshots require Screen Recording (CGPreflightScreenCaptureAccess).
//! Missing permission is `error.AccessDenied` with last_os_error 1001 or 1002
//! so the Go layer can tell the user exactly which Settings pane to open.
//! Capability and unsigned-spawn gates stay in `host.zig` (same as Windows).

const std = @import("std");
const host = @import("host.zig");

const Error = host.Error;
const allocator = host.allocator;

pub const os_err_accessibility: u32 = 1001;
pub const os_err_screen_recording: u32 = 1002;

const CFTypeRef = ?*anyopaque;
const CFStringRef = ?*const opaque {};
const CFDictionaryRef = ?*const opaque {};
const CFArrayRef = ?*const opaque {};
const CFDataRef = ?*const opaque {};
const CGEventRef = ?*opaque {};
const CGEventSourceRef = ?*opaque {};
const CGImageRef = ?*opaque {};
const CGDataProviderRef = ?*opaque {};
const AXUIElementRef = ?*opaque {};
const PasteboardRef = ?*opaque {};

const Boolean = u8;
const CGFloat = f64;
const CGError = i32;
const AXError = i32;
const CGWindowID = u32;
const CGKeyCode = u16;
const CGEventFlags = u64;
const CGEventType = u32;
const CGMouseButton = u32;
const CGEventTapLocation = u32;
const CGEventSourceStateID = i32;
const CGWindowListOption = u32;
const CGWindowImageOption = u32;
const CGDirectDisplayID = u32;
const CFIndex = isize;
const CFStringEncoding = u32;
const pid_t = i32;

const CGPoint = extern struct { x: CGFloat, y: CGFloat };
const CGSize = extern struct { width: CGFloat, height: CGFloat };
const CGRect = extern struct { origin: CGPoint, size: CGSize };

const kCFStringEncodingUTF8: CFStringEncoding = 0x08000100;
const kAXErrorSuccess: AXError = 0;
const kCGErrorSuccess: CGError = 0;
const kCGHIDEventTap: CGEventTapLocation = 0;
const kCGEventSourceStateHIDSystemState: CGEventSourceStateID = 1;
const kCGEventMouseMoved: CGEventType = 5;
const kCGEventLeftMouseDown: CGEventType = 1;
const kCGEventLeftMouseUp: CGEventType = 2;
const kCGEventRightMouseDown: CGEventType = 3;
const kCGEventRightMouseUp: CGEventType = 4;
const kCGEventOtherMouseDown: CGEventType = 25;
const kCGEventOtherMouseUp: CGEventType = 26;
const kCGEventScrollWheel: CGEventType = 22;
const kCGEventKeyDown: CGEventType = 10;
const kCGEventKeyUp: CGEventType = 11;
const kCGMouseButtonLeft: CGMouseButton = 0;
const kCGMouseButtonRight: CGMouseButton = 1;
const kCGMouseButtonCenter: CGMouseButton = 2;
const kCGWindowListOptionOnScreenOnly: CGWindowListOption = 1;
const kCGWindowListExcludeDesktopElements: CGWindowListOption = 16;
const kCGWindowImageDefault: CGWindowImageOption = 0;
const kCGWindowImageNominalResolution: CGWindowImageOption = 16;
const kCGNullWindowID: CGWindowID = 0;
const kCGEventFlagMaskShift: CGEventFlags = 0x00020000;
const kCGEventFlagMaskControl: CGEventFlags = 0x00040000;
const kCGEventFlagMaskAlternate: CGEventFlags = 0x00080000;
const kCGEventFlagMaskCommand: CGEventFlags = 0x00100000;
const kCGScrollEventUnitLine: u32 = 0;
const PROC_PIDTBSDINFO: c_int = 3;
const SIGKILL: c_int = 9;
const WNOHANG: c_int = 1;

extern fn AXIsProcessTrusted() callconv(.c) Boolean;
extern fn AXIsProcessTrustedWithOptions(options: CFDictionaryRef) callconv(.c) Boolean;
extern fn AXUIElementCreateSystemWide() callconv(.c) AXUIElementRef;
extern fn AXUIElementCreateApplication(pid: pid_t) callconv(.c) AXUIElementRef;
extern fn AXUIElementCopyAttributeValue(element: AXUIElementRef, attribute: CFStringRef, value: *CFTypeRef) callconv(.c) AXError;
extern fn AXUIElementPerformAction(element: AXUIElementRef, action: CFStringRef) callconv(.c) AXError;
extern fn AXUIElementCopyElementAtPosition(application: AXUIElementRef, x: f32, y: f32, element: *AXUIElementRef) callconv(.c) AXError;
extern fn AXUIElementGetPid(element: AXUIElementRef, pid: *pid_t) callconv(.c) AXError;
extern fn AXValueGetType(value: CFTypeRef) callconv(.c) u32;
extern fn AXValueGetValue(value: CFTypeRef, typ: u32, value_ptr: *anyopaque) callconv(.c) Boolean;
extern fn AXValueCreate(typ: u32, value_ptr: *const anyopaque) callconv(.c) CFTypeRef;
extern fn AXUIElementSetAttributeValue(element: AXUIElementRef, attribute: CFStringRef, value: CFTypeRef) callconv(.c) AXError;

const kAXValueTypeCGPoint: u32 = 1;
const kAXValueTypeCGSize: u32 = 2;

extern fn CGPreflightScreenCaptureAccess() callconv(.c) bool;
extern fn CGRequestScreenCaptureAccess() callconv(.c) bool;
extern fn CGMainDisplayID() callconv(.c) CGDirectDisplayID;
extern fn CGGetActiveDisplayList(max: u32, ids: ?[*]CGDirectDisplayID, count: *u32) callconv(.c) CGError;
extern fn CGDisplayBounds(id: CGDirectDisplayID) callconv(.c) CGRect;
extern fn CGDisplayPixelsWide(id: CGDirectDisplayID) callconv(.c) usize;
extern fn CGDisplayPixelsHigh(id: CGDirectDisplayID) callconv(.c) usize;
extern fn CGWindowListCopyWindowInfo(option: CGWindowListOption, relative: CGWindowID) callconv(.c) CFArrayRef;
extern fn CGWindowListCreateImage(rect: CGRect, option: CGWindowListOption, wid: CGWindowID, image_option: CGWindowImageOption) callconv(.c) CGImageRef;
extern fn CGImageGetWidth(image: CGImageRef) callconv(.c) usize;
extern fn CGImageGetHeight(image: CGImageRef) callconv(.c) usize;
extern fn CGImageGetBitsPerPixel(image: CGImageRef) callconv(.c) usize;
extern fn CGImageGetBytesPerRow(image: CGImageRef) callconv(.c) usize;
extern fn CGImageGetDataProvider(image: CGImageRef) callconv(.c) CGDataProviderRef;
extern fn CGDataProviderCopyData(provider: CGDataProviderRef) callconv(.c) CFDataRef;
extern fn CGEventSourceCreate(state: CGEventSourceStateID) callconv(.c) CGEventSourceRef;
extern fn CGEventCreateMouseEvent(source: CGEventSourceRef, typ: CGEventType, pos: CGPoint, button: CGMouseButton) callconv(.c) CGEventRef;
extern fn CGEventCreateKeyboardEvent(source: CGEventSourceRef, keycode: CGKeyCode, key_down: bool) callconv(.c) CGEventRef;
extern fn CGEventCreateScrollWheelEvent2(source: CGEventSourceRef, unit: u32, wheel_count: u32, wheel1: i32, wheel2: i32, wheel3: i32) callconv(.c) CGEventRef;
extern fn CGEventKeyboardSetUnicodeString(event: CGEventRef, length: usize, string: [*]const u16) callconv(.c) void;
extern fn CGEventSetFlags(event: CGEventRef, flags: CGEventFlags) callconv(.c) void;
extern fn CGEventPost(tap: CGEventTapLocation, event: CGEventRef) callconv(.c) void;
extern fn CGEventGetLocation(event: CGEventRef) callconv(.c) CGPoint;

extern fn CFRelease(cf: CFTypeRef) callconv(.c) void;
extern fn CFRetain(cf: CFTypeRef) callconv(.c) CFTypeRef;
extern fn CFStringCreateWithCString(alloc: CFTypeRef, c_str: [*:0]const u8, encoding: CFStringEncoding) callconv(.c) CFStringRef;
extern fn CFStringGetLength(s: CFStringRef) callconv(.c) CFIndex;
extern fn CFStringGetCString(s: CFStringRef, buf: [*]u8, buf_size: CFIndex, encoding: CFStringEncoding) callconv(.c) Boolean;
extern fn CFArrayGetCount(arr: CFArrayRef) callconv(.c) CFIndex;
extern fn CFArrayGetValueAtIndex(arr: CFArrayRef, idx: CFIndex) callconv(.c) CFTypeRef;
extern fn CFDictionaryGetValue(dict: CFDictionaryRef, key: CFTypeRef) callconv(.c) CFTypeRef;
extern fn CFDictionaryCreate(alloc: CFTypeRef, keys: [*]const CFTypeRef, values: [*]const CFTypeRef, num: CFIndex, key_cb: ?*const anyopaque, value_cb: ?*const anyopaque) callconv(.c) CFDictionaryRef;
extern fn CFNumberGetValue(number: CFTypeRef, typ: i32, value_ptr: *anyopaque) callconv(.c) Boolean;
extern fn CFDataGetLength(data: CFDataRef) callconv(.c) CFIndex;
extern fn CFDataGetBytePtr(data: CFDataRef) callconv(.c) ?[*]const u8;
extern fn CFGetTypeID(cf: CFTypeRef) callconv(.c) usize;
extern fn CFStringGetTypeID() callconv(.c) usize;
extern fn CFArrayGetTypeID() callconv(.c) usize;
extern fn CFBooleanGetValue(b: CFTypeRef) callconv(.c) Boolean;
extern const kCFBooleanTrue: CFTypeRef;
extern const kAXTrustedCheckOptionPrompt: CFStringRef;
extern const kCGWindowNumber: CFStringRef;
extern const kCGWindowOwnerPID: CFStringRef;
extern const kCGWindowOwnerName: CFStringRef;
extern const kCGWindowName: CFStringRef;
extern const kCGWindowBounds: CFStringRef;
extern const kCGWindowLayer: CFStringRef;
extern const kCGWindowIsOnscreen: CFStringRef;
extern const kCFTypeDictionaryKeyCallBacks: opaque {};
extern const kCFTypeDictionaryValueCallBacks: opaque {};

extern fn proc_listallpids(buffer: ?*anyopaque, buffersize: c_int) callconv(.c) c_int;
extern fn proc_pidpath(pid: c_int, buffer: [*]u8, buffersize: u32) callconv(.c) c_int;
extern fn proc_pidinfo(pid: c_int, flavor: c_int, arg: u64, buffer: ?*anyopaque, buffersize: c_int) callconv(.c) c_int;
extern fn waitpid(pid: pid_t, status: *c_int, options: c_int) callconv(.c) pid_t;
extern fn kill(pid: pid_t, sig: c_int) callconv(.c) c_int;

const kCFNumberSInt32Type: i32 = 3;
const kCFNumberIntType: i32 = 9;
const kCFNumberDoubleType: i32 = 13;
const kAXValueTypeCGPoint: u32 = 1;
const kAXValueTypeCGSize: u32 = 2;
const kAXValueTypeCGRect: u32 = 3;

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

fn cfRelease(ref: CFTypeRef) void {
    if (ref != null) CFRelease(ref);
}

fn cfStringToBuf(s: CFStringRef, buf: []u8) []const u8 {
    if (s == null or buf.len == 0) return "";
    if (CFStringGetCString(s, buf.ptr, @intCast(buf.len), kCFStringEncodingUTF8) == 0) return "";
    const n = std.mem.indexOfScalar(u8, buf, 0) orelse buf.len;
    return buf[0..n];
}

fn cfStringAlloc(gpa: std.mem.Allocator, s: CFStringRef) Error![]u8 {
    var tmp: [2048]u8 = undefined;
    const slice = cfStringToBuf(s, &tmp);
    return gpa.dupe(u8, slice) catch return error.OutOfMemory;
}

fn cfDictI32(dict: CFDictionaryRef, key: CFStringRef) ?i32 {
    const raw = CFDictionaryGetValue(dict, @ptrCast(@constCast(key))) orelse return null;
    var v: i32 = 0;
    if (CFNumberGetValue(raw, kCFNumberSInt32Type, &v) == 0) return null;
    return v;
}

fn cfDictF64(dict: CFDictionaryRef, key: CFTypeRef) ?f64 {
    const raw = CFDictionaryGetValue(dict, key) orelse return null;
    var v: f64 = 0;
    if (CFNumberGetValue(raw, kCFNumberDoubleType, &v) == 0) return null;
    return v;
}

fn windowBounds(dict: CFDictionaryRef) ?host.Rect {
    const raw = CFDictionaryGetValue(dict, @ptrCast(@constCast(kCGWindowBounds))) orelse return null;
    const bdict: CFDictionaryRef = @ptrCast(raw);
    const x = cfDictF64(bdict, @ptrFromInt(0)) orelse blk: {
        // Keys are CFSTR("X"), "Y", "Width", "Height"
        break :blk null;
    };
    _ = x;
    var x_s = CFStringCreateWithCString(null, "X", kCFStringEncodingUTF8);
    defer cfRelease(@ptrCast(@constCast(x_s)));
    var y_s = CFStringCreateWithCString(null, "Y", kCFStringEncodingUTF8);
    defer cfRelease(@ptrCast(@constCast(y_s)));
    var w_s = CFStringCreateWithCString(null, "Width", kCFStringEncodingUTF8);
    defer cfRelease(@ptrCast(@constCast(w_s)));
    var h_s = CFStringCreateWithCString(null, "Height", kCFStringEncodingUTF8);
    defer cfRelease(@ptrCast(@constCast(h_s)));
    const xf = cfDictF64(bdict, @ptrCast(@constCast(x_s))) orelse return null;
    const yf = cfDictF64(bdict, @ptrCast(@constCast(y_s))) orelse return null;
    const wf = cfDictF64(bdict, @ptrCast(@constCast(w_s))) orelse return null;
    const hf = cfDictF64(bdict, @ptrCast(@constCast(h_s))) orelse return null;
    const left: i32 = @intFromFloat(@round(xf));
    const top: i32 = @intFromFloat(@round(yf));
    const width: i32 = @intFromFloat(@round(wf));
    const height: i32 = @intFromFloat(@round(hf));
    return .{ .left = left, .top = top, .right = left + width, .bottom = top + height };
}

// ---------------------------------------------------------------------------
// Permissions
// ---------------------------------------------------------------------------

pub fn accessibilityTrusted() bool {
    return AXIsProcessTrusted() != 0;
}

pub fn screenRecordingTrusted() bool {
    return CGPreflightScreenCaptureAccess();
}

fn promptAccessibility() void {
    if (kAXTrustedCheckOptionPrompt != null and kCFBooleanTrue != null) {
        const keys = [_]CFTypeRef{@ptrCast(@constCast(kAXTrustedCheckOptionPrompt))};
        const vals = [_]CFTypeRef{kCFBooleanTrue};
        const opts = CFDictionaryCreate(
            null,
            &keys,
            &vals,
            1,
            @ptrCast(&kCFTypeDictionaryKeyCallBacks),
            @ptrCast(&kCFTypeDictionaryValueCallBacks),
        );
        defer cfRelease(opts);
        _ = AXIsProcessTrustedWithOptions(opts);
    } else {
        _ = AXIsProcessTrusted();
    }
}

fn shouldPromptForTcc() bool {
    // Headless CI must not block on a TCC dialog.
    if (std.c.getenv("GITHUB_ACTIONS") != null) return false;
    if (std.c.getenv("CI") != null) return false;
    return true;
}

pub fn requireAccessibility() Error!void {
    if (accessibilityTrusted()) return;
    if (shouldPromptForTcc()) promptAccessibility();
    if (accessibilityTrusted()) return;
    host.setOsError(os_err_accessibility);
    return error.AccessDenied;
}

pub fn requireScreenRecording() Error!void {
    if (screenRecordingTrusted()) return;
    if (shouldPromptForTcc()) _ = CGRequestScreenCaptureAccess();
    if (screenRecordingTrusted()) return;
    host.setOsError(os_err_screen_recording);
    return error.AccessDenied;
}

// ---------------------------------------------------------------------------
// Coordinates
// ---------------------------------------------------------------------------

/// Convert a top-left (Y-down, origin at top of the main display) point into
/// the CGEvent / Cocoa space (origin at the bottom-left of the main display,
/// Y increasing up).
pub fn eventYFromTopLeft(y: i32, primary_height: i32) f64 {
    return @as(f64, @floatFromInt(primary_height)) - @as(f64, @floatFromInt(y));
}

pub fn eventPointFromTopLeft(x: i32, y: i32, primary_height: i32) CGPoint {
    return .{ .x = @floatFromInt(x), .y = eventYFromTopLeft(y, primary_height) };
}

fn primaryHeightPoints() i32 {
    const bounds = CGDisplayBounds(CGMainDisplayID());
    const h: i32 = @intFromFloat(@round(bounds.size.height));
    return if (h > 0) h else 1;
}

/// CGDisplayBounds is Cocoa space. Convert one display to top-left Y-down.
pub fn displayRectTopLeft(bounds: CGRect, primary_height: i32) host.Rect {
    const width: i32 = @intFromFloat(@round(bounds.size.width));
    const height: i32 = @intFromFloat(@round(bounds.size.height));
    const left: i32 = @intFromFloat(@round(bounds.origin.x));
    const bottom: i32 = @intFromFloat(@round(bounds.origin.y));
    const top = primary_height - (bottom + height);
    return .{ .left = left, .top = top, .right = left + width, .bottom = top + height };
}

pub fn enableDpiAwareness() Error!void {}

pub fn virtualScreen() Error!host.VirtualScreen {
    var count: u32 = 0;
    if (CGGetActiveDisplayList(0, null, &count) != kCGErrorSuccess or count == 0) {
        const b = CGDisplayBounds(CGMainDisplayID());
        const w: i32 = @intFromFloat(@round(b.size.width));
        const h: i32 = @intFromFloat(@round(b.size.height));
        if (w <= 0 or h <= 0) return error.OperationFailed;
        return .{ .left = 0, .top = 0, .width = w, .height = h };
    }
    var ids: [16]CGDirectDisplayID = undefined;
    const n: u32 = @min(count, ids.len);
    if (CGGetActiveDisplayList(n, &ids, &count) != kCGErrorSuccess) return error.OperationFailed;
    const primary_h = primaryHeightPoints();
    var left: i32 = std.math.maxInt(i32);
    var top: i32 = std.math.maxInt(i32);
    var right: i32 = std.math.minInt(i32);
    var bottom: i32 = std.math.minInt(i32);
    var i: u32 = 0;
    while (i < @min(count, ids.len)) : (i += 1) {
        const r = displayRectTopLeft(CGDisplayBounds(ids[i]), primary_h);
        left = @min(left, r.left);
        top = @min(top, r.top);
        right = @max(right, r.right);
        bottom = @max(bottom, r.bottom);
    }
    if (right <= left or bottom <= top) return error.OperationFailed;
    return .{ .left = left, .top = top, .width = right - left, .height = bottom - top };
}

pub fn listMonitorsJson() Error![]u8 {
    var count: u32 = 0;
    if (CGGetActiveDisplayList(0, null, &count) != kCGErrorSuccess) return error.OperationFailed;
    var ids: [16]CGDirectDisplayID = undefined;
    const n: u32 = @min(if (count == 0) 1 else count, ids.len);
    if (count == 0) {
        ids[0] = CGMainDisplayID();
        count = 1;
    } else if (CGGetActiveDisplayList(n, &ids, &count) != kCGErrorSuccess) {
        return error.OperationFailed;
    }
    const primary = CGMainDisplayID();
    const primary_h = primaryHeightPoints();
    var entries: std.ArrayList(host.MonitorEntry) = .empty;
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    var i: u32 = 0;
    while (i < @min(count, ids.len)) : (i += 1) {
        const id = ids[i];
        const r = displayRectTopLeft(CGDisplayBounds(id), primary_h);
        const pw: i32 = @intCast(@max(CGDisplayPixelsWide(id), 1));
        const point_w = @max(r.right - r.left, 1);
        const scale: f64 = @as(f64, @floatFromInt(pw)) / @as(f64, @floatFromInt(point_w));
        try entries.append(gpa, .{
            .index = i,
            .left = r.left,
            .top = r.top,
            .right = r.right,
            .bottom = r.bottom,
            .width = r.right - r.left,
            .height = r.bottom - r.top,
            .primary = id == primary,
            .scale = scale,
        });
    }
    return host.monitorsJson(allocator, entries.items);
}

fn strideFor(width: i32, bytes_per_pixel: u32) usize {
    const row = @as(usize, @intCast(width)) * bytes_per_pixel;
    return (row + 3) & ~@as(usize, 3);
}

fn validateBpp(bytes_per_pixel: u32) Error!void {
    if (bytes_per_pixel != 3 and bytes_per_pixel != 4) return error.InvalidArgument;
}

fn cgRectFromTopLeft(left: i32, top: i32, width: i32, height: i32) CGRect {
    return .{
        .origin = .{ .x = @floatFromInt(left), .y = @floatFromInt(top) },
        .size = .{ .width = @floatFromInt(width), .height = @floatFromInt(height) },
    };
}

fn pixelsFromImage(image: CGImageRef, bytes_per_pixel: u32, left: i32, top: i32) Error!host.Pixels {
    try validateBpp(bytes_per_pixel);
    const width_u = CGImageGetWidth(image);
    const height_u = CGImageGetHeight(image);
    if (width_u == 0 or height_u == 0) return error.OperationFailed;
    const width: i32 = @intCast(width_u);
    const height: i32 = @intCast(height_u);
    const provider = CGImageGetDataProvider(image) orelse return error.OperationFailed;
    const data = CGDataProviderCopyData(provider) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(@constCast(data)));
    const src_ptr = CFDataGetBytePtr(data) orelse return error.OperationFailed;
    const src_len: usize = @intCast(CFDataGetLength(data));
    const src_stride = CGImageGetBytesPerRow(image);
    const bpp = CGImageGetBitsPerPixel(image);
    const src_bpp: usize = if (bpp == 0) 32 else bpp;
    const src_pp: usize = (src_bpp + 7) / 8;
    const stride = strideFor(width, bytes_per_pixel);
    const out = allocator.alloc(u8, stride * @as(usize, @intCast(height))) catch return error.OutOfMemory;
    errdefer allocator.free(out);
    @memset(out, 0);
    var y: i32 = 0;
    while (y < height) : (y += 1) {
        const row_off = @as(usize, @intCast(y)) * src_stride;
        if (row_off >= src_len) break;
        const row = src_ptr[row_off..src_len];
        const dst = out[@as(usize, @intCast(y)) * stride ..];
        var x: i32 = 0;
        while (x < width) : (x += 1) {
            const so = @as(usize, @intCast(x)) * src_pp;
            if (so + 2 >= row.len) break;
            // Quartz images are typically BGRA or RGBA; detect poorly as BGRA
            // (little-endian Mac). If first byte looks like alpha-heavy we'll
            // still emit B,G,R from bytes 0..2 as BGRA.
            const b = row[so + 0];
            const g = row[so + 1];
            const r = row[so + 2];
            const o = @as(usize, @intCast(x)) * bytes_per_pixel;
            dst[o] = b;
            dst[o + 1] = g;
            dst[o + 2] = r;
            if (bytes_per_pixel == 4) dst[o + 3] = if (src_pp >= 4 and so + 3 < row.len) row[so + 3] else 255;
        }
    }
    return .{
        .bytes = out,
        .width = width,
        .height = height,
        .stride = stride,
        .left = left,
        .top = top,
    };
}

pub fn captureRegion(left: i32, top: i32, width: i32, height: i32, bytes_per_pixel: u32) Error!host.Pixels {
    try requireScreenRecording();
    try validateBpp(bytes_per_pixel);
    if (width <= 0 or height <= 0) return error.InvalidArgument;
    const rect = cgRectFromTopLeft(left, top, width, height);
    const image = CGWindowListCreateImage(
        rect,
        kCGWindowListOptionOnScreenOnly,
        kCGNullWindowID,
        kCGWindowImageNominalResolution,
    ) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(image));
    return pixelsFromImage(image, bytes_per_pixel, left, top);
}

pub fn captureVirtualScreen(bytes_per_pixel: u32) Error!host.Pixels {
    try requireScreenRecording();
    const screen = try virtualScreen();
    // CGRectNull is all-NaN in Apple headers; pass the virtual-screen rect
    // in top-left space so we stay consistent with click coordinates.
    return captureRegion(screen.left, screen.top, screen.width, screen.height, bytes_per_pixel);
}

pub fn printWindow(raw_hwnd: u64, bytes_per_pixel: u32) Error!host.Pixels {
    try requireScreenRecording();
    try validateBpp(bytes_per_pixel);
    const wid: CGWindowID = @truncate(raw_hwnd);
    if (wid == 0) return error.InvalidArgument;
    const rect = try windowRect(raw_hwnd);
    const width = rect.right - rect.left;
    const height = rect.bottom - rect.top;
    if (width < 2 or height < 2) return error.InvalidArgument;
    const image = CGWindowListCreateImage(
        cgRectFromTopLeft(rect.left, rect.top, width, height),
        kCGWindowListOptionOnScreenOnly,
        wid,
        kCGWindowImageNominalResolution,
    ) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(image));
    return pixelsFromImage(image, bytes_per_pixel, rect.left, rect.top);
}

// ---------------------------------------------------------------------------
// Input (CGEvent) — requires Accessibility
// ---------------------------------------------------------------------------

fn eventSource() Error!CGEventSourceRef {
    return CGEventSourceCreate(kCGEventSourceStateHIDSystemState) orelse error.OperationFailed;
}

fn postMouse(typ: CGEventType, x: i32, y: i32, button: CGMouseButton) Error!void {
    try requireAccessibility();
    const src = try eventSource();
    defer cfRelease(@ptrCast(src));
    const pos = eventPointFromTopLeft(x, y, primaryHeightPoints());
    const ev = CGEventCreateMouseEvent(src, typ, pos, button) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(ev));
    CGEventPost(kCGHIDEventTap, ev);
}

fn buttonOf(button: host.MouseButton) struct { typ_down: CGEventType, typ_up: CGEventType, btn: CGMouseButton } {
    return switch (button) {
        .left => .{ .typ_down = kCGEventLeftMouseDown, .typ_up = kCGEventLeftMouseUp, .btn = kCGMouseButtonLeft },
        .right => .{ .typ_down = kCGEventRightMouseDown, .typ_up = kCGEventRightMouseUp, .btn = kCGMouseButtonRight },
        .middle => .{ .typ_down = kCGEventOtherMouseDown, .typ_up = kCGEventOtherMouseUp, .btn = kCGMouseButtonCenter },
    };
}

const Timespec = extern struct {
    sec: isize,
    nsec: isize,
};

extern "c" fn nanosleep(req: *const Timespec, rem: ?*Timespec) callconv(.c) c_int;
extern "c" fn clock_gettime(clock_id: c_int, tp: *Timespec) callconv(.c) c_int;

// CLOCK_MONOTONIC is 6 on Darwin (1 on Linux).
const CLOCK_MONOTONIC: c_int = 6;

fn sleepMs(ms: u32) void {
    if (ms == 0) return;
    var req = Timespec{
        .sec = @intCast(ms / 1000),
        .nsec = @intCast((ms % 1000) * 1_000_000),
    };
    var rem: Timespec = undefined;
    while (nanosleep(&req, &rem) != 0) {
        req = rem;
    }
}

fn monoMillis() i64 {
    var ts: Timespec = undefined;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) return 0;
    return @as(i64, @intCast(ts.sec)) * 1000 + @divTrunc(@as(i64, @intCast(ts.nsec)), 1_000_000);
}

pub fn mouseMove(x: i32, y: i32) Error!void {
    try postMouse(kCGEventMouseMoved, x, y, kCGMouseButtonLeft);
}

pub fn mouseClick(x: i32, y: i32, button: host.MouseButton, clicks: u32) Error!void {
    try mouseMove(x, y);
    sleepMs(20);
    const spec = buttonOf(button);
    var remaining: u32 = @max(clicks, 1);
    while (remaining > 0) : (remaining -= 1) {
        try postMouse(spec.typ_down, x, y, spec.btn);
        try postMouse(spec.typ_up, x, y, spec.btn);
        sleepMs(40);
    }
}

pub fn mouseButton(button: host.MouseButton, pressed: bool) Error!void {
    try requireAccessibility();
    const spec = buttonOf(button);
    // Current pointer position is unknown; post at (0,0) only if we must.
    // Use a mouse moved-less button event at the last known cursor via
    // a no-op location (0,0) is wrong. Query the current event location
    // by creating a source-less dummy? Use CGEventCreate(null) + GetLocation.
    const src = try eventSource();
    defer cfRelease(@ptrCast(src));
    const dummy = CGEventCreateMouseEvent(src, kCGEventMouseMoved, .{ .x = 0, .y = 0 }, spec.btn) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(dummy));
    // We don't have a "get current" in this path; callers use mouseClick
    // for positioned input. For press/release, reuse 0,0 only as last resort.
    const typ = if (pressed) spec.typ_down else spec.typ_up;
    try postMouse(typ, 0, 0, spec.btn);
}

pub fn mouseDrag(x1: i32, y1: i32, x2: i32, y2: i32, steps: u32) Error!void {
    try mouseMove(x1, y1);
    sleepMs(20);
    try postMouse(kCGEventLeftMouseDown, x1, y1, kCGMouseButtonLeft);
    sleepMs(50);
    const n: u32 = @max(steps, 2);
    var i: u32 = 1;
    while (i <= n) : (i += 1) {
        const t: f64 = @as(f64, @floatFromInt(i)) / @as(f64, @floatFromInt(n));
        const fx = @as(f64, @floatFromInt(x1)) + @as(f64, @floatFromInt(x2 - x1)) * t;
        const fy = @as(f64, @floatFromInt(y1)) + @as(f64, @floatFromInt(y2 - y1)) * t;
        try postMouse(kCGEventMouseMoved, @intFromFloat(fx), @intFromFloat(fy), kCGMouseButtonLeft);
        sleepMs(12);
    }
    sleepMs(120);
    try postMouse(kCGEventLeftMouseUp, x2, y2, kCGMouseButtonLeft);
}

pub fn mouseScroll(x: i32, y: i32, dx: i32, dy: i32) Error!void {
    try mouseMove(x, y);
    sleepMs(20);
    try requireAccessibility();
    const src = try eventSource();
    defer cfRelease(@ptrCast(src));
    const ev = CGEventCreateScrollWheelEvent2(src, kCGScrollEventUnitLine, 2, dy, dx, 0) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(ev));
    CGEventPost(kCGHIDEventTap, ev);
}

/// Map a Windows VK code (used by the computer.key tool on every OS) to a
/// macOS virtual key code. Returns null when there is no stable mapping.
pub fn keyCodeFromVk(vk: u16) ?CGKeyCode {
    return switch (vk) {
        0x08 => 0x33, // Backspace / Delete
        0x09 => 0x30, // Tab
        0x0D => 0x24, // Return
        0x1B => 0x35, // Escape
        0x20 => 0x31, // Space
        0x25 => 0x7B, // Left
        0x26 => 0x7E, // Up
        0x27 => 0x7C, // Right
        0x28 => 0x7D, // Down
        0x24 => 0x73, // Home
        0x23 => 0x77, // End
        0x21 => 0x74, // Page Up
        0x22 => 0x79, // Page Down
        0x2E => 0x75, // Forward delete
        0x10 => 0x38, // Shift
        0x11 => 0x3B, // Control
        0x12 => 0x3A, // Option / Alt
        0x5B => 0x37, // Command
        0x70 => 0x7A, // F1
        0x71 => 0x78, // F2
        0x72 => 0x63, // F3
        0x73 => 0x76, // F4
        0x74 => 0x60, // F5
        0x75 => 0x61, // F6
        0x76 => 0x62, // F7
        0x77 => 0x64, // F8
        0x78 => 0x65, // F9
        0x79 => 0x6D, // F10
        0x7A => 0x67, // F11
        0x7B => 0x6F, // F12
        0x30 => 0x1D, // 0
        0x31 => 0x12, // 1
        0x32 => 0x13,
        0x33 => 0x14,
        0x34 => 0x15,
        0x35 => 0x17,
        0x36 => 0x16,
        0x37 => 0x1A,
        0x38 => 0x1C,
        0x39 => 0x19,
        0x41 => 0x00, // A
        0x42 => 0x0B, // B
        0x43 => 0x08, // C
        0x44 => 0x02, // D
        0x45 => 0x0E, // E
        0x46 => 0x03, // F
        0x47 => 0x05, // G
        0x48 => 0x04, // H
        0x49 => 0x22, // I
        0x4A => 0x26, // J
        0x4B => 0x28, // K
        0x4C => 0x25, // L
        0x4D => 0x2E, // M
        0x4E => 0x2D, // N
        0x4F => 0x1F, // O
        0x50 => 0x23, // P
        0x51 => 0x0C, // Q
        0x52 => 0x0F, // R
        0x53 => 0x01, // S
        0x54 => 0x11, // T
        0x55 => 0x20, // U
        0x56 => 0x09, // V
        0x57 => 0x0D, // W
        0x58 => 0x07, // X
        0x59 => 0x10, // Y
        0x5A => 0x06, // Z
        else => null,
    };
}

fn postKey(code: CGKeyCode, down: bool) Error!void {
    try requireAccessibility();
    const src = try eventSource();
    defer cfRelease(@ptrCast(src));
    const ev = CGEventCreateKeyboardEvent(src, code, down) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(ev));
    CGEventPost(kCGHIDEventTap, ev);
}

fn postUnicode(codepoint: u21) Error!void {
    try requireAccessibility();
    var units: [2]u16 = undefined;
    var n: usize = 0;
    if (codepoint <= 0xFFFF) {
        units[0] = @intCast(codepoint);
        n = 1;
    } else {
        const u = codepoint - 0x10000;
        units[0] = @intCast(0xD800 + (u >> 10));
        units[1] = @intCast(0xDC00 + (u & 0x3FF));
        n = 2;
    }
    const src = try eventSource();
    defer cfRelease(@ptrCast(src));
    const down = CGEventCreateKeyboardEvent(src, 0, true) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(down));
    CGEventKeyboardSetUnicodeString(down, n, &units);
    CGEventPost(kCGHIDEventTap, down);
    const up = CGEventCreateKeyboardEvent(src, 0, false) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(up));
    CGEventKeyboardSetUnicodeString(up, n, &units);
    CGEventPost(kCGHIDEventTap, up);
}

pub fn typeText(utf8: []const u8, per_char_delay_ms: u32) Error!void {
    try requireAccessibility();
    const view = std.unicode.Utf8View.init(utf8) catch return error.InvalidArgument;
    var iterator = view.iterator();
    while (iterator.nextCodepoint()) |codepoint| {
        if (codepoint == '\r') {
            if (iterator.i < utf8.len and utf8[iterator.i] == '\n') continue;
            try postKey(0x24, true);
            try postKey(0x24, false);
        } else if (codepoint == '\n') {
            try postKey(0x24, true);
            try postKey(0x24, false);
        } else {
            try postUnicode(codepoint);
        }
        if (per_char_delay_ms > 0) sleepMs(per_char_delay_ms);
    }
}

pub fn keyCombo(vks: []const u16) Error!void {
    try requireAccessibility();
    var codes = allocator.alloc(CGKeyCode, vks.len) catch return error.OutOfMemory;
    defer allocator.free(codes);
    for (vks, 0..) |vk, i| {
        codes[i] = keyCodeFromVk(vk) orelse return error.InvalidArgument;
    }
    for (codes) |code| try postKey(code, true);
    var index = codes.len;
    while (index > 0) {
        index -= 1;
        try postKey(codes[index], false);
    }
}

pub fn keyHold(vk: u16, hold_ms: u32) Error!void {
    const code = keyCodeFromVk(vk) orelse return error.InvalidArgument;
    try postKey(code, true);
    sleepMs(hold_ms);
    try postKey(code, false);
}

pub fn vkKeyScan(codepoint: u32) Error!i32 {
    if (codepoint > 0x7F) return -1;
    if (codepoint >= 'A' and codepoint <= 'Z') {
        return @intCast((@as(i32, 0x10) << 8) | @as(i32, @intCast(codepoint)));
    }
    if ((codepoint >= 'a' and codepoint <= 'z') or (codepoint >= '0' and codepoint <= '9') or codepoint == ' ') {
        const vk: i32 = if (codepoint >= 'a' and codepoint <= 'z')
            @intCast(codepoint - 0x20)
        else
            @intCast(codepoint);
        return vk;
    }
    return -1;
}

// ---------------------------------------------------------------------------
// Windows (CGWindowList)
// ---------------------------------------------------------------------------

fn listWindowDicts() Error!CFArrayRef {
    return CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID,
    ) orelse error.OperationFailed;
}

pub fn listWindowsJson(limit: u32) Error![]u8 {
    const arr = try listWindowDicts();
    defer cfRelease(@ptrCast(@constCast(arr)));
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    var entries: std.ArrayList(host.WindowEntry) = .empty;
    const cap = @max(limit, 1);
    const n: usize = @intCast(CFArrayGetCount(arr));
    var i: usize = 0;
    while (i < n and entries.items.len < cap) : (i += 1) {
        const raw = CFArrayGetValueAtIndex(arr, @intCast(i)) orelse continue;
        const dict: CFDictionaryRef = @ptrCast(raw);
        const layer = cfDictI32(dict, kCGWindowLayer) orelse 0;
        if (layer != 0) continue;
        const wid = cfDictI32(dict, kCGWindowNumber) orelse continue;
        const pid = cfDictI32(dict, kCGWindowOwnerPID) orelse 0;
        const bounds = windowBounds(dict) orelse continue;
        const width = bounds.right - bounds.left;
        const height = bounds.bottom - bounds.top;
        if (width < 8 or height < 8) continue;
        var title_buf: [512]u8 = undefined;
        var owner_buf: [512]u8 = undefined;
        const title_cf: CFStringRef = @ptrCast(CFDictionaryGetValue(dict, @ptrCast(@constCast(kCGWindowName))));
        const owner_cf: CFStringRef = @ptrCast(CFDictionaryGetValue(dict, @ptrCast(@constCast(kCGWindowOwnerName))));
        const title_raw = cfStringToBuf(title_cf, &title_buf);
        const owner = cfStringToBuf(owner_cf, &owner_buf);
        const title = if (title_raw.len > 0) title_raw else owner;
        if (title.len == 0) continue;
        const class = try gpa.dupe(u8, owner);
        try entries.append(gpa, .{
            .hwnd = @as(u64, @intCast(wid)),
            .title = host.truncateCodepoints(title, 200),
            .class = class,
            .pid = @intCast(@max(pid, 0)),
            .bounds = bounds,
            .width = width,
            .height = height,
            .visible = true,
            .minimized = false,
        });
    }
    return host.windowsJson(allocator, entries.items);
}

fn windowDictById(wid: CGWindowID) Error!struct { arr: CFArrayRef, dict: CFDictionaryRef } {
    const arr = try listWindowDicts();
    const n: usize = @intCast(CFArrayGetCount(arr));
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const raw = CFArrayGetValueAtIndex(arr, @intCast(i)) orelse continue;
        const dict: CFDictionaryRef = @ptrCast(raw);
        const id = cfDictI32(dict, kCGWindowNumber) orelse continue;
        if (@as(u32, @intCast(id)) == wid) return .{ .arr = arr, .dict = dict };
    }
    cfRelease(@ptrCast(@constCast(arr)));
    return error.InvalidArgument;
}

pub fn windowRect(raw_hwnd: u64) Error!host.Rect {
    const wid: CGWindowID = @truncate(raw_hwnd);
    if (wid == 0) return error.InvalidArgument;
    const found = try windowDictById(wid);
    defer cfRelease(@ptrCast(@constCast(found.arr)));
    return windowBounds(found.dict) orelse error.OperationFailed;
}

pub fn windowClass(raw_hwnd: u64) Error![]u8 {
    const wid: CGWindowID = @truncate(raw_hwnd);
    if (wid == 0) return error.InvalidArgument;
    const found = try windowDictById(wid);
    defer cfRelease(@ptrCast(@constCast(found.arr)));
    const owner_cf: CFStringRef = @ptrCast(CFDictionaryGetValue(found.dict, @ptrCast(@constCast(kCGWindowOwnerName))));
    return cfStringAlloc(allocator, owner_cf);
}

pub const ForegroundInfo = struct { hwnd: u64, title: []u8 };

pub fn foregroundWindow() Error!ForegroundInfo {
    // Frontmost normal window is the first layer-0 entry (list is front-to-back).
    const arr = try listWindowDicts();
    defer cfRelease(@ptrCast(@constCast(arr)));
    const n: usize = @intCast(CFArrayGetCount(arr));
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const raw = CFArrayGetValueAtIndex(arr, @intCast(i)) orelse continue;
        const dict: CFDictionaryRef = @ptrCast(raw);
        const layer = cfDictI32(dict, kCGWindowLayer) orelse 0;
        if (layer != 0) continue;
        const wid = cfDictI32(dict, kCGWindowNumber) orelse continue;
        var title_buf: [512]u8 = undefined;
        var owner_buf: [512]u8 = undefined;
        const title_cf: CFStringRef = @ptrCast(CFDictionaryGetValue(dict, @ptrCast(@constCast(kCGWindowName))));
        const owner_cf: CFStringRef = @ptrCast(CFDictionaryGetValue(dict, @ptrCast(@constCast(kCGWindowOwnerName))));
        const title_raw = cfStringToBuf(title_cf, &title_buf);
        const owner = cfStringToBuf(owner_cf, &owner_buf);
        const title = if (title_raw.len > 0) title_raw else owner;
        return .{
            .hwnd = @as(u64, @intCast(wid)),
            .title = allocator.dupe(u8, title) catch return error.OutOfMemory,
        };
    }
    return .{ .hwnd = 0, .title = allocator.dupe(u8, "") catch return error.OutOfMemory };
}

pub fn foregroundDetailJson() Error![]u8 {
    const info = try foregroundWindow();
    defer allocator.free(info.title);
    if (info.hwnd == 0) {
        return host.jsonAlloc(.{
            .hwnd = @as(u64, 0),
            .title = "",
            .pid = @as(u32, 0),
            .exe = "",
        });
    }
    const found = windowDictById(@truncate(info.hwnd)) catch {
        return host.jsonAlloc(.{
            .hwnd = info.hwnd,
            .title = host.truncateCodepoints(info.title, 200),
            .pid = @as(u32, 0),
            .exe = "",
        });
    };
    defer cfRelease(@ptrCast(@constCast(found.arr)));
    const pid_i = cfDictI32(found.dict, kCGWindowOwnerPID) orelse 0;
    const pid: u32 = @intCast(@max(pid_i, 0));
    var path_buf: [4096]u8 = undefined;
    var exe: []const u8 = "";
    if (pid > 0) {
        const n = proc_pidpath(@intCast(pid), &path_buf, path_buf.len);
        if (n > 0) exe = path_buf[0..@intCast(n)];
    }
    return host.jsonAlloc(.{
        .hwnd = info.hwnd,
        .title = host.truncateCodepoints(info.title, 200),
        .pid = pid,
        .exe = exe,
    });
}

fn axAttrString(name: [*:0]const u8) CFStringRef {
    return CFStringCreateWithCString(null, name, kCFStringEncodingUTF8);
}

fn raiseAndActivate(wid: CGWindowID, pid: i32) Error!bool {
    try requireAccessibility();
    if (pid <= 0) return error.OperationFailed;
    const app = AXUIElementCreateApplication(@intCast(pid)) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(app));
    const windows_key = axAttrString("AXWindows");
    defer cfRelease(@ptrCast(@constCast(windows_key)));
    var windows_raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(app, windows_key, &windows_raw) != kAXErrorSuccess or windows_raw == null) {
        return error.OperationFailed;
    }
    defer cfRelease(windows_raw);
    if (CFGetTypeID(windows_raw) != CFArrayGetTypeID()) return error.OperationFailed;
    const arr: CFArrayRef = @ptrCast(windows_raw);
    const raise = axAttrString("AXRaise");
    defer cfRelease(@ptrCast(@constCast(raise)));
    // If we cannot match a window number, raise the first window of the pid.
    var target: AXUIElementRef = if (CFArrayGetCount(arr) > 0) @ptrCast(CFArrayGetValueAtIndex(arr, 0)) else null;
    _ = wid;
    if (target == null) return false;
    _ = AXUIElementPerformAction(target, raise);
    const focused_key = axAttrString("AXFocused");
    defer cfRelease(@ptrCast(@constCast(focused_key)));
    _ = AXUIElementCopyAttributeValue(app, focused_key, &windows_raw);
    return true;
}

pub fn focusWindow(raw_hwnd: u64) Error!bool {
    const wid: CGWindowID = @truncate(raw_hwnd);
    if (wid == 0) return error.InvalidArgument;
    const found = try windowDictById(wid);
    defer cfRelease(@ptrCast(@constCast(found.arr)));
    const pid = cfDictI32(found.dict, kCGWindowOwnerPID) orelse 0;
    return raiseAndActivate(wid, pid);
}

fn axSetPoint(element: AXUIElementRef, attr: []const u8, x: i32, y: i32) Error!void {
    var point = CGPoint{ .x = @floatFromInt(x), .y = @floatFromInt(y) };
    const value = AXValueCreate(kAXValueTypeCGPoint, @ptrCast(&point)) orelse return error.OperationFailed;
    defer cfRelease(value);
    const key = axAttrString(attr);
    defer cfRelease(@ptrCast(@constCast(key)));
    if (AXUIElementSetAttributeValue(element, key, value) != kAXErrorSuccess) return error.OperationFailed;
}

fn axSetSize(element: AXUIElementRef, attr: []const u8, w: i32, h: i32) Error!void {
    var size = CGSize{ .width = @floatFromInt(w), .height = @floatFromInt(h) };
    const value = AXValueCreate(kAXValueTypeCGSize, @ptrCast(&size)) orelse return error.OperationFailed;
    defer cfRelease(value);
    const key = axAttrString(attr);
    defer cfRelease(@ptrCast(@constCast(key)));
    if (AXUIElementSetAttributeValue(element, key, value) != kAXErrorSuccess) return error.OperationFailed;
}

pub fn manageWindow(raw_hwnd: u64, action: host.WindowAction, x: i32, y: i32, width: i32, height: i32) Error!void {
    try requireAccessibility();
    const wid: CGWindowID = @truncate(raw_hwnd);
    if (wid == 0) return error.InvalidArgument;
    const found = try windowDictById(wid);
    defer cfRelease(@ptrCast(@constCast(found.arr)));
    const pid = cfDictI32(found.dict, kCGWindowOwnerPID) orelse 0;
    if (pid <= 0) return error.OperationFailed;
    const app = AXUIElementCreateApplication(@intCast(pid)) orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(app));
    const windows_key = axAttrString("AXWindows");
    defer cfRelease(@ptrCast(@constCast(windows_key)));
    var windows_raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(app, windows_key, &windows_raw) != kAXErrorSuccess or windows_raw == null) {
        return error.OperationFailed;
    }
    defer cfRelease(windows_raw);
    if (CFArrayGetCount(@ptrCast(windows_raw)) <= 0) return error.OperationFailed;
    const win: AXUIElementRef = @ptrCast(CFArrayGetValueAtIndex(@ptrCast(windows_raw), 0));
    switch (action) {
        .minimize => {
            const act = axAttrString("AXMinimize");
            defer cfRelease(@ptrCast(@constCast(act)));
            if (AXUIElementPerformAction(win, act) != kAXErrorSuccess) return error.OperationFailed;
        },
        .close => {
            const act = axAttrString("AXClose");
            defer cfRelease(@ptrCast(@constCast(act)));
            // Close is often a button, not an action on the window; try AXPress
            // on the close button if AXClose fails.
            if (AXUIElementPerformAction(win, act) != kAXErrorSuccess) return error.OperationFailed;
        },
        .maximize, .restore => {
            const act = axAttrString("AXZoom");
            defer cfRelease(@ptrCast(@constCast(act)));
            _ = AXUIElementPerformAction(win, act);
        },
        .move_resize => {
            try axSetPoint(win, "AXPosition", x, y);
            if (width > 0 and height > 0) {
                try axSetSize(win, "AXSize", width, height);
            }
        },
    }
}

pub fn findChildHwnd(raw_parent: u64, class_substr: []const u8, title_substr: []const u8) Error!u64 {
    // Window ids are not hierarchical the way HWNDs are. Accessibility
    // children are not CGWindowIDs. Fail closed rather than guess.
    _ = raw_parent;
    _ = class_substr;
    _ = title_substr;
    return error.Unsupported;
}

// ---------------------------------------------------------------------------
// Clipboard — NSPasteboard via the `pbpaste`/`pbcopy` tools is not used.
// Carbon Pasteboard API (ApplicationServices).
// ---------------------------------------------------------------------------

extern fn PasteboardCreate(name: CFStringRef, out: *PasteboardRef) callconv(.c) i32;
extern fn PasteboardClear(pb: PasteboardRef) callconv(.c) i32;
extern fn PasteboardGetItemCount(pb: PasteboardRef, out: *u64) callconv(.c) i32;
extern fn PasteboardGetItemIdentifier(pb: PasteboardRef, index: u64, out: *u64) callconv(.c) i32;
extern fn PasteboardCopyItemFlavorData(pb: PasteboardRef, item: u64, flavor: CFStringRef, data: *CFDataRef) callconv(.c) i32;
extern fn PasteboardPutItemFlavor(pb: PasteboardRef, item: u64, flavor: CFStringRef, data: CFDataRef, flags: u32) callconv(.c) i32;
extern fn CFDataCreate(alloc: CFTypeRef, bytes: [*]const u8, length: CFIndex) callconv(.c) CFDataRef;
extern const kPasteboardClipboard: CFStringRef;

fn publicUtf8Flavor() CFStringRef {
    return CFStringCreateWithCString(null, "public.utf8-plain-text", kCFStringEncodingUTF8);
}

pub fn clipboardGetText() Error![]u8 {
    var pb: PasteboardRef = null;
    const name = kPasteboardClipboard;
    if (PasteboardCreate(name, &pb) != 0 or pb == null) return error.OperationFailed;
    defer cfRelease(@ptrCast(pb));
    var count: u64 = 0;
    if (PasteboardGetItemCount(pb, &count) != 0 or count == 0) {
        return allocator.dupe(u8, "") catch return error.OutOfMemory;
    }
    var ident: u64 = 0;
    if (PasteboardGetItemIdentifier(pb, 1, &ident) != 0) return error.OperationFailed;
    const flavor = publicUtf8Flavor();
    defer cfRelease(@ptrCast(@constCast(flavor)));
    var data: CFDataRef = null;
    if (PasteboardCopyItemFlavorData(pb, ident, flavor, &data) != 0 or data == null) {
        return allocator.dupe(u8, "") catch return error.OutOfMemory;
    }
    defer cfRelease(@ptrCast(@constCast(data)));
    const ptr = CFDataGetBytePtr(data) orelse return error.OperationFailed;
    const len: usize = @intCast(CFDataGetLength(data));
    return allocator.dupe(u8, ptr[0..len]) catch return error.OutOfMemory;
}

pub fn clipboardSetText(utf8: []const u8) Error!void {
    var pb: PasteboardRef = null;
    if (PasteboardCreate(kPasteboardClipboard, &pb) != 0 or pb == null) return error.OperationFailed;
    defer cfRelease(@ptrCast(pb));
    if (PasteboardClear(pb) != 0) return error.OperationFailed;
    const flavor = publicUtf8Flavor();
    defer cfRelease(@ptrCast(@constCast(flavor)));
    const data = CFDataCreate(null, utf8.ptr, @intCast(utf8.len)) orelse return error.OutOfMemory;
    defer cfRelease(@ptrCast(@constCast(data)));
    if (PasteboardPutItemFlavor(pb, 1, flavor, data, 0) != 0) return error.OperationFailed;
}

// ---------------------------------------------------------------------------
// Process control
// ---------------------------------------------------------------------------

const Process = struct {
    pid: pid_t,
    pgid: pid_t,
    exited: bool = false,
    exit_code: u32 = 0,
};

const Spawned = host.Spawned;
const WaitOutcome = host.WaitOutcome;

fn processFrom(handle: u64) Error!*Process {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(handle);
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
    const deadline: i64 = if (forever)
        std.math.maxInt(i64)
    else
        monoMillis() + @as(i64, @intCast(timeout_ms));
    while (forever or monoMillis() < deadline) {
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

// ---------------------------------------------------------------------------
// AX snapshot
// ---------------------------------------------------------------------------

const Candidate = struct {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    area: i32,
    name: []const u8,
    role: []const u8,
    source: []const u8 = "ax",
};

pub const click_roles = [_][]const u8{
    "AXButton",   "AXPopUpButton", "AXCheckBox", "AXRadioButton",
    "AXMenuItem", "AXMenuButton",  "AXLink",     "AXTextField",
    "AXTextArea", "AXComboBox",    "AXSlider",   "AXTab",
    "AXTabGroup", "AXCell",        "AXImage",    "AXToolbar",
    "button",     "checkbox",      "radio",      "link",
    "text",       "field",         "slider",     "tab",
    "menuitem",
};

pub const skip_roles = [_][]const u8{
    "AXApplication", "AXWindow",   "AXGroup",   "AXScrollArea",
    "AXScrollBar",   "AXSplitter", "AXToolbar", "AXUnknown",
    "application",   "window",     "group",
};

fn containsIgnoreCase(haystack: []const u8, needle: []const u8) bool {
    if (needle.len == 0 or needle.len > haystack.len) return false;
    var i: usize = 0;
    while (i + needle.len <= haystack.len) : (i += 1) {
        if (std.ascii.eqlIgnoreCase(haystack[i..][0..needle.len], needle)) return true;
    }
    return false;
}

pub fn roleIsClickable(role: []const u8) bool {
    for (skip_roles) |item| {
        if (std.ascii.eqlIgnoreCase(role, item)) return false;
    }
    for (click_roles) |item| {
        if (std.ascii.eqlIgnoreCase(role, item)) return true;
        if (containsIgnoreCase(role, item)) return true;
    }
    return false;
}

fn axCopyString(element: AXUIElementRef, attr: [*:0]const u8, gpa: std.mem.Allocator) []const u8 {
    const key = axAttrString(attr);
    defer cfRelease(@ptrCast(@constCast(key)));
    var raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(element, key, &raw) != kAXErrorSuccess or raw == null) return "";
    defer cfRelease(raw);
    if (CFGetTypeID(raw) != CFStringGetTypeID()) return "";
    return cfStringAlloc(gpa, @ptrCast(raw)) catch "";
}

fn axCopyPoint(element: AXUIElementRef, attr: [*:0]const u8) ?CGPoint {
    const key = axAttrString(attr);
    defer cfRelease(@ptrCast(@constCast(key)));
    var raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(element, key, &raw) != kAXErrorSuccess or raw == null) return null;
    defer cfRelease(raw);
    var point: CGPoint = .{ .x = 0, .y = 0 };
    if (AXValueGetValue(raw, kAXValueTypeCGPoint, &point) == 0) return null;
    return point;
}

fn axCopySize(element: AXUIElementRef, attr: [*:0]const u8) ?CGSize {
    const key = axAttrString(attr);
    defer cfRelease(@ptrCast(@constCast(key)));
    var raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(element, key, &raw) != kAXErrorSuccess or raw == null) return null;
    defer cfRelease(raw);
    var size: CGSize = .{ .width = 0, .height = 0 };
    if (AXValueGetValue(raw, kAXValueTypeCGSize, &size) == 0) return null;
    return size;
}

fn walkAx(
    element: AXUIElementRef,
    gpa: std.mem.Allocator,
    candidates: *std.ArrayList(Candidate),
    depth: u32,
    visited: *u32,
    limit: u32,
) void {
    if (depth > 12 or visited.* >= 500 or candidates.items.len >= limit) return;
    visited.* += 1;
    const role = axCopyString(element, "AXRole", gpa);
    const name = axCopyString(element, "AXTitle", gpa);
    const value = if (name.len == 0) axCopyString(element, "AXValue", gpa) else name;
    const pos = axCopyPoint(element, "AXPosition");
    const size = axCopySize(element, "AXSize");
    if (roleIsClickable(role) and pos != null and size != null) {
        const w: i32 = @intFromFloat(@round(size.?.width));
        const h: i32 = @intFromFloat(@round(size.?.height));
        if (w >= 2 and h >= 2) {
            const x: i32 = @intFromFloat(@round(pos.?.x));
            // AX position is already top-left in screen space (Y down).
            const y: i32 = @intFromFloat(@round(pos.?.y));
            candidates.append(gpa, .{
                .x = x,
                .y = y,
                .w = w,
                .h = h,
                .area = w * h,
                .name = if (value.len > 0) value else name,
                .role = role,
            }) catch {};
        }
    }
    const children_key = axAttrString("AXChildren");
    defer cfRelease(@ptrCast(@constCast(children_key)));
    var raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(element, children_key, &raw) != kAXErrorSuccess or raw == null) return;
    defer cfRelease(raw);
    if (CFGetTypeID(raw) != CFArrayGetTypeID()) return;
    const arr: CFArrayRef = @ptrCast(raw);
    const n: usize = @intCast(@min(CFArrayGetCount(arr), 80));
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const child: AXUIElementRef = @ptrCast(CFArrayGetValueAtIndex(arr, @intCast(i)));
        if (child != null) walkAx(child, gpa, candidates, depth + 1, visited, limit);
    }
}

pub fn a11ySnapshotJson(limit: u32) Error![]u8 {
    try requireAccessibility();
    const system = AXUIElementCreateSystemWide() orelse return error.OperationFailed;
    defer cfRelease(@ptrCast(system));
    const focused_app_key = axAttrString("AXFocusedApplication");
    defer cfRelease(@ptrCast(@constCast(focused_app_key)));
    var app_raw: CFTypeRef = null;
    if (AXUIElementCopyAttributeValue(system, focused_app_key, &app_raw) != kAXErrorSuccess or app_raw == null) {
        return error.OperationFailed;
    }
    defer cfRelease(app_raw);
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    var candidates: std.ArrayList(Candidate) = .empty;
    var visited: u32 = 0;
    const cap = @max(limit, 1);
    walkAx(@ptrCast(app_raw), gpa, &candidates, 0, &visited, cap);
    return std.json.Stringify.valueAlloc(allocator, candidates.items, .{}) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// Tests (run on macos-15 without granting permissions)
// ---------------------------------------------------------------------------

test "event Y conversion flips around the primary display height" {
    try std.testing.expectEqual(@as(f64, 800), eventYFromTopLeft(0, 800));
    try std.testing.expectEqual(@as(f64, 0), eventYFromTopLeft(800, 800));
    try std.testing.expectEqual(@as(f64, 400), eventYFromTopLeft(400, 800));
    const r = displayRectTopLeft(.{
        .origin = .{ .x = 0, .y = 0 },
        .size = .{ .width = 1440, .height = 900 },
    }, 900);
    try std.testing.expectEqual(@as(i32, 0), r.left);
    try std.testing.expectEqual(@as(i32, 0), r.top);
    try std.testing.expectEqual(@as(i32, 1440), r.right);
    try std.testing.expectEqual(@as(i32, 900), r.bottom);
}

test "vk to mac keycode maps letters, modifiers, and arrows" {
    try std.testing.expectEqual(@as(CGKeyCode, 0x00), keyCodeFromVk(0x41).?); // A
    try std.testing.expectEqual(@as(CGKeyCode, 0x06), keyCodeFromVk(0x5A).?); // Z
    try std.testing.expectEqual(@as(CGKeyCode, 0x37), keyCodeFromVk(0x5B).?); // Command
    try std.testing.expectEqual(@as(CGKeyCode, 0x7B), keyCodeFromVk(0x25).?); // Left
    try std.testing.expect(keyCodeFromVk(0xFF) == null);
}

test "clickable AX role filter" {
    try std.testing.expect(roleIsClickable("AXButton"));
    try std.testing.expect(roleIsClickable("AXTextField"));
    try std.testing.expect(!roleIsClickable("AXWindow"));
    try std.testing.expect(!roleIsClickable("AXApplication"));
}

test "accessibility and screen-recording denials are AccessDenied" {
    // CI runners do not grant these TCC permissions. The check must fail
    // closed with a distinct os error so the Go layer can name the pane.
    if (!accessibilityTrusted()) {
        const err = requireAccessibility();
        try std.testing.expectError(error.AccessDenied, err);
        try std.testing.expectEqual(os_err_accessibility, host.lastOsErrorForTest());
    }
    if (!screenRecordingTrusted()) {
        const err = requireScreenRecording();
        try std.testing.expectError(error.AccessDenied, err);
        try std.testing.expectEqual(os_err_screen_recording, host.lastOsErrorForTest());
    }
}

test "a11y snapshot fails closed without Accessibility" {
    if (accessibilityTrusted()) return;
    try std.testing.expectError(error.AccessDenied, a11ySnapshotJson(8));
}

test "capture fails closed without Screen Recording" {
    if (screenRecordingTrusted()) return;
    try std.testing.expectError(error.AccessDenied, captureRegion(0, 0, 16, 16, 4));
}

test "move_resize AX value types match the AXValue public enum" {
    try std.testing.expectEqual(@as(u32, 1), kAXValueTypeCGPoint);
    try std.testing.expectEqual(@as(u32, 2), kAXValueTypeCGSize);
}

test "killTree rejects pid 0 and 1" {
    try std.testing.expectError(error.InvalidArgument, killTree(0));
    try std.testing.expectError(error.InvalidArgument, killTree(1));
}

test "spawnHidden rejects a relative argv" {
    try std.testing.expectError(error.InvalidArgument, spawnHidden("[\"sleep\",\"1\"]", "", ""));
}
