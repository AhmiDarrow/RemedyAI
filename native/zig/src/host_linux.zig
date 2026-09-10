//! X11 / XTest implementation of the host primitives declared in `host.zig`.
//!
//! Built only on Linux (`builtin.os.tag == .linux`). Prefer direct Xlib/XTest
//! over shelling to xdotool. Pure Wayland without an XWayland display returns
//! `OperationFailed` (no DISPLAY). AT-SPI lives in `atspi_linux.zig`.

const std = @import("std");
const host = @import("host.zig");
const atspi = @import("atspi_linux.zig");

const Error = host.Error;
const allocator = host.allocator;

// ---------------------------------------------------------------------------
// X11 / XTest surface
// ---------------------------------------------------------------------------

const Display = opaque {};
const Window = c_ulong;
const Atom = c_ulong;
const Time = c_ulong;
const KeyCode = u8;
const KeySym = c_ulong;
const Bool = c_int;
const Status = c_int;
const Drawable = c_ulong;

const XImage = extern struct {
    width: c_int,
    height: c_int,
    xoffset: c_int,
    format: c_int,
    data: ?[*]u8,
    byte_order: c_int,
    bitmap_unit: c_int,
    bitmap_bit_order: c_int,
    bitmap_pad: c_int,
    depth: c_int,
    bytes_per_line: c_int,
    bits_per_pixel: c_int,
    red_mask: c_ulong,
    green_mask: c_ulong,
    blue_mask: c_ulong,
    obdata: ?*anyopaque,
    f: [4]usize,
};

const XWindowAttributes = extern struct {
    x: c_int,
    y: c_int,
    width: c_int,
    height: c_int,
    border_width: c_int,
    depth: c_int,
    visual: ?*anyopaque,
    root: Window,
    class: c_int,
    bit_gravity: c_int,
    win_gravity: c_int,
    backing_store: c_int,
    backing_planes: c_ulong,
    backing_pixel: c_ulong,
    save_under: Bool,
    colormap: c_ulong,
    map_installed: Bool,
    map_state: c_int,
    all_event_masks: c_long,
    your_event_mask: c_long,
    do_not_propagate_mask: c_long,
    override_redirect: Bool,
    screen: ?*anyopaque,
};

const XTextProperty = extern struct {
    value: ?[*]u8,
    encoding: Atom,
    format: c_int,
    nitems: c_ulong,
};

const XEvent = extern struct {
    pad: [24]c_long,
};

const XButtonEvent = extern struct {
    type: c_int,
    serial: c_ulong,
    send_event: Bool,
    display: ?*Display,
    window: Window,
    root: Window,
    subwindow: Window,
    time: Time,
    x: c_int,
    y: c_int,
    x_root: c_int,
    y_root: c_int,
    state: c_uint,
    button: c_uint,
    same_screen: Bool,
};

const IsUnmapped = 0;
const IsUnviewable = 1;
const IsViewable = 2;
const ZPixmap = 2;
const AllPlanes: c_ulong = ~@as(c_ulong, 0);
const CurrentTime: Time = 0;
const PropModeReplace: c_int = 0;
const XA_WINDOW: Atom = 33;
const XA_ATOM: Atom = 4;
const XA_CARDINAL: Atom = 6;
const XA_STRING: Atom = 31;
const Success: Status = 0;
const SelectionNotify = 31;
const SelectionRequest = 30;
const PropertyNotify = 28;
const PropertyNewValue = 0;
const False: Bool = 0;
const True: Bool = 1;
const CWEventMask: c_ulong = 1 << 11;
const PropertyChangeMask: c_long = 1 << 22;
const StructureNotifyMask: c_long = 1 << 17;

const XK_Control_L: KeySym = 0xffe3;
const XK_Shift_L: KeySym = 0xffe1;
const XK_Alt_L: KeySym = 0xffe9;
const XK_Super_L: KeySym = 0xffeb;
const XK_Return: KeySym = 0xff0d;
const XK_Tab: KeySym = 0xff09;
const XK_Escape: KeySym = 0xff1b;
const XK_BackSpace: KeySym = 0xff08;
const XK_Delete: KeySym = 0xffff;
const XK_space: KeySym = 0x0020;
const XK_Left: KeySym = 0xff51;
const XK_Up: KeySym = 0xff52;
const XK_Right: KeySym = 0xff53;
const XK_Down: KeySym = 0xff54;
const XK_Home: KeySym = 0xff50;
const XK_End: KeySym = 0xff57;
const XK_Page_Up: KeySym = 0xff55;
const XK_Page_Down: KeySym = 0xff56;
const XK_Insert: KeySym = 0xff63;
const XK_F1: KeySym = 0xffbe;

const Button1: c_uint = 1;
const Button2: c_uint = 2;
const Button3: c_uint = 3;
const Button4: c_uint = 4;
const Button5: c_uint = 5;
const Button6: c_uint = 6;
const Button7: c_uint = 7;

extern "X11" fn XOpenDisplay(name: ?[*:0]const u8) callconv(.c) ?*Display;
extern "X11" fn XCloseDisplay(display: *Display) callconv(.c) c_int;
extern "X11" fn XDefaultRootWindow(display: *Display) callconv(.c) Window;
extern "X11" fn XDefaultScreen(display: *Display) callconv(.c) c_int;
extern "X11" fn XDisplayWidth(display: *Display, screen: c_int) callconv(.c) c_int;
extern "X11" fn XDisplayHeight(display: *Display, screen: c_int) callconv(.c) c_int;
extern "X11" fn XFlush(display: *Display) callconv(.c) c_int;
extern "X11" fn XSync(display: *Display, discard: Bool) callconv(.c) c_int;
extern "X11" fn XWarpPointer(
    display: *Display,
    src_w: Window,
    dest_w: Window,
    src_x: c_int,
    src_y: c_int,
    src_width: c_uint,
    src_height: c_uint,
    dest_x: c_int,
    dest_y: c_int,
) callconv(.c) c_int;
extern "X11" fn XQueryPointer(
    display: *Display,
    window: Window,
    root_return: *Window,
    child_return: *Window,
    root_x: *c_int,
    root_y: *c_int,
    win_x: *c_int,
    win_y: *c_int,
    mask: *c_uint,
) callconv(.c) Bool;
extern "X11" fn XGetImage(
    display: *Display,
    drawable: Drawable,
    x: c_int,
    y: c_int,
    width: c_uint,
    height: c_uint,
    plane_mask: c_ulong,
    format: c_int,
) callconv(.c) ?*XImage;
extern "X11" fn XDestroyImage(image: *XImage) callconv(.c) c_int;
extern "X11" fn XGetWindowAttributes(display: *Display, window: Window, attrs: *XWindowAttributes) callconv(.c) Status;
extern "X11" fn XTranslateCoordinates(
    display: *Display,
    src_w: Window,
    dest_w: Window,
    src_x: c_int,
    src_y: c_int,
    dest_x: *c_int,
    dest_y: *c_int,
    child: *Window,
) callconv(.c) Bool;
extern "X11" fn XInternAtom(display: *Display, name: [*:0]const u8, only_if_exists: Bool) callconv(.c) Atom;
extern "X11" fn XGetWindowProperty(
    display: *Display,
    window: Window,
    property: Atom,
    long_offset: c_long,
    long_length: c_long,
    delete: Bool,
    req_type: Atom,
    actual_type: *Atom,
    actual_format: *c_int,
    nitems: *c_ulong,
    bytes_after: *c_ulong,
    prop: *?[*]u8,
) callconv(.c) c_int;
extern "X11" fn XFree(data: ?*anyopaque) callconv(.c) c_int;
extern "X11" fn XGetWMName(display: *Display, window: Window, prop: *XTextProperty) callconv(.c) Status;
extern "X11" fn Xutf8TextPropertyToTextList(
    display: *Display,
    prop: *XTextProperty,
    list_return: *?[*]?[*:0]u8,
    count_return: *c_int,
) callconv(.c) c_int;
extern "X11" fn XFreeStringList(list: ?[*]?[*:0]u8) callconv(.c) void;
extern "X11" fn XFetchName(display: *Display, window: Window, name: *?[*:0]u8) callconv(.c) Status;
extern "X11" fn XRaiseWindow(display: *Display, window: Window) callconv(.c) c_int;
extern "X11" fn XMapRaised(display: *Display, window: Window) callconv(.c) c_int;
extern "X11" fn XIconifyWindow(display: *Display, window: Window, screen: c_int) callconv(.c) Status;
extern "X11" fn XMoveResizeWindow(display: *Display, window: Window, x: c_int, y: c_int, width: c_uint, height: c_uint) callconv(.c) c_int;
extern "X11" fn XMoveWindow(display: *Display, window: Window, x: c_int, y: c_int) callconv(.c) c_int;
extern "X11" fn XResizeWindow(display: *Display, window: Window, width: c_uint, height: c_uint) callconv(.c) c_int;
extern "X11" fn XGetInputFocus(display: *Display, focus: *Window, revert: *c_int) callconv(.c) c_int;
extern "X11" fn XSetInputFocus(display: *Display, focus: Window, revert_to: c_int, time: Time) callconv(.c) c_int;
extern "X11" fn XSendEvent(display: *Display, window: Window, propagate: Bool, mask: c_long, event: *XEvent) callconv(.c) Status;
extern "X11" fn XKeysymToKeycode(display: *Display, keysym: KeySym) callconv(.c) KeyCode;
extern "X11" fn XStringToKeysym(string: [*:0]const u8) callconv(.c) KeySym;
extern "X11" fn XCreateSimpleWindow(
    display: *Display,
    parent: Window,
    x: c_int,
    y: c_int,
    width: c_uint,
    height: c_uint,
    border_width: c_uint,
    border: c_ulong,
    background: c_ulong,
) callconv(.c) Window;
extern "X11" fn XDestroyWindow(display: *Display, window: Window) callconv(.c) c_int;
extern "X11" fn XSelectInput(display: *Display, window: Window, mask: c_long) callconv(.c) c_int;
extern "X11" fn XConvertSelection(
    display: *Display,
    selection: Atom,
    target: Atom,
    property: Atom,
    requestor: Window,
    time: Time,
) callconv(.c) c_int;
extern "X11" fn XSetSelectionOwner(display: *Display, selection: Atom, owner: Window, time: Time) callconv(.c) c_int;
extern "X11" fn XGetSelectionOwner(display: *Display, selection: Atom) callconv(.c) Window;
extern "X11" fn XChangeProperty(
    display: *Display,
    window: Window,
    property: Atom,
    typ: Atom,
    format: c_int,
    mode: c_int,
    data: [*]const u8,
    nelements: c_int,
) callconv(.c) c_int;
extern "X11" fn XDeleteProperty(display: *Display, window: Window, property: Atom) callconv(.c) c_int;
extern "X11" fn XPending(display: *Display) callconv(.c) c_int;
extern "X11" fn XNextEvent(display: *Display, event: *XEvent) callconv(.c) c_int;
extern "X11" fn XConnectionNumber(display: *Display) callconv(.c) c_int;
extern "X11" fn XQueryTree(
    display: *Display,
    window: Window,
    root: *Window,
    parent: *Window,
    children: *?[*]Window,
    nchildren: *c_uint,
) callconv(.c) Status;
extern "X11" fn XGetClassHint(display: *Display, window: Window, class_hint: *XClassHint) callconv(.c) Status;

const XClassHint = extern struct {
    res_name: ?[*:0]u8,
    res_class: ?[*:0]u8,
};

extern "Xtst" fn XTestFakeMotionEvent(
    display: *Display,
    screen: c_int,
    x: c_int,
    y: c_int,
    delay: c_ulong,
) callconv(.c) c_int;
extern "Xtst" fn XTestFakeButtonEvent(
    display: *Display,
    button: c_uint,
    is_press: Bool,
    delay: c_ulong,
) callconv(.c) c_int;
extern "Xtst" fn XTestFakeKeyEvent(
    display: *Display,
    keycode: c_uint,
    is_press: Bool,
    delay: c_ulong,
) callconv(.c) c_int;

fn failErrno() Error {
    host.setOsError(@intCast(std.posix.errno(std.posix.E.IO)));
    return error.OperationFailed;
}

fn failMsg(code: u32) Error {
    host.setOsError(code);
    return error.OperationFailed;
}

const DisplayGuard = struct {
    display: *Display,

    fn open() Error!DisplayGuard {
        const display = XOpenDisplay(null) orelse return failMsg(1);
        return .{ .display = display };
    }

    fn close(self: DisplayGuard) void {
        _ = XCloseDisplay(self.display);
    }

    fn flush(self: DisplayGuard) void {
        _ = XFlush(self.display);
    }
};

const Timespec = extern struct {
    sec: isize,
    nsec: isize,
};

extern "c" fn nanosleep(req: *const Timespec, rem: ?*Timespec) callconv(.c) c_int;
extern "c" fn clock_gettime(clock_id: c_int, tp: *Timespec) callconv(.c) c_int;

const CLOCK_MONOTONIC: c_int = 1;

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

fn windowFrom(raw: u64) Error!Window {
    if (raw == 0) return error.InvalidArgument;
    return @intCast(raw);
}

// ---------------------------------------------------------------------------
// DPI / monitors / geometry
// ---------------------------------------------------------------------------

pub fn enableDpiAwareness() Error!void {
    // X11 physical pixels are already what we want; no-op success.
}

pub fn virtualScreen() Error!host.VirtualScreen {
    const guard = try DisplayGuard.open();
    defer guard.close();
    const screen = XDefaultScreen(guard.display);
    const width = XDisplayWidth(guard.display, screen);
    const height = XDisplayHeight(guard.display, screen);
    if (width <= 0 or height <= 0) return failMsg(2);
    return .{ .left = 0, .top = 0, .width = width, .height = height };
}

pub fn listMonitorsJson() Error![]u8 {
    const screen = try virtualScreen();
    const entries = [_]host.MonitorEntry{.{
        .index = 0,
        .left = screen.left,
        .top = screen.top,
        .right = screen.left + screen.width,
        .bottom = screen.top + screen.height,
        .width = screen.width,
        .height = screen.height,
        .primary = true,
        .scale = 1.0,
    }};
    return host.monitorsJson(allocator, &entries);
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

fn maskShift(mask: c_ulong) u5 {
    if (mask == 0) return 0;
    var shift: u5 = 0;
    var value = mask;
    while ((value & 1) == 0) : (shift += 1) value >>= 1;
    return shift;
}

fn captureRoot(left: i32, top: i32, width: i32, height: i32, bytes_per_pixel: u32) Error![]u8 {
    try validateBpp(bytes_per_pixel);
    if (width <= 0 or height <= 0) return error.InvalidArgument;
    const guard = try DisplayGuard.open();
    defer guard.close();
    const root = XDefaultRootWindow(guard.display);
    const image = XGetImage(
        guard.display,
        root,
        left,
        top,
        @intCast(width),
        @intCast(height),
        AllPlanes,
        ZPixmap,
    ) orelse return failMsg(3);
    defer _ = XDestroyImage(image);

    const stride = strideFor(width, bytes_per_pixel);
    const out = allocator.alloc(u8, stride * @as(usize, @intCast(height))) catch return error.OutOfMemory;
    errdefer allocator.free(out);
    @memset(out, 0);

    const red_shift = maskShift(image.red_mask);
    const green_shift = maskShift(image.green_mask);
    const blue_shift = maskShift(image.blue_mask);
    const bpp = image.bits_per_pixel;
    const src = image.data orelse return failMsg(4);

    var y: i32 = 0;
    while (y < height) : (y += 1) {
        const row = src[@as(usize, @intCast(y)) * @as(usize, @intCast(image.bytes_per_line)) ..];
        const dst = out[@as(usize, @intCast(y)) * stride ..];
        var x: i32 = 0;
        while (x < width) : (x += 1) {
            const pixel: u32 = switch (bpp) {
                32, 24 => blk: {
                    const o = @as(usize, @intCast(x)) * @as(usize, @intCast(@divTrunc(bpp, 8)));
                    break :blk @as(u32, row[o]) |
                        (@as(u32, row[o + 1]) << 8) |
                        (@as(u32, row[o + 2]) << 16) |
                        (if (bpp == 32) @as(u32, row[o + 3]) << 24 else 0);
                },
                16 => blk: {
                    const o = @as(usize, @intCast(x)) * 2;
                    break :blk @as(u32, row[o]) | (@as(u32, row[o + 1]) << 8);
                },
                else => return failMsg(5),
            };
            const r: u8 = @truncate((pixel & @as(u32, @intCast(image.red_mask))) >> red_shift);
            const g: u8 = @truncate((pixel & @as(u32, @intCast(image.green_mask))) >> green_shift);
            const b: u8 = @truncate((pixel & @as(u32, @intCast(image.blue_mask))) >> blue_shift);
            const o = @as(usize, @intCast(x)) * bytes_per_pixel;
            dst[o] = b;
            dst[o + 1] = g;
            dst[o + 2] = r;
            if (bytes_per_pixel == 4) dst[o + 3] = 255;
        }
    }
    return out;
}

pub fn captureVirtualScreen(bytes_per_pixel: u32) Error!host.Pixels {
    const screen = try virtualScreen();
    const bytes = try captureRoot(screen.left, screen.top, screen.width, screen.height, bytes_per_pixel);
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
    const bytes = try captureRoot(left, top, width, height, bytes_per_pixel);
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
    // X11 has no PrintWindow analogue; capture the window's on-screen rect.
    const rect = try windowRect(raw_hwnd);
    const width = rect.right - rect.left;
    const height = rect.bottom - rect.top;
    if (width < 2 or height < 2) return error.InvalidArgument;
    return captureRegion(rect.left, rect.top, width, height, bytes_per_pixel);
}

// ---------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------

fn buttonNumber(button: host.MouseButton) c_uint {
    return switch (button) {
        .left => Button1,
        .middle => Button2,
        .right => Button3,
    };
}

pub fn mouseMove(x: i32, y: i32) Error!void {
    const guard = try DisplayGuard.open();
    defer guard.close();
    const screen = XDefaultScreen(guard.display);
    if (XTestFakeMotionEvent(guard.display, screen, x, y, CurrentTime) == 0) return failMsg(10);
    guard.flush();
}

pub fn mouseClick(x: i32, y: i32, button: host.MouseButton, clicks: u32) Error!void {
    try mouseMove(x, y);
    sleepMs(20);
    const guard = try DisplayGuard.open();
    defer guard.close();
    const btn = buttonNumber(button);
    var remaining: u32 = @max(clicks, 1);
    while (remaining > 0) : (remaining -= 1) {
        if (XTestFakeButtonEvent(guard.display, btn, True, CurrentTime) == 0) return failMsg(11);
        if (XTestFakeButtonEvent(guard.display, btn, False, CurrentTime) == 0) return failMsg(11);
        guard.flush();
        sleepMs(40);
    }
}

pub fn mouseButton(button: host.MouseButton, pressed: bool) Error!void {
    const guard = try DisplayGuard.open();
    defer guard.close();
    if (XTestFakeButtonEvent(guard.display, buttonNumber(button), if (pressed) True else False, CurrentTime) == 0) {
        return failMsg(12);
    }
    guard.flush();
}

pub fn mouseDrag(x1: i32, y1: i32, x2: i32, y2: i32, steps: u32) Error!void {
    try mouseMove(x1, y1);
    sleepMs(20);
    try mouseButton(.left, true);
    sleepMs(50);
    const n: u32 = @max(steps, 2);
    var i: u32 = 1;
    while (i <= n) : (i += 1) {
        const t: f64 = @as(f64, @floatFromInt(i)) / @as(f64, @floatFromInt(n));
        const fx = @as(f64, @floatFromInt(x1)) + @as(f64, @floatFromInt(x2 - x1)) * t;
        const fy = @as(f64, @floatFromInt(y1)) + @as(f64, @floatFromInt(y2 - y1)) * t;
        try mouseMove(@intFromFloat(fx), @intFromFloat(fy));
        sleepMs(12);
    }
    sleepMs(120);
    try mouseButton(.left, false);
}

pub fn mouseScroll(x: i32, y: i32, dx: i32, dy: i32) Error!void {
    try mouseMove(x, y);
    sleepMs(20);
    const guard = try DisplayGuard.open();
    defer guard.close();
    if (dy != 0) {
        const btn: c_uint = if (dy > 0) Button4 else Button5;
        var n: i32 = @intCast(@abs(dy));
        while (n > 0) : (n -= 1) {
            _ = XTestFakeButtonEvent(guard.display, btn, True, CurrentTime);
            _ = XTestFakeButtonEvent(guard.display, btn, False, CurrentTime);
        }
    }
    if (dx != 0) {
        const btn: c_uint = if (dx > 0) Button7 else Button6;
        var n: i32 = @intCast(@abs(dx));
        while (n > 0) : (n -= 1) {
            _ = XTestFakeButtonEvent(guard.display, btn, True, CurrentTime);
            _ = XTestFakeButtonEvent(guard.display, btn, False, CurrentTime);
        }
    }
    guard.flush();
}

fn keysymFromVk(vk: u16) ?KeySym {
    return switch (vk) {
        0x0D => XK_Return,
        0x09 => XK_Tab,
        0x1B => XK_Escape,
        0x08 => XK_BackSpace,
        0x2E => XK_Delete,
        0x20 => XK_space,
        0x25 => XK_Left,
        0x26 => XK_Up,
        0x27 => XK_Right,
        0x28 => XK_Down,
        0x24 => XK_Home,
        0x23 => XK_End,
        0x21 => XK_Page_Up,
        0x22 => XK_Page_Down,
        0x2D => XK_Insert,
        0x10 => XK_Shift_L,
        0x11 => XK_Control_L,
        0x12 => XK_Alt_L,
        0x5B => XK_Super_L,
        0x30...0x39 => @as(KeySym, vk), // '0'-'9'
        0x41...0x5A => @as(KeySym, vk + 0x20), // a-z
        0x70...0x7B => XK_F1 + (vk - 0x70),
        else => null,
    };
}

fn keysymFromCodepoint(codepoint: u21) KeySym {
    if (codepoint <= 0xFF) return @intCast(codepoint);
    return 0x01000000 | @as(KeySym, codepoint);
}

fn fakeKey(display: *Display, keysym: KeySym, press: bool) Error!void {
    const code = XKeysymToKeycode(display, keysym);
    if (code == 0) return failMsg(13);
    if (XTestFakeKeyEvent(display, code, if (press) True else False, CurrentTime) == 0) return failMsg(14);
}

pub fn typeText(utf8: []const u8, per_char_delay_ms: u32) Error!void {
    const guard = try DisplayGuard.open();
    defer guard.close();
    const view = std.unicode.Utf8View.init(utf8) catch return error.InvalidArgument;
    var iterator = view.iterator();
    while (iterator.nextCodepoint()) |codepoint| {
        if (codepoint == '\r') {
            if (iterator.i < utf8.len and utf8[iterator.i] == '\n') continue;
            try fakeKey(guard.display, XK_Return, true);
            try fakeKey(guard.display, XK_Return, false);
        } else if (codepoint == '\n') {
            try fakeKey(guard.display, XK_Return, true);
            try fakeKey(guard.display, XK_Return, false);
        } else {
            const keysym = keysymFromCodepoint(codepoint);
            try fakeKey(guard.display, keysym, true);
            try fakeKey(guard.display, keysym, false);
        }
        guard.flush();
        if (per_char_delay_ms > 0) sleepMs(per_char_delay_ms);
    }
}

pub fn keyCombo(vks: []const u16) Error!void {
    const guard = try DisplayGuard.open();
    defer guard.close();
    var keysyms = allocator.alloc(KeySym, vks.len) catch return error.OutOfMemory;
    defer allocator.free(keysyms);
    for (vks, 0..) |vk, i| {
        keysyms[i] = keysymFromVk(vk) orelse return error.InvalidArgument;
    }
    for (keysyms) |ks| try fakeKey(guard.display, ks, true);
    var index = keysyms.len;
    while (index > 0) {
        index -= 1;
        try fakeKey(guard.display, keysyms[index], false);
    }
    guard.flush();
}

pub fn keyHold(vk: u16, hold_ms: u32) Error!void {
    const keysym = keysymFromVk(vk) orelse return error.InvalidArgument;
    const guard = try DisplayGuard.open();
    defer guard.close();
    try fakeKey(guard.display, keysym, true);
    guard.flush();
    sleepMs(hold_ms);
    try fakeKey(guard.display, keysym, false);
    guard.flush();
}

pub fn vkKeyScan(codepoint: u32) Error!i32 {
    // Linux has no VkKeyScanW. Latin-1 printable letters/digits map 1:1 without shift.
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
// Windows
// ---------------------------------------------------------------------------

fn readAtomWindows(display: *Display, root: Window, prop_atom: Atom, gpa: std.mem.Allocator) Error![]Window {
    var actual_type: Atom = 0;
    var actual_format: c_int = 0;
    var nitems: c_ulong = 0;
    var bytes_after: c_ulong = 0;
    var prop: ?[*]u8 = null;
    if (XGetWindowProperty(
        display,
        root,
        prop_atom,
        0,
        0xFFFF,
        False,
        XA_WINDOW,
        &actual_type,
        &actual_format,
        &nitems,
        &bytes_after,
        &prop,
    ) != Success or prop == null or actual_format != 32) {
        if (prop) |p| _ = XFree(p);
        return gpa.alloc(Window, 0) catch return error.OutOfMemory;
    }
    defer _ = XFree(prop);
    const raw: [*]align(1) Window = @ptrCast(prop.?);
    const out = gpa.alloc(Window, @intCast(nitems)) catch return error.OutOfMemory;
    @memcpy(out, raw[0..nitems]);
    return out;
}

fn windowTitle(display: *Display, window: Window, gpa: std.mem.Allocator) Error![]u8 {
    var prop: XTextProperty = undefined;
    if (XGetWMName(display, window, &prop) != 0 and prop.value != null and prop.nitems > 0) {
        defer _ = XFree(prop.value);
        var list: ?[*]?[*:0]u8 = null;
        var count: c_int = 0;
        if (Xutf8TextPropertyToTextList(display, &prop, &list, &count) >= 0 and count > 0 and list != null) {
            defer XFreeStringList(list);
            if (list.?[0]) |text| {
                return gpa.dupe(u8, std.mem.span(text)) catch return error.OutOfMemory;
            }
        }
        if (prop.encoding == XA_STRING) {
            return gpa.dupe(u8, prop.value.?[0..prop.nitems]) catch return error.OutOfMemory;
        }
    }
    var name: ?[*:0]u8 = null;
    if (XFetchName(display, window, &name) != 0 and name != null) {
        defer _ = XFree(name);
        return gpa.dupe(u8, std.mem.span(name.?)) catch return error.OutOfMemory;
    }
    return gpa.dupe(u8, "") catch return error.OutOfMemory;
}

fn windowClassName(display: *Display, window: Window, gpa: std.mem.Allocator) Error![]u8 {
    var hint = XClassHint{ .res_name = null, .res_class = null };
    if (XGetClassHint(display, window, &hint) != 0) {
        defer {
            if (hint.res_name) |n| _ = XFree(n);
            if (hint.res_class) |c| _ = XFree(c);
        }
        if (hint.res_class) |class| {
            return gpa.dupe(u8, std.mem.span(class)) catch return error.OutOfMemory;
        }
    }
    return gpa.dupe(u8, "") catch return error.OutOfMemory;
}

fn windowPid(display: *Display, window: Window) u32 {
    const pid_atom = XInternAtom(display, "_NET_WM_PID", True);
    if (pid_atom == 0) return 0;
    var actual_type: Atom = 0;
    var actual_format: c_int = 0;
    var nitems: c_ulong = 0;
    var bytes_after: c_ulong = 0;
    var prop: ?[*]u8 = null;
    if (XGetWindowProperty(display, window, pid_atom, 0, 1, False, XA_CARDINAL, &actual_type, &actual_format, &nitems, &bytes_after, &prop) != Success or prop == null) {
        return 0;
    }
    defer _ = XFree(prop);
    if (nitems == 0 or actual_format != 32) return 0;
    const values: [*]align(1) c_ulong = @ptrCast(prop.?);
    return @truncate(values[0]);
}

fn isMinimized(display: *Display, window: Window) bool {
    const state = XInternAtom(display, "WM_STATE", True);
    if (state == 0) return false;
    var actual_type: Atom = 0;
    var actual_format: c_int = 0;
    var nitems: c_ulong = 0;
    var bytes_after: c_ulong = 0;
    var prop: ?[*]u8 = null;
    if (XGetWindowProperty(display, window, state, 0, 2, False, state, &actual_type, &actual_format, &nitems, &bytes_after, &prop) != Success or prop == null) {
        return false;
    }
    defer _ = XFree(prop);
    if (nitems == 0) return false;
    const values: [*]align(1) c_ulong = @ptrCast(prop.?);
    // IconicState = 3
    return values[0] == 3;
}

pub fn windowRect(raw_hwnd: u64) Error!host.Rect {
    const window = try windowFrom(raw_hwnd);
    const guard = try DisplayGuard.open();
    defer guard.close();
    var attrs: XWindowAttributes = undefined;
    if (XGetWindowAttributes(guard.display, window, &attrs) == 0) return failMsg(20);
    const root = XDefaultRootWindow(guard.display);
    var x: c_int = 0;
    var y: c_int = 0;
    var child: Window = 0;
    _ = XTranslateCoordinates(guard.display, window, root, 0, 0, &x, &y, &child);
    return .{
        .left = x,
        .top = y,
        .right = x + attrs.width,
        .bottom = y + attrs.height,
    };
}

pub fn windowClass(raw_hwnd: u64) Error![]u8 {
    const window = try windowFrom(raw_hwnd);
    const guard = try DisplayGuard.open();
    defer guard.close();
    return windowClassName(guard.display, window, allocator);
}

pub fn listWindowsJson(limit: u32) Error![]u8 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    const guard = try DisplayGuard.open();
    defer guard.close();
    const root = XDefaultRootWindow(guard.display);
    const stacking = XInternAtom(guard.display, "_NET_CLIENT_LIST_STACKING", True);
    const client_list = XInternAtom(guard.display, "_NET_CLIENT_LIST", True);
    const ids = if (stacking != 0)
        try readAtomWindows(guard.display, root, stacking, gpa)
    else if (client_list != 0)
        try readAtomWindows(guard.display, root, client_list, gpa)
    else
        try gpa.alloc(Window, 0);

    // Stacking is bottom→top; reverse so most-recent / topmost comes first.
    if (stacking != 0 and ids.len > 1) {
        std.mem.reverse(Window, ids);
    }

    var entries: std.ArrayList(host.WindowEntry) = .empty;
    const cap = @max(limit, 1);
    for (ids) |window| {
        if (entries.items.len >= cap) break;
        var attrs: XWindowAttributes = undefined;
        if (XGetWindowAttributes(guard.display, window, &attrs) == 0) continue;
        if (attrs.map_state != IsViewable) continue;
        const raw_title = try windowTitle(guard.display, window, gpa);
        const title = host.trimWhitespace(raw_title);
        if (title.len == 0) continue;
        var x: c_int = 0;
        var y: c_int = 0;
        var child: Window = 0;
        _ = XTranslateCoordinates(guard.display, window, root, 0, 0, &x, &y, &child);
        const width = attrs.width;
        const height = attrs.height;
        if (width < 8 or height < 8) continue;
        try entries.append(gpa, .{
            .hwnd = window,
            .title = host.truncateCodepoints(title, 200),
            .class = try windowClassName(guard.display, window, gpa),
            .pid = windowPid(guard.display, window),
            .bounds = .{ .left = x, .top = y, .right = x + width, .bottom = y + height },
            .width = width,
            .height = height,
            .visible = true,
            .minimized = isMinimized(guard.display, window),
        });
    }
    return host.windowsJson(allocator, entries.items);
}

pub const ForegroundInfo = struct { hwnd: u64, title: []u8 };

fn processImagePath(gpa: std.mem.Allocator, pid: u32) Error![]u8 {
    if (pid == 0) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    // Zig 0.16: use the Linux syscall, not std.posix.readlink (removed).
    var path_buf: [64]u8 = undefined;
    const link_path = std.fmt.bufPrintZ(&path_buf, "/proc/{d}/exe", .{pid}) catch {
        return gpa.dupe(u8, "") catch return error.OutOfMemory;
    };
    var out_buf: [std.fs.max_path_bytes]u8 = undefined;
    const rc = std.os.linux.readlink(link_path.ptr, &out_buf, out_buf.len);
    const errno = std.os.linux.errno(rc);
    if (errno != .SUCCESS) {
        return gpa.dupe(u8, "") catch return error.OutOfMemory;
    }
    return gpa.dupe(u8, out_buf[0..rc]) catch return error.OutOfMemory;
}

/// JSON `{hwnd,title,pid,exe}` for the foreground window (empty fields when none).
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
    const guard = try DisplayGuard.open();
    defer guard.close();
    const pid = windowPid(guard.display, @intCast(info.hwnd));
    const exe = try processImagePath(allocator, pid);
    defer allocator.free(exe);
    return host.jsonAlloc(.{
        .hwnd = info.hwnd,
        .title = host.truncateCodepoints(info.title, 200),
        .pid = pid,
        .exe = exe,
    });
}

pub fn foregroundWindow() Error!ForegroundInfo {
    const guard = try DisplayGuard.open();
    defer guard.close();
    // Prefer EWMH active window.
    const root = XDefaultRootWindow(guard.display);
    const active = XInternAtom(guard.display, "_NET_ACTIVE_WINDOW", True);
    if (active != 0) {
        var actual_type: Atom = 0;
        var actual_format: c_int = 0;
        var nitems: c_ulong = 0;
        var bytes_after: c_ulong = 0;
        var prop: ?[*]u8 = null;
        if (XGetWindowProperty(guard.display, root, active, 0, 1, False, XA_WINDOW, &actual_type, &actual_format, &nitems, &bytes_after, &prop) == Success and prop != null and nitems > 0) {
            defer _ = XFree(prop);
            const values: [*]align(1) Window = @ptrCast(prop.?);
            const window = values[0];
            if (window != 0) {
                return .{
                    .hwnd = window,
                    .title = try windowTitle(guard.display, window, allocator),
                };
            }
        }
    }
    var focus: Window = 0;
    var revert: c_int = 0;
    _ = XGetInputFocus(guard.display, &focus, &revert);
    if (focus == 0 or focus == 1) {
        return .{ .hwnd = 0, .title = allocator.dupe(u8, "") catch return error.OutOfMemory };
    }
    return .{ .hwnd = focus, .title = try windowTitle(guard.display, focus, allocator) };
}

pub fn focusWindow(raw_hwnd: u64) Error!bool {
    const window = try windowFrom(raw_hwnd);
    const guard = try DisplayGuard.open();
    defer guard.close();
    _ = XMapRaised(guard.display, window);
    _ = XRaiseWindow(guard.display, window);
    // RevertToParent = 2
    _ = XSetInputFocus(guard.display, window, 2, CurrentTime);
    // EWMH _NET_ACTIVE_WINDOW ClientMessage would go here; XRaise + focus covers most WMs.
    _ = XInternAtom(guard.display, "_NET_ACTIVE_WINDOW", False);
    guard.flush();
    sleepMs(50);
    const info = try foregroundWindow();
    defer allocator.free(info.title);
    return info.hwnd == window;
}

fn sendWmDelete(display: *Display, window: Window) Error!void {
    const protocols = XInternAtom(display, "WM_PROTOCOLS", True);
    const delete = XInternAtom(display, "WM_DELETE_WINDOW", True);
    if (protocols == 0 or delete == 0) {
        _ = XDestroyWindow(display, window);
        return;
    }
    // XClientMessageEvent layout inside XEvent pad: type=33 ClientMessage
    var event: XEvent = std.mem.zeroes(XEvent);
    const words: [*]c_long = @ptrCast(&event);
    words[0] = 33; // type ClientMessage
    words[1] = 0; // serial
    words[2] = 0; // send_event
    // display pointer occupies words on 64-bit; keep zeroed and use XSendEvent
    // Safer: use a properly typed client message via raw bytes.
    const ClientMessage = extern struct {
        type: c_int,
        serial: c_ulong,
        send_event: Bool,
        display: ?*Display,
        window: Window,
        message_type: Atom,
        format: c_int,
        data: extern union {
            b: [20]u8,
            s: [10]c_short,
            l: [5]c_long,
        },
    };
    var cm = std.mem.zeroes(ClientMessage);
    cm.type = 33;
    cm.display = display;
    cm.window = window;
    cm.message_type = protocols;
    cm.format = 32;
    cm.data.l[0] = @intCast(delete);
    cm.data.l[1] = @intCast(CurrentTime);
    if (XSendEvent(display, window, False, 0, @ptrCast(&cm)) == 0) return failMsg(21);
}

fn setNetWmState(display: *Display, window: Window, action: c_long, atom1: Atom, atom2: Atom) void {
    const root = XDefaultRootWindow(display);
    const state = XInternAtom(display, "_NET_WM_STATE", False);
    if (state == 0) return;
    const ClientMessage = extern struct {
        type: c_int,
        serial: c_ulong,
        send_event: Bool,
        display: ?*Display,
        window: Window,
        message_type: Atom,
        format: c_int,
        data: extern union {
            b: [20]u8,
            s: [10]c_short,
            l: [5]c_long,
        },
    };
    var cm = std.mem.zeroes(ClientMessage);
    cm.type = 33;
    cm.display = display;
    cm.window = window;
    cm.message_type = state;
    cm.format = 32;
    cm.data.l[0] = action; // 0 remove, 1 add, 2 toggle
    cm.data.l[1] = @intCast(atom1);
    cm.data.l[2] = @intCast(atom2);
    cm.data.l[3] = 1; // source indication
    _ = XSendEvent(display, root, False, (1 << 20) | (1 << 19), @ptrCast(&cm)); // SubstructureRedirect|Notify
}

pub fn manageWindow(raw_hwnd: u64, action: host.WindowAction, x: i32, y: i32, width: i32, height: i32) Error!void {
    const window = try windowFrom(raw_hwnd);
    const guard = try DisplayGuard.open();
    defer guard.close();
    var attrs: XWindowAttributes = undefined;
    if (XGetWindowAttributes(guard.display, window, &attrs) == 0) return error.InvalidArgument;
    const screen = XDefaultScreen(guard.display);
    switch (action) {
        .minimize => {
            if (XIconifyWindow(guard.display, window, screen) == 0) return failMsg(22);
        },
        .maximize => {
            const vert = XInternAtom(guard.display, "_NET_WM_STATE_MAXIMIZED_VERT", False);
            const horz = XInternAtom(guard.display, "_NET_WM_STATE_MAXIMIZED_HORZ", False);
            setNetWmState(guard.display, window, 1, vert, horz);
        },
        .restore => {
            const vert = XInternAtom(guard.display, "_NET_WM_STATE_MAXIMIZED_VERT", False);
            const horz = XInternAtom(guard.display, "_NET_WM_STATE_MAXIMIZED_HORZ", False);
            setNetWmState(guard.display, window, 0, vert, horz);
            _ = XMapRaised(guard.display, window);
        },
        .close => try sendWmDelete(guard.display, window),
        .move_resize => {
            if (width <= 0 or height <= 0) return error.InvalidArgument;
            _ = XMoveResizeWindow(guard.display, window, x, y, @intCast(width), @intCast(height));
        },
    }
    guard.flush();
}

pub fn findChildHwnd(raw_parent: u64, class_substr: []const u8, title_substr: []const u8) Error!u64 {
    const parent = try windowFrom(raw_parent);
    const guard = try DisplayGuard.open();
    defer guard.close();
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    var root_ret: Window = 0;
    var parent_ret: Window = 0;
    var children: ?[*]Window = null;
    var nchildren: c_uint = 0;
    if (XQueryTree(guard.display, parent, &root_ret, &parent_ret, &children, &nchildren) == 0) return 0;
    defer {
        if (children) |c| _ = XFree(c);
    }
    if (children == null or nchildren == 0) return 0;

    // Breadth-first over immediate children then their descendants.
    var queue: std.ArrayList(Window) = .empty;
    try queue.appendSlice(gpa, children.?[0..nchildren]);
    var index: usize = 0;
    while (index < queue.items.len) : (index += 1) {
        const window = queue.items[index];
        const class = try windowClassName(guard.display, window, gpa);
        const title = try windowTitle(guard.display, window, gpa);
        const class_ok = class_substr.len == 0 or host.containsIgnoreCase(class, class_substr);
        const title_ok = title_substr.len == 0 or host.containsIgnoreCase(title, title_substr);
        if (class_ok and title_ok) return window;

        var child_root: Window = 0;
        var child_parent: Window = 0;
        var kids: ?[*]Window = null;
        var nkids: c_uint = 0;
        if (XQueryTree(guard.display, window, &child_root, &child_parent, &kids, &nkids) != 0) {
            defer {
                if (kids) |k| _ = XFree(k);
            }
            if (kids) |k| try queue.appendSlice(gpa, k[0..nkids]);
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Clipboard (CLIPBOARD selection, UTF8_STRING)
// ---------------------------------------------------------------------------

fn atom(display: *Display, name: [*:0]const u8) Atom {
    return XInternAtom(display, name, False);
}

pub fn clipboardGetText() Error![]u8 {
    const guard = try DisplayGuard.open();
    defer guard.close();
    const display = guard.display;
    const clipboard = atom(display, "CLIPBOARD");
    const utf8 = atom(display, "UTF8_STRING");
    const prop = atom(display, "REMEDY_CLIPBOARD");
    const root = XDefaultRootWindow(display);
    const requestor = XCreateSimpleWindow(display, root, 0, 0, 1, 1, 0, 0, 0);
    defer _ = XDestroyWindow(display, requestor);
    _ = XSelectInput(display, requestor, PropertyChangeMask);
    _ = XConvertSelection(display, clipboard, utf8, prop, requestor, CurrentTime);
    guard.flush();

    const deadline = monoMillis() + 500;
    while (monoMillis() < deadline) {
        while (XPending(display) > 0) {
            var event: XEvent = undefined;
            _ = XNextEvent(display, &event);
            const type_id: c_int = @bitCast(@as(i32, @truncate(event.pad[0])));
            // SelectionNotify
            if (type_id == SelectionNotify) {
                var actual_type: Atom = 0;
                var actual_format: c_int = 0;
                var nitems: c_ulong = 0;
                var bytes_after: c_ulong = 0;
                var data: ?[*]u8 = null;
                if (XGetWindowProperty(display, requestor, prop, 0, 0x100000, False, 0, &actual_type, &actual_format, &nitems, &bytes_after, &data) == Success and data != null) {
                    defer _ = XFree(data);
                    _ = XDeleteProperty(display, requestor, prop);
                    if (nitems == 0) return allocator.dupe(u8, "") catch return error.OutOfMemory;
                    return allocator.dupe(u8, data.?[0..nitems]) catch return error.OutOfMemory;
                }
                return allocator.dupe(u8, "") catch return error.OutOfMemory;
            }
        }
        sleepMs(10);
    }
    return allocator.dupe(u8, "") catch return error.OutOfMemory;
}

pub fn clipboardSetText(utf8: []const u8) Error!void {
    const guard = try DisplayGuard.open();
    defer guard.close();
    const display = guard.display;
    const clipboard = atom(display, "CLIPBOARD");
    const primary = atom(display, "PRIMARY");
    const targets = atom(display, "TARGETS");
    const utf8_atom = atom(display, "UTF8_STRING");
    const root = XDefaultRootWindow(display);
    const owner = XCreateSimpleWindow(display, root, -10, -10, 1, 1, 0, 0, 0);
    // Keep the owned buffer for a short window so pastes that race us still work.
    _ = XSetSelectionOwner(display, clipboard, owner, CurrentTime);
    _ = XSetSelectionOwner(display, primary, owner, CurrentTime);
    if (XGetSelectionOwner(display, clipboard) != owner) {
        _ = XDestroyWindow(display, owner);
        return failMsg(30);
    }
    _ = XChangeProperty(display, owner, utf8_atom, utf8_atom, 8, PropModeReplace, utf8.ptr, @intCast(utf8.len));
    _ = XSelectInput(display, owner, StructureNotifyMask);
    guard.flush();

    const deadline = monoMillis() + 250;
    while (monoMillis() < deadline) {
        while (XPending(display) > 0) {
            var event: XEvent = undefined;
            _ = XNextEvent(display, &event);
            const type_id: *const c_int = @ptrCast(@alignCast(&event));
            if (type_id.* == SelectionRequest) {
                // Layout of XSelectionRequestEvent
                const Req = extern struct {
                    type: c_int,
                    serial: c_ulong,
                    send_event: Bool,
                    display: ?*Display,
                    owner: Window,
                    requestor: Window,
                    selection: Atom,
                    target: Atom,
                    property: Atom,
                    time: Time,
                };
                const req: *const Req = @ptrCast(@alignCast(&event));
                const property = if (req.property != 0) req.property else req.target;
                if (req.target == targets) {
                    var atoms = [_]Atom{ utf8_atom, XA_STRING, targets };
                    _ = XChangeProperty(display, req.requestor, property, XA_ATOM, 32, PropModeReplace, @ptrCast(&atoms), 3);
                } else if (req.target == utf8_atom or req.target == XA_STRING) {
                    _ = XChangeProperty(display, req.requestor, property, req.target, 8, PropModeReplace, utf8.ptr, @intCast(utf8.len));
                }
                const Notify = extern struct {
                    type: c_int,
                    serial: c_ulong,
                    send_event: Bool,
                    display: ?*Display,
                    requestor: Window,
                    selection: Atom,
                    target: Atom,
                    property: Atom,
                    time: Time,
                };
                var notify = std.mem.zeroes(Notify);
                notify.type = SelectionNotify;
                notify.display = display;
                notify.requestor = req.requestor;
                notify.selection = req.selection;
                notify.target = req.target;
                notify.property = property;
                notify.time = req.time;
                _ = XSendEvent(display, req.requestor, False, 0, @ptrCast(&notify));
                guard.flush();
            }
        }
        sleepMs(10);
    }
    // Leave the owner window alive briefly by destroying after the poll window;
    // many paste targets already retrieved the property by now.
    _ = XDestroyWindow(display, owner);
    guard.flush();
}

// ---------------------------------------------------------------------------
// Processes — hidden spawn in its own process group (job-object analogue)
// ---------------------------------------------------------------------------

const linux = std.os.linux;

const Process = struct {
    pid: linux.pid_t,
    pgid: linux.pid_t,
    exited: bool = false,
    exit_code: u32 = 0,
};

pub const Spawned = struct { pid: u32, handle: u64 };
pub const WaitOutcome = struct { exited: bool, exit_code: u32 };

fn processFrom(handle: u64) Error!*Process {
    if (handle == 0) return error.InvalidArgument;
    return @ptrFromInt(@as(usize, @intCast(handle)));
}

fn failErrnoCode(err: linux.E) Error {
    host.setOsError(@intCast(@intFromEnum(err)));
    return error.OperationFailed;
}

fn reapNonBlocking(process: *Process) Error!bool {
    if (process.exited) return true;
    var status: u32 = 0;
    while (true) {
        const rc = linux.waitpid(process.pid, &status, linux.W.NOHANG);
        switch (linux.errno(rc)) {
            .SUCCESS => {
                if (rc == 0) return false;
                process.exited = true;
                process.exit_code = if (linux.W.IFEXITED(status))
                    linux.W.EXITSTATUS(status)
                else if (linux.W.IFSIGNALED(status))
                    @as(u32, @intCast(@intFromEnum(linux.W.TERMSIG(status)))) | 0x80
                else
                    1;
                return true;
            },
            .INTR => continue,
            .CHILD => {
                // Already reaped elsewhere — treat as exited unknown.
                process.exited = true;
                process.exit_code = 1;
                return true;
            },
            else => |e| return failErrnoCode(e),
        }
    }
}

fn killProcessGroup(pgid: linux.pid_t) void {
    if (pgid <= 1) return;
    _ = linux.kill(-pgid, .KILL);
}

/// init_single_threaded uses Allocator.failing — process spawn /proc walks OOM.
fn threadedIo() std.Io.Threaded {
    const parent_env: std.process.Environ = .{ .block = .empty };
    return std.Io.Threaded.init(allocator, .{ .environ = parent_env });
}

fn readProcStatPpid(io: std.Io, pid: u32) ?u32 {
    var path_buf: [64]u8 = undefined;
    const path = std.fmt.bufPrint(&path_buf, "/proc/{d}/stat", .{pid}) catch return null;
    var file = std.Io.Dir.openFileAbsolute(io, path, .{}) catch return null;
    defer file.close(io);
    var data_buf: [512]u8 = undefined;
    const n = file.readPositionalAll(io, &data_buf, 0) catch return null;
    const data = data_buf[0..n];
    // /proc/pid/stat: pid (comm) state ppid ...
    const rparen = std.mem.lastIndexOfScalar(u8, data, ')') orelse return null;
    if (rparen + 1 >= data.len or data[rparen + 1] != ' ') return null;
    var rest = data[rparen + 2 ..];
    const sp = std.mem.indexOfScalar(u8, rest, ' ') orelse return null;
    rest = rest[sp + 1 ..];
    const end = std.mem.indexOfScalar(u8, rest, ' ') orelse rest.len;
    return std.fmt.parseInt(u32, rest[0..end], 10) catch null;
}

fn collectDescendants(gpa: std.mem.Allocator, io: std.Io, root: u32) Error![]u32 {
    var victims: std.ArrayList(u32) = .empty;
    errdefer victims.deinit(gpa);
    try victims.append(gpa, root);

    var proc_dir = std.Io.Dir.openDirAbsolute(io, "/proc", .{ .iterate = true }) catch return error.OperationFailed;
    defer proc_dir.close(io);

    var index: usize = 0;
    while (index < victims.items.len) : (index += 1) {
        const parent = victims.items[index];
        var it = proc_dir.iterate();
        while (it.next(io) catch null) |entry| {
            if (entry.kind != .directory) continue;
            const child_pid = std.fmt.parseInt(u32, entry.name, 10) catch continue;
            if (child_pid == parent) continue;
            if (std.mem.indexOfScalar(u32, victims.items, child_pid) != null) continue;
            const ppid = readProcStatPpid(io, child_pid) orelse continue;
            if (ppid == parent) {
                victims.append(gpa, child_pid) catch return error.OutOfMemory;
            }
        }
    }
    return victims.toOwnedSlice(gpa) catch return error.OutOfMemory;
}

/// Kill `pid` and descendants (process-group SIGKILL + /proc walk), deepest first.
pub fn killTree(pid: u32) Error!void {
    if (pid == 0 or pid == 1) return error.InvalidArgument;
    var threaded = threadedIo();
    defer threaded.deinit();
    const io = threaded.io();
    const victims = try collectDescendants(allocator, io, pid);
    defer allocator.free(victims);
    // Prefer group kill when pid is the group leader (spawnHidden uses pgid=0).
    killProcessGroup(@intCast(pid));
    var index = victims.len;
    while (index > 0) {
        index -= 1;
        _ = linux.kill(@intCast(victims[index]), .KILL);
    }
}

/// Hidden spawn: stdin/stdout/stderr → /dev/null, own process group (pgid=0).
/// Handle close / kill-tree ends the whole group (Windows job-object parity).
pub fn spawnHidden(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!Spawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    if (!std.fs.path.isAbsolute(argv[0])) return error.InvalidArgument;

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
        .pgid = 0, // own process group leader (setpgid(0, 0))
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
    // Own waitpid/kill-tree ourselves (Child would block forever on wait).
    child.id = null;

    const process = allocator.create(Process) catch {
        killProcessGroup(pid);
        _ = linux.kill(pid, .KILL);
        var status: u32 = 0;
        _ = linux.waitpid(pid, &status, 0);
        return error.OutOfMemory;
    };
    process.* = .{ .pid = pid, .pgid = pid };
    return .{ .pid = @intCast(pid), .handle = @intFromPtr(process) };
}

pub const PipedSpawned = struct {
    pid: u32,
    handle: u64, // Process* — wait/close via processWait/processClose
    stdin_write: u64,
    stdout_read: u64,
    stderr_read: u64,
};

/// Interactive spawn with separate stdin/stdout/stderr pipes. Parent fds are
/// transferred to the caller (not closed by `processClose`). Own process group.
pub fn spawnPiped3(argv_json: []const u8, cwd: []const u8, env_json: []const u8) Error!PipedSpawned {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const argv = try host.parseArgv(gpa, argv_json);
    if (!std.fs.path.isAbsolute(argv[0])) return error.InvalidArgument;

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
        .stdin = .pipe,
        .stdout = .pipe,
        .stderr = .pipe,
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
    const stdin_file = child.stdin orelse {
        host.setOsError(1);
        return error.OperationFailed;
    };
    const stdout_file = child.stdout orelse {
        host.setOsError(1);
        return error.OperationFailed;
    };
    const stderr_file = child.stderr orelse {
        host.setOsError(1);
        return error.OperationFailed;
    };
    // Transfer pipe ownership to the caller; we own waitpid/kill-tree.
    child.stdin = null;
    child.stdout = null;
    child.stderr = null;
    child.id = null;

    const process = allocator.create(Process) catch {
        _ = linux.close(stdin_file.handle);
        _ = linux.close(stdout_file.handle);
        _ = linux.close(stderr_file.handle);
        killProcessGroup(pid);
        _ = linux.kill(pid, .KILL);
        var status: u32 = 0;
        _ = linux.waitpid(pid, &status, 0);
        return error.OutOfMemory;
    };
    process.* = .{ .pid = pid, .pgid = pid };
    return .{
        .pid = @intCast(pid),
        .handle = @intFromPtr(process),
        .stdin_write = @as(u64, @intCast(stdin_file.handle)),
        .stdout_read = @as(u64, @intCast(stdout_file.handle)),
        .stderr_read = @as(u64, @intCast(stderr_file.handle)),
    };
}

pub fn processWait(handle: u64, timeout_ms: u32) Error!WaitOutcome {
    const process = try processFrom(handle);
    if (try reapNonBlocking(process)) {
        return .{ .exited = true, .exit_code = process.exit_code };
    }
    if (timeout_ms == 0) {
        return .{ .exited = false, .exit_code = 0 };
    }
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
        killProcessGroup(process.pgid);
        _ = linux.kill(process.pid, .KILL);
        // Block briefly to reap; ignore leftover races.
        var status: u32 = 0;
        var spins: u32 = 0;
        while (spins < 200) : (spins += 1) {
            const rc = linux.waitpid(process.pid, &status, linux.W.NOHANG);
            switch (linux.errno(rc)) {
                .SUCCESS => {
                    if (rc != 0) break;
                },
                .CHILD => break,
                .INTR => continue,
                else => break,
            }
            sleepMs(10);
        }
    }
    allocator.destroy(process);
}

test "hidden spawn and kill-tree reaps a sleeping child" {
    const argv = "[\"/bin/sleep\",\"30\"]";
    const spawned = try spawnHidden(argv, "", "");
    defer processClose(spawned.handle) catch {};
    try std.testing.expect(spawned.pid > 1);
    const still = try processWait(spawned.handle, 50);
    try std.testing.expect(!still.exited);
    try killTree(spawned.pid);
    const done = try processWait(spawned.handle, 2000);
    try std.testing.expect(done.exited);
}

test "spawnHidden rejects empty argv" {
    try std.testing.expectError(error.InvalidArgument, spawnHidden("[]", "", ""));
}

// ---------------------------------------------------------------------------
// AT-SPI (ABI 3)
// ---------------------------------------------------------------------------

pub fn a11ySnapshotJson(limit: u32) Error![]u8 {
    return atspi.snapshotJson(allocator, limit);
}
