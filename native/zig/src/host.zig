//! Host primitives behind the versioned C ABI: DPI, monitors, capture, PNG
//! encoding, input injection, window control, clipboard and hidden process
//! control with kill-tree. ABI 3 adds accessibility snapshots (AT-SPI on
//! Linux; Windows UIA shares the same export names).
//!
//! This file owns the portable pieces (status/error model, allocation, JSON,
//! PNG, UTF-8/UTF-16, argv/env parsing, command lines) and every export. The
//! Windows implementation lives in `host_windows.zig`; Linux (X11/XTest +
//! AT-SPI) in `host_linux.zig`. Other OS builds export the same symbols as
//! `unsupported` so the library still builds and tests.
//!
//! Memory contract: every buffer handed to the caller is allocated by this
//! module and must be released with `remedy_core_free(ptr, len)`. Strings
//! crossing the ABI are UTF-8 (WTF-8 for lone surrogates coming from Win32).

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");

pub const is_windows = builtin.os.tag == .windows;
pub const is_linux = builtin.os.tag == .linux;
const windows = if (is_windows) @import("host_windows.zig") else struct {};
const linux = if (is_linux) @import("host_linux.zig") else struct {};

pub const Status = root.Status;

pub const Error = error{
    Unsupported,
    InvalidArgument,
    OperationFailed,
    OutOfMemory,
};

/// One allocator for everything that crosses the ABI, so `remedy_core_free`
/// can release any buffer produced by any host export.
pub const allocator: std.mem.Allocator = std.heap.smp_allocator;

threadlocal var last_os_error: u32 = 0;

/// Record the OS error behind a failure so the caller can report it.
pub fn setOsError(code: u32) void {
    last_os_error = code;
}

pub fn statusOf(err: Error) i32 {
    return @intFromEnum(switch (err) {
        error.Unsupported => Status.unsupported,
        error.InvalidArgument => Status.invalid_argument,
        error.OperationFailed => Status.operation_failed,
        error.OutOfMemory => Status.operation_failed,
    });
}

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

fn statusOfVoid(result: Error!void) i32 {
    result catch |err| return statusOf(err);
    return ok_status;
}

fn deliverBytes(result: Error![]u8, out_ptr: ?*?[*]u8, out_len: ?*usize) i32 {
    const ptr_slot = out_ptr orelse return invalid_status;
    const len_slot = out_len orelse return invalid_status;
    const bytes = result catch |err| {
        ptr_slot.* = null;
        len_slot.* = 0;
        return statusOf(err);
    };
    ptr_slot.* = bytes.ptr;
    len_slot.* = bytes.len;
    return ok_status;
}

// ---------------------------------------------------------------------------
// Portable data model
// ---------------------------------------------------------------------------

pub const Rect = struct {
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
};

pub const WindowEntry = struct {
    hwnd: u64,
    title: []const u8,
    class: []const u8,
    pid: u32,
    bounds: Rect,
    width: i32,
    height: i32,
    visible: bool,
    minimized: bool,
};

pub const MonitorEntry = struct {
    index: u32,
    left: i32,
    top: i32,
    right: i32,
    bottom: i32,
    width: i32,
    height: i32,
    primary: bool,
    scale: f64,
};

pub const Pixels = struct {
    bytes: []u8,
    width: i32,
    height: i32,
    stride: usize,
    left: i32,
    top: i32,
};

pub const MouseButton = enum(u32) {
    left = 0,
    right = 1,
    middle = 2,

    pub fn fromRaw(raw: u32) Error!MouseButton {
        return std.enums.fromInt(MouseButton, raw) orelse error.InvalidArgument;
    }
};

pub const WindowAction = enum(u32) {
    minimize = 0,
    maximize = 1,
    restore = 2,
    close = 3,
    move_resize = 4,

    pub fn fromRaw(raw: u32) Error!WindowAction {
        return std.enums.fromInt(WindowAction, raw) orelse error.InvalidArgument;
    }
};

pub fn jsonAlloc(value: anytype) Error![]u8 {
    return std.json.Stringify.valueAlloc(allocator, value, .{}) catch return error.OutOfMemory;
}

pub fn windowsJson(gpa: std.mem.Allocator, entries: []const WindowEntry) Error![]u8 {
    return std.json.Stringify.valueAlloc(gpa, entries, .{}) catch return error.OutOfMemory;
}

pub fn monitorsJson(gpa: std.mem.Allocator, entries: []const MonitorEntry) Error![]u8 {
    return std.json.Stringify.valueAlloc(gpa, entries, .{}) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// Text
// ---------------------------------------------------------------------------

/// UTF-8 (or WTF-8) to NUL-terminated UTF-16LE. Lone surrogates are preserved
/// so a title read from Win32 can round-trip untouched.
pub fn utf8ToUtf16Z(gpa: std.mem.Allocator, utf8: []const u8) Error![:0]u16 {
    return std.unicode.wtf8ToWtf16LeAllocZ(gpa, utf8) catch |err| switch (err) {
        error.InvalidWtf8 => error.InvalidArgument,
        error.OutOfMemory => error.OutOfMemory,
    };
}

/// UTF-16LE to WTF-8 (valid UTF-8 whenever the input has no lone surrogates).
pub fn utf16ToUtf8(gpa: std.mem.Allocator, utf16: []const u16) Error![]u8 {
    return std.unicode.wtf16LeToWtf8Alloc(gpa, utf16) catch return error.OutOfMemory;
}

/// Iterate UTF-8 as UTF-16 code units, one code point at a time.
pub const Utf16Units = struct {
    high: u16,
    low: u16 = 0,

    pub fn count(self: Utf16Units) usize {
        return if (self.low == 0) 1 else 2;
    }
};

pub fn codepointToUtf16(codepoint: u21) Utf16Units {
    if (codepoint < 0x10000) return .{ .high = @intCast(codepoint) };
    const offset = codepoint - 0x10000;
    return .{
        .high = @intCast(0xD800 + (offset >> 10)),
        .low = @intCast(0xDC00 + (offset & 0x3FF)),
    };
}

/// Cut a UTF-8 string after at most `max_codepoints` code points without
/// splitting a sequence. Invalid input is cut at the first bad byte.
pub fn truncateCodepoints(utf8: []const u8, max_codepoints: usize) []const u8 {
    var index: usize = 0;
    var seen: usize = 0;
    while (index < utf8.len and seen < max_codepoints) {
        const len = std.unicode.utf8ByteSequenceLength(utf8[index]) catch return utf8[0..index];
        if (index + len > utf8.len) return utf8[0..index];
        index += len;
        seen += 1;
    }
    return utf8[0..index];
}

/// ASCII case-insensitive substring test (non-ASCII bytes compare exactly).
pub fn containsIgnoreCase(haystack: []const u8, needle: []const u8) bool {
    if (needle.len == 0) return true;
    if (needle.len > haystack.len) return false;
    var start: usize = 0;
    while (start + needle.len <= haystack.len) : (start += 1) {
        if (std.ascii.eqlIgnoreCase(haystack[start .. start + needle.len], needle)) return true;
    }
    return false;
}

pub fn trimWhitespace(text: []const u8) []const u8 {
    return std.mem.trim(u8, text, " \t\r\n\x0b\x0c");
}

// ---------------------------------------------------------------------------
// Coordinates
// ---------------------------------------------------------------------------

pub const VirtualScreen = struct {
    left: i32,
    top: i32,
    width: i32,
    height: i32,
};

pub const AbsPoint = struct { x: i32, y: i32 };

/// Map virtual-screen pixels onto the 0..65535 range SendInput expects with
/// MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK. Truncates toward zero
/// exactly as the previous Python `int((x - left) * 65535 / (width - 1))`.
pub fn absCoords(x: i32, y: i32, screen: VirtualScreen) AbsPoint {
    const width: i64 = @max(@as(i64, screen.width) - 1, 1);
    const height: i64 = @max(@as(i64, screen.height) - 1, 1);
    const dx: i64 = @as(i64, x) - screen.left;
    const dy: i64 = @as(i64, y) - screen.top;
    return .{
        .x = @intCast(@divTrunc(dx * 65535, width)),
        .y = @intCast(@divTrunc(dy * 65535, height)),
    };
}

// ---------------------------------------------------------------------------
// PNG
// ---------------------------------------------------------------------------

const png_signature = [_]u8{ 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n' };

/// Encode BGR (3 bytes per pixel) or BGRA (4 bytes per pixel, alpha ignored)
/// rows into an 8-bit RGB PNG. `stride` is the byte distance between rows.
pub fn encodePng(
    gpa: std.mem.Allocator,
    pixels: []const u8,
    width: u32,
    height: u32,
    stride: usize,
    bytes_per_pixel: u32,
) Error![]u8 {
    if (width == 0 or height == 0) return error.InvalidArgument;
    if (bytes_per_pixel != 3 and bytes_per_pixel != 4) return error.InvalidArgument;
    const row_bytes = @as(usize, width) * bytes_per_pixel;
    if (stride < row_bytes) return error.InvalidArgument;
    const needed = std.math.add(usize, std.math.mul(usize, stride, height - 1) catch return error.InvalidArgument, row_bytes) catch return error.InvalidArgument;
    if (pixels.len < needed) return error.InvalidArgument;

    const scan_row = 1 + @as(usize, width) * 3;
    const scanlines = gpa.alloc(u8, scan_row * height) catch return error.OutOfMemory;
    defer gpa.free(scanlines);
    var y: usize = 0;
    while (y < height) : (y += 1) {
        const src = pixels[y * stride ..][0..row_bytes];
        const dst = scanlines[y * scan_row ..][0..scan_row];
        dst[0] = 0; // filter: none
        var x: usize = 0;
        while (x < width) : (x += 1) {
            const p = src[x * bytes_per_pixel ..];
            dst[1 + x * 3] = p[2];
            dst[2 + x * 3] = p[1];
            dst[3 + x * 3] = p[0];
        }
    }

    const window = gpa.alloc(u8, std.compress.flate.max_window_len) catch return error.OutOfMemory;
    defer gpa.free(window);
    var compressed: std.Io.Writer.Allocating = std.Io.Writer.Allocating.initCapacity(gpa, scanlines.len / 4 + 64) catch return error.OutOfMemory;
    defer compressed.deinit();
    var deflate = std.compress.flate.Compress.init(&compressed.writer, window, .zlib, .fastest) catch return error.OutOfMemory;
    deflate.writer.writeAll(scanlines) catch return error.OutOfMemory;
    deflate.finish() catch return error.OutOfMemory;
    const idat = compressed.writer.buffered();

    var out: std.Io.Writer.Allocating = std.Io.Writer.Allocating.initCapacity(gpa, idat.len + 64) catch return error.OutOfMemory;
    errdefer out.deinit();
    const w = &out.writer;
    w.writeAll(&png_signature) catch return error.OutOfMemory;
    var ihdr: [13]u8 = undefined;
    std.mem.writeInt(u32, ihdr[0..4], width, .big);
    std.mem.writeInt(u32, ihdr[4..8], height, .big);
    ihdr[8] = 8; // bit depth
    ihdr[9] = 2; // colour type: truecolour
    ihdr[10] = 0; // compression
    ihdr[11] = 0; // filter
    ihdr[12] = 0; // interlace
    try writeChunk(w, "IHDR", &ihdr);
    try writeChunk(w, "IDAT", idat);
    try writeChunk(w, "IEND", "");
    return out.toOwnedSlice() catch return error.OutOfMemory;
}

fn writeChunk(w: *std.Io.Writer, tag: *const [4]u8, data: []const u8) Error!void {
    var len_bytes: [4]u8 = undefined;
    std.mem.writeInt(u32, &len_bytes, @intCast(data.len), .big);
    var crc = std.hash.crc.Crc32.init();
    crc.update(tag);
    crc.update(data);
    var crc_bytes: [4]u8 = undefined;
    std.mem.writeInt(u32, &crc_bytes, crc.final(), .big);
    w.writeAll(&len_bytes) catch return error.OutOfMemory;
    w.writeAll(tag) catch return error.OutOfMemory;
    w.writeAll(data) catch return error.OutOfMemory;
    w.writeAll(&crc_bytes) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// Process arguments
// ---------------------------------------------------------------------------

pub const max_arguments = 4096;

/// Parse a JSON array of strings. The result lives in `arena`.
pub fn parseArgv(arena: std.mem.Allocator, json: []const u8) Error![]const []const u8 {
    const parsed = std.json.parseFromSliceLeaky([]const []const u8, arena, json, .{}) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return error.InvalidArgument,
    };
    if (parsed.len == 0 or parsed.len > max_arguments) return error.InvalidArgument;
    if (parsed[0].len == 0) return error.InvalidArgument;
    for (parsed) |argument| {
        if (std.mem.indexOfScalar(u8, argument, 0) != null) return error.InvalidArgument;
    }
    return parsed;
}

pub const EnvPair = struct { key: []const u8, value: []const u8 };

/// Parse a JSON object of string values into sorted `KEY=value` pairs.
/// An empty input means "inherit the parent environment" and yields null.
pub fn parseEnv(arena: std.mem.Allocator, json: []const u8) Error!?[]EnvPair {
    if (trimWhitespace(json).len == 0) return null;
    const value = std.json.parseFromSliceLeaky(std.json.Value, arena, json, .{}) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return error.InvalidArgument,
    };
    const object = switch (value) {
        .object => |object| object,
        .null => return null,
        else => return error.InvalidArgument,
    };
    var pairs = arena.alloc(EnvPair, object.count()) catch return error.OutOfMemory;
    var index: usize = 0;
    var iterator = object.iterator();
    while (iterator.next()) |entry| : (index += 1) {
        const text = switch (entry.value_ptr.*) {
            .string => |text| text,
            else => return error.InvalidArgument,
        };
        const key = entry.key_ptr.*;
        if (key.len == 0 or std.mem.indexOfScalar(u8, key, '=') != null) return error.InvalidArgument;
        if (std.mem.indexOfScalar(u8, key, 0) != null or std.mem.indexOfScalar(u8, text, 0) != null) {
            return error.InvalidArgument;
        }
        pairs[index] = .{ .key = key, .value = text };
    }
    std.mem.sort(EnvPair, pairs, {}, envLessThan);
    return pairs;
}

fn envLessThan(_: void, a: EnvPair, b: EnvPair) bool {
    const n = @min(a.key.len, b.key.len);
    var i: usize = 0;
    while (i < n) : (i += 1) {
        const ca = std.ascii.toUpper(a.key[i]);
        const cb = std.ascii.toUpper(b.key[i]);
        if (ca != cb) return ca < cb;
    }
    return a.key.len < b.key.len;
}

/// Build a Windows environment block: `KEY=value\0...\0` in UTF-16LE.
pub fn envBlock(gpa: std.mem.Allocator, pairs: []const EnvPair) Error![]u16 {
    var text: std.ArrayList(u8) = .empty;
    defer text.deinit(gpa);
    for (pairs) |pair| {
        text.appendSlice(gpa, pair.key) catch return error.OutOfMemory;
        text.append(gpa, '=') catch return error.OutOfMemory;
        text.appendSlice(gpa, pair.value) catch return error.OutOfMemory;
        text.append(gpa, 0) catch return error.OutOfMemory;
    }
    text.append(gpa, 0) catch return error.OutOfMemory;
    if (pairs.len == 0) text.append(gpa, 0) catch return error.OutOfMemory;
    return std.unicode.wtf8ToWtf16LeAlloc(gpa, text.items) catch |err| switch (err) {
        error.InvalidWtf8 => error.InvalidArgument,
        error.OutOfMemory => error.OutOfMemory,
    };
}

/// Quote argv the way CommandLineToArgvW / the CRT parse it. The first
/// argument may not contain a double quote (it cannot be escaped there).
pub fn commandLine(gpa: std.mem.Allocator, argv: []const []const u8) Error![:0]u16 {
    if (argv.len == 0) return error.InvalidArgument;
    var buf: std.ArrayList(u8) = .empty;
    defer buf.deinit(gpa);

    const arg0 = argv[0];
    var needs_quotes = arg0.len == 0;
    for (arg0) |c| {
        if (c <= ' ') needs_quotes = true;
        if (c == '"') return error.InvalidArgument;
    }
    if (needs_quotes) buf.append(gpa, '"') catch return error.OutOfMemory;
    buf.appendSlice(gpa, arg0) catch return error.OutOfMemory;
    if (needs_quotes) buf.append(gpa, '"') catch return error.OutOfMemory;

    for (argv[1..]) |arg| {
        buf.append(gpa, ' ') catch return error.OutOfMemory;
        const quote = for (arg) |c| {
            if (c <= ' ' or c == '"') break true;
        } else arg.len == 0;
        if (!quote) {
            buf.appendSlice(gpa, arg) catch return error.OutOfMemory;
            continue;
        }
        buf.append(gpa, '"') catch return error.OutOfMemory;
        var backslashes: usize = 0;
        for (arg) |byte| {
            switch (byte) {
                '\\' => backslashes += 1,
                '"' => {
                    buf.appendNTimes(gpa, '\\', backslashes * 2 + 1) catch return error.OutOfMemory;
                    buf.append(gpa, '"') catch return error.OutOfMemory;
                    backslashes = 0;
                },
                else => {
                    buf.appendNTimes(gpa, '\\', backslashes) catch return error.OutOfMemory;
                    buf.append(gpa, byte) catch return error.OutOfMemory;
                    backslashes = 0;
                },
            }
        }
        buf.appendNTimes(gpa, '\\', backslashes * 2) catch return error.OutOfMemory;
        buf.append(gpa, '"') catch return error.OutOfMemory;
    }
    return utf8ToUtf16Z(gpa, buf.items);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

export fn remedy_core_free(ptr: ?[*]u8, len: usize) callconv(.c) void {
    const raw = ptr orelse return;
    if (len == 0) return;
    allocator.free(raw[0..len]);
}

export fn remedy_core_last_os_error() callconv(.c) u32 {
    return last_os_error;
}

export fn remedy_core_dpi_awareness_enable() callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.enableDpiAwareness());
    if (is_linux) return statusOfVoid(linux.enableDpiAwareness());
    return unsupported_status;
}

export fn remedy_core_virtual_screen_rect(
    out_left: ?*i32,
    out_top: ?*i32,
    out_width: ?*i32,
    out_height: ?*i32,
) callconv(.c) i32 {
    const left = out_left orelse return invalid_status;
    const top = out_top orelse return invalid_status;
    const width = out_width orelse return invalid_status;
    const height = out_height orelse return invalid_status;
    const screen = if (is_windows)
        windows.virtualScreen()
    else if (is_linux)
        linux.virtualScreen()
    else
        error.Unsupported;
    const value = screen catch |err| return statusOf(err);
    left.* = value.left;
    top.* = value.top;
    width.* = value.width;
    height.* = value.height;
    return ok_status;
}

export fn remedy_core_list_monitors(out_json: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    if (is_windows) return deliverBytes(windows.listMonitorsJson(), out_json, out_len);
    if (is_linux) return deliverBytes(linux.listMonitorsJson(), out_json, out_len);
    return unsupported_status;
}

fn deliverPixels(
    result: Error!Pixels,
    out_pixels: ?*?[*]u8,
    out_len: ?*usize,
    out_width: ?*i32,
    out_height: ?*i32,
    out_stride: ?*usize,
    out_left: ?*i32,
    out_top: ?*i32,
) i32 {
    const width = out_width orelse return invalid_status;
    const height = out_height orelse return invalid_status;
    const stride = out_stride orelse return invalid_status;
    const left = out_left orelse return invalid_status;
    const top = out_top orelse return invalid_status;
    const pixels = result catch |err| return deliverBytes(err, out_pixels, out_len);
    width.* = pixels.width;
    height.* = pixels.height;
    stride.* = pixels.stride;
    left.* = pixels.left;
    top.* = pixels.top;
    return deliverBytes(pixels.bytes, out_pixels, out_len);
}

export fn remedy_core_capture_virtual_screen(
    bytes_per_pixel: u32,
    out_pixels: ?*?[*]u8,
    out_len: ?*usize,
    out_width: ?*i32,
    out_height: ?*i32,
    out_stride: ?*usize,
    out_left: ?*i32,
    out_top: ?*i32,
) callconv(.c) i32 {
    const result = if (is_windows)
        windows.captureVirtualScreen(bytes_per_pixel)
    else if (is_linux)
        linux.captureVirtualScreen(bytes_per_pixel)
    else
        error.Unsupported;
    return deliverPixels(result, out_pixels, out_len, out_width, out_height, out_stride, out_left, out_top);
}

export fn remedy_core_capture_region(
    left: i32,
    top: i32,
    width: i32,
    height: i32,
    bytes_per_pixel: u32,
    out_pixels: ?*?[*]u8,
    out_len: ?*usize,
    out_stride: ?*usize,
) callconv(.c) i32 {
    const stride = out_stride orelse return invalid_status;
    const pixels = (if (is_windows)
        windows.captureRegion(left, top, width, height, bytes_per_pixel)
    else if (is_linux)
        linux.captureRegion(left, top, width, height, bytes_per_pixel)
    else
        error.Unsupported) catch |err| {
        return deliverBytes(err, out_pixels, out_len);
    };
    stride.* = pixels.stride;
    return deliverBytes(pixels.bytes, out_pixels, out_len);
}

export fn remedy_core_print_window(
    hwnd: u64,
    bytes_per_pixel: u32,
    out_pixels: ?*?[*]u8,
    out_len: ?*usize,
    out_width: ?*i32,
    out_height: ?*i32,
    out_stride: ?*usize,
    out_left: ?*i32,
    out_top: ?*i32,
) callconv(.c) i32 {
    const result = if (is_windows)
        windows.printWindow(hwnd, bytes_per_pixel)
    else if (is_linux)
        linux.printWindow(hwnd, bytes_per_pixel)
    else
        error.Unsupported;
    return deliverPixels(result, out_pixels, out_len, out_width, out_height, out_stride, out_left, out_top);
}

export fn remedy_core_encode_png(
    pixels: ?[*]const u8,
    pixels_len: usize,
    width: i32,
    height: i32,
    stride: usize,
    bytes_per_pixel: u32,
    out_png: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (width <= 0 or height <= 0) return deliverBytes(error.InvalidArgument, out_png, out_len);
    const png = encodePng(
        allocator,
        slice(pixels, pixels_len),
        @intCast(width),
        @intCast(height),
        stride,
        bytes_per_pixel,
    );
    return deliverBytes(png, out_png, out_len);
}

export fn remedy_core_mouse_move(x: i32, y: i32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.mouseMove(x, y));
    if (is_linux) return statusOfVoid(linux.mouseMove(x, y));
    return unsupported_status;
}

export fn remedy_core_mouse_click(x: i32, y: i32, button: u32, clicks: u32) callconv(.c) i32 {
    const which = MouseButton.fromRaw(button) catch |err| return statusOf(err);
    if (is_windows) return statusOfVoid(windows.mouseClick(x, y, which, clicks));
    if (is_linux) return statusOfVoid(linux.mouseClick(x, y, which, clicks));
    return unsupported_status;
}

export fn remedy_core_mouse_button(button: u32, pressed: u8) callconv(.c) i32 {
    const which = MouseButton.fromRaw(button) catch |err| return statusOf(err);
    if (is_windows) return statusOfVoid(windows.mouseButton(which, pressed != 0));
    if (is_linux) return statusOfVoid(linux.mouseButton(which, pressed != 0));
    return unsupported_status;
}

export fn remedy_core_mouse_drag(x1: i32, y1: i32, x2: i32, y2: i32, steps: u32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.mouseDrag(x1, y1, x2, y2, steps));
    if (is_linux) return statusOfVoid(linux.mouseDrag(x1, y1, x2, y2, steps));
    return unsupported_status;
}

export fn remedy_core_mouse_scroll(x: i32, y: i32, dx: i32, dy: i32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.mouseScroll(x, y, dx, dy));
    if (is_linux) return statusOfVoid(linux.mouseScroll(x, y, dx, dy));
    return unsupported_status;
}

export fn remedy_core_type_text(utf8: ?[*]const u8, len: usize, per_char_delay_ms: u32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.typeText(slice(utf8, len), per_char_delay_ms));
    if (is_linux) return statusOfVoid(linux.typeText(slice(utf8, len), per_char_delay_ms));
    return unsupported_status;
}

export fn remedy_core_key_combo(vks: ?[*]const u16, count: usize) callconv(.c) i32 {
    const raw = vks orelse return invalid_status;
    if (is_windows) return statusOfVoid(windows.keyCombo(raw[0..count]));
    if (is_linux) return statusOfVoid(linux.keyCombo(raw[0..count]));
    return unsupported_status;
}

export fn remedy_core_key_hold(vk: u16, hold_ms: u32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.keyHold(vk, hold_ms));
    if (is_linux) return statusOfVoid(linux.keyHold(vk, hold_ms));
    return unsupported_status;
}

export fn remedy_core_vk_key_scan(codepoint: u32, out_scan: ?*i32) callconv(.c) i32 {
    const output = out_scan orelse return invalid_status;
    const scan = if (is_windows)
        windows.vkKeyScan(codepoint)
    else if (is_linux)
        linux.vkKeyScan(codepoint)
    else
        error.Unsupported;
    output.* = scan catch |err| return statusOf(err);
    return ok_status;
}

export fn remedy_core_list_windows(limit: u32, out_json: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    if (is_windows) return deliverBytes(windows.listWindowsJson(limit), out_json, out_len);
    if (is_linux) return deliverBytes(linux.listWindowsJson(limit), out_json, out_len);
    return unsupported_status;
}

export fn remedy_core_window_class(hwnd: u64, out_utf8: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    if (is_windows) return deliverBytes(windows.windowClass(hwnd), out_utf8, out_len);
    if (is_linux) return deliverBytes(linux.windowClass(hwnd), out_utf8, out_len);
    return unsupported_status;
}

export fn remedy_core_window_rect(
    hwnd: u64,
    out_left: ?*i32,
    out_top: ?*i32,
    out_right: ?*i32,
    out_bottom: ?*i32,
) callconv(.c) i32 {
    const left = out_left orelse return invalid_status;
    const top = out_top orelse return invalid_status;
    const right = out_right orelse return invalid_status;
    const bottom = out_bottom orelse return invalid_status;
    const rect = if (is_windows)
        windows.windowRect(hwnd)
    else if (is_linux)
        linux.windowRect(hwnd)
    else
        error.Unsupported;
    const value = rect catch |err| return statusOf(err);
    left.* = value.left;
    top.* = value.top;
    right.* = value.right;
    bottom.* = value.bottom;
    return ok_status;
}

export fn remedy_core_foreground_window(
    out_hwnd: ?*u64,
    out_title: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    const hwnd_slot = out_hwnd orelse return invalid_status;
    const info = if (is_windows)
        windows.foregroundWindow()
    else if (is_linux)
        linux.foregroundWindow()
    else
        error.Unsupported;
    const value = info catch |err| return deliverBytes(err, out_title, out_len);
    hwnd_slot.* = value.hwnd;
    return deliverBytes(value.title, out_title, out_len);
}

export fn remedy_core_focus_window(hwnd: u64, out_focused: ?*u8) callconv(.c) i32 {
    const output = out_focused orelse return invalid_status;
    const focused = if (is_windows)
        windows.focusWindow(hwnd)
    else if (is_linux)
        linux.focusWindow(hwnd)
    else
        error.Unsupported;
    output.* = @intFromBool(focused catch |err| return statusOf(err));
    return ok_status;
}

export fn remedy_core_manage_window(
    hwnd: u64,
    action: u32,
    x: i32,
    y: i32,
    width: i32,
    height: i32,
) callconv(.c) i32 {
    const verb = WindowAction.fromRaw(action) catch |err| return statusOf(err);
    if (is_windows) return statusOfVoid(windows.manageWindow(hwnd, verb, x, y, width, height));
    if (is_linux) return statusOfVoid(linux.manageWindow(hwnd, verb, x, y, width, height));
    return unsupported_status;
}

export fn remedy_core_find_child_hwnd(
    parent: u64,
    class_substr: ?[*]const u8,
    class_len: usize,
    title_substr: ?[*]const u8,
    title_len: usize,
    out_hwnd: ?*u64,
) callconv(.c) i32 {
    const output = out_hwnd orelse return invalid_status;
    const found = if (is_windows)
        windows.findChildHwnd(parent, slice(class_substr, class_len), slice(title_substr, title_len))
    else if (is_linux)
        linux.findChildHwnd(parent, slice(class_substr, class_len), slice(title_substr, title_len))
    else
        error.Unsupported;
    output.* = found catch |err| return statusOf(err);
    return ok_status;
}

export fn remedy_core_clipboard_get_text(out_utf8: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    if (is_windows) return deliverBytes(windows.clipboardGetText(), out_utf8, out_len);
    if (is_linux) return deliverBytes(linux.clipboardGetText(), out_utf8, out_len);
    return unsupported_status;
}

export fn remedy_core_clipboard_set_text(utf8: ?[*]const u8, len: usize) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.clipboardSetText(slice(utf8, len)));
    if (is_linux) return statusOfVoid(linux.clipboardSetText(slice(utf8, len)));
    return unsupported_status;
}

export fn remedy_core_process_spawn_hidden(
    argv_json: ?[*]const u8,
    argv_len: usize,
    cwd: ?[*]const u8,
    cwd_len: usize,
    env_json: ?[*]const u8,
    env_len: usize,
    out_pid: ?*u32,
    out_handle: ?*u64,
) callconv(.c) i32 {
    const pid_slot = out_pid orelse return invalid_status;
    const handle_slot = out_handle orelse return invalid_status;
    pid_slot.* = 0;
    handle_slot.* = 0;
    const spawned = if (is_windows)
        windows.spawnHidden(slice(argv_json, argv_len), slice(cwd, cwd_len), slice(env_json, env_len))
    else if (is_linux)
        linux.spawnHidden(slice(argv_json, argv_len), slice(cwd, cwd_len), slice(env_json, env_len))
    else
        error.Unsupported;
    const result = spawned catch |err| return statusOf(err);
    pid_slot.* = result.pid;
    handle_slot.* = result.handle;
    return ok_status;
}

export fn remedy_core_process_wait(
    handle: u64,
    timeout_ms: u32,
    out_exited: ?*u8,
    out_exit_code: ?*u32,
) callconv(.c) i32 {
    const exited_slot = out_exited orelse return invalid_status;
    const code_slot = out_exit_code orelse return invalid_status;
    const outcome = if (is_windows)
        windows.processWait(handle, timeout_ms)
    else if (is_linux)
        linux.processWait(handle, timeout_ms)
    else
        error.Unsupported;
    const result = outcome catch |err| return statusOf(err);
    exited_slot.* = @intFromBool(result.exited);
    code_slot.* = result.exit_code;
    return ok_status;
}

export fn remedy_core_process_kill_tree(pid: u32) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.killTree(pid));
    if (is_linux) return statusOfVoid(linux.killTree(pid));
    return unsupported_status;
}

export fn remedy_core_process_close(handle: u64) callconv(.c) i32 {
    if (is_windows) return statusOfVoid(windows.processClose(handle));
    if (is_linux) return statusOfVoid(linux.processClose(handle));
    return unsupported_status;
}

/// ABI 3 accessibility snapshot. Linux: AT-SPI clickables. Windows: UIA
/// (filled by the UIA agent); until then returns unsupported on Windows.
export fn remedy_core_a11y_snapshot(limit: u32, out_json: ?*?[*]u8, out_len: ?*usize) callconv(.c) i32 {
    if (is_linux) return deliverBytes(linux.a11ySnapshotJson(limit), out_json, out_len);
    return unsupported_status;
}

// ---------------------------------------------------------------------------
// Tests (portable)
// ---------------------------------------------------------------------------

fn inflateIdat(gpa: std.mem.Allocator, png: []const u8) ![]u8 {
    try std.testing.expectEqualSlices(u8, &png_signature, png[0..8]);
    var offset: usize = 8;
    var idat: std.ArrayList(u8) = .empty;
    defer idat.deinit(gpa);
    var saw_iend = false;
    while (offset + 12 <= png.len) {
        const len = std.mem.readInt(u32, png[offset..][0..4], .big);
        const tag = png[offset + 4 ..][0..4];
        const data = png[offset + 8 ..][0..len];
        const crc = std.mem.readInt(u32, png[offset + 8 + len ..][0..4], .big);
        var hasher = std.hash.crc.Crc32.init();
        hasher.update(tag);
        hasher.update(data);
        try std.testing.expectEqual(hasher.final(), crc);
        if (std.mem.eql(u8, tag, "IDAT")) try idat.appendSlice(gpa, data);
        if (std.mem.eql(u8, tag, "IEND")) saw_iend = true;
        offset += 12 + len;
    }
    try std.testing.expect(saw_iend);
    try std.testing.expectEqual(png.len, offset);
    var reader: std.Io.Reader = .fixed(idat.items);
    var window: [std.compress.flate.max_window_len]u8 = undefined;
    var inflate: std.compress.flate.Decompress = .init(&reader, .zlib, &window);
    return inflate.reader.allocRemaining(gpa, .unlimited);
}

test "png encoder round-trips BGR and BGRA rows through zlib" {
    const gpa = std.testing.allocator;
    const width: u32 = 5;
    const height: u32 = 3;
    // BGR with a padded stride, like a 24-bit DIB.
    const stride24 = (width * 3 + 3) & ~@as(usize, 3);
    var bgr: [stride24 * height]u8 = undefined;
    for (&bgr, 0..) |*byte, i| byte.* = @intCast(i % 251);
    const png24 = try encodePng(gpa, &bgr, width, height, stride24, 3);
    defer gpa.free(png24);
    try std.testing.expectEqual(@as(u32, width), std.mem.readInt(u32, png24[16..20], .big));
    try std.testing.expectEqual(@as(u32, height), std.mem.readInt(u32, png24[20..24], .big));
    const raw24 = try inflateIdat(gpa, png24);
    defer gpa.free(raw24);
    try std.testing.expectEqual(@as(usize, height * (1 + width * 3)), raw24.len);
    var y: usize = 0;
    while (y < height) : (y += 1) {
        const row = raw24[y * (1 + width * 3) ..][0 .. 1 + width * 3];
        try std.testing.expectEqual(@as(u8, 0), row[0]);
        var x: usize = 0;
        while (x < width) : (x += 1) {
            const src = bgr[y * stride24 + x * 3 ..][0..3];
            try std.testing.expectEqual(src[2], row[1 + x * 3]);
            try std.testing.expectEqual(src[1], row[2 + x * 3]);
            try std.testing.expectEqual(src[0], row[3 + x * 3]);
        }
    }
    // BGRA: alpha is dropped, colour order swapped.
    const bgra = [_]u8{ 1, 2, 3, 255, 4, 5, 6, 0 };
    const png32 = try encodePng(gpa, &bgra, 2, 1, 8, 4);
    defer gpa.free(png32);
    const raw32 = try inflateIdat(gpa, png32);
    defer gpa.free(raw32);
    try std.testing.expectEqualSlices(u8, &.{ 0, 3, 2, 1, 6, 5, 4 }, raw32);
}

test "png encoder rejects impossible geometry" {
    const gpa = std.testing.allocator;
    const px = [_]u8{0} ** 12;
    try std.testing.expectError(error.InvalidArgument, encodePng(gpa, &px, 0, 1, 3, 3));
    try std.testing.expectError(error.InvalidArgument, encodePng(gpa, &px, 2, 2, 6, 5));
    try std.testing.expectError(error.InvalidArgument, encodePng(gpa, &px, 2, 3, 6, 3));
    try std.testing.expectError(error.InvalidArgument, encodePng(gpa, &px, 4, 1, 8, 3));
}

test "window json escapes quotes, backslashes and control characters" {
    const gpa = std.testing.allocator;
    const entries = [_]WindowEntry{.{
        .hwnd = 0x1234,
        .title = "He said \"hi\" \\ tab\there\x01",
        .class = "#32770",
        .pid = 42,
        .bounds = .{ .left = -5, .top = 0, .right = 100, .bottom = 50 },
        .width = 105,
        .height = 50,
        .visible = true,
        .minimized = false,
    }};
    const json = try windowsJson(gpa, &entries);
    defer gpa.free(json);
    try std.testing.expect(std.mem.indexOf(u8, json, "\\\"hi\\\"") != null);
    try std.testing.expect(std.mem.indexOf(u8, json, "\\\\ tab\\there\\u0001") != null);
    const parsed = try std.json.parseFromSlice([]const WindowEntry, gpa, json, .{});
    defer parsed.deinit();
    try std.testing.expectEqual(@as(usize, 1), parsed.value.len);
    try std.testing.expectEqualStrings(entries[0].title, parsed.value[0].title);
    try std.testing.expectEqual(entries[0].bounds, parsed.value[0].bounds);
    try std.testing.expectEqual(@as(u64, 0x1234), parsed.value[0].hwnd);
}

test "utf-8 to utf-16 conversion keeps surrogate pairs and lone surrogates" {
    const gpa = std.testing.allocator;
    const wide = try utf8ToUtf16Z(gpa, "a\u{1F600}b");
    defer gpa.free(wide);
    try std.testing.expectEqualSlices(u16, &.{ 'a', 0xD83D, 0xDE00, 'b' }, wide);
    try std.testing.expectEqual(@as(u16, 0), wide[wide.len]);
    const back = try utf16ToUtf8(gpa, wide);
    defer gpa.free(back);
    try std.testing.expectEqualStrings("a\u{1F600}b", back);
    // A lone high surrogate from a Win32 title survives as WTF-8.
    const lone = try utf16ToUtf8(gpa, &.{ 'x', 0xD800, 'y' });
    defer gpa.free(lone);
    try std.testing.expectEqualSlices(u8, &.{ 'x', 0xED, 0xA0, 0x80, 'y' }, lone);
    try std.testing.expectError(error.InvalidArgument, utf8ToUtf16Z(gpa, "\xff\xfe"));
    try std.testing.expectEqual(Utf16Units{ .high = 0xD83D, .low = 0xDE00 }, codepointToUtf16(0x1F600));
    try std.testing.expectEqual(Utf16Units{ .high = 'z' }, codepointToUtf16('z'));
}

test "code point truncation never splits a sequence" {
    try std.testing.expectEqualStrings("ab", truncateCodepoints("abc", 2));
    try std.testing.expectEqualStrings("a\u{1F600}", truncateCodepoints("a\u{1F600}b", 2));
    try std.testing.expectEqualStrings("abc", truncateCodepoints("abc", 10));
    try std.testing.expectEqualStrings("a", truncateCodepoints("a\xf0\x9f", 5));
}

test "case-insensitive contains and whitespace trim" {
    try std.testing.expect(containsIgnoreCase("Chrome_WidgetWin_1", "chrome_widgetwin"));
    try std.testing.expect(containsIgnoreCase("Untitled - Notepad", "NOTEPAD"));
    try std.testing.expect(!containsIgnoreCase("Notepad", "notepad++"));
    try std.testing.expect(containsIgnoreCase("anything", ""));
    try std.testing.expectEqualStrings("Title", trimWhitespace(" \tTitle\r\n"));
}

test "absolute coordinates match the previous normalisation" {
    const screen = VirtualScreen{ .left = -1920, .top = 0, .width = 3840, .height = 1080 };
    try std.testing.expectEqual(AbsPoint{ .x = 0, .y = 0 }, absCoords(-1920, 0, screen));
    try std.testing.expectEqual(AbsPoint{ .x = 65535, .y = 65535 }, absCoords(1919, 1079, screen));
    try std.testing.expectEqual(AbsPoint{ .x = 32776, .y = 30368 }, absCoords(0, 500, screen));
    const tiny = VirtualScreen{ .left = 0, .top = 0, .width = 1, .height = 1 };
    try std.testing.expectEqual(AbsPoint{ .x = 0, .y = 0 }, absCoords(0, 0, tiny));
}

test "argv json parsing rejects malformed families" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    const argv = try parseArgv(gpa, "[\"cmd\", \"/c\", \"echo \\\"hi\\\"\"]");
    try std.testing.expectEqual(@as(usize, 3), argv.len);
    try std.testing.expectEqualStrings("echo \"hi\"", argv[2]);
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "[]"));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "[\"\"]"));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "[1, 2]"));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "{\"a\": 1}"));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "not json"));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, ""));
    try std.testing.expectError(error.InvalidArgument, parseArgv(gpa, "[\"cmd\", \"a\\u0000b\"]"));
}

test "env json parsing sorts case-insensitively and builds a block" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const gpa = arena.allocator();
    try std.testing.expectEqual(@as(?[]EnvPair, null), try parseEnv(gpa, ""));
    try std.testing.expectEqual(@as(?[]EnvPair, null), try parseEnv(gpa, "  \n"));
    try std.testing.expectEqual(@as(?[]EnvPair, null), try parseEnv(gpa, "null"));
    const pairs = (try parseEnv(gpa, "{\"b\": \"2\", \"A\": \"1\", \"path\": \"C:\\\\x\"}")).?;
    try std.testing.expectEqual(@as(usize, 3), pairs.len);
    try std.testing.expectEqualStrings("A", pairs[0].key);
    try std.testing.expectEqualStrings("b", pairs[1].key);
    try std.testing.expectEqualStrings("path", pairs[2].key);
    const block = try envBlock(gpa, pairs);
    const expected = try std.unicode.utf8ToUtf16LeAlloc(gpa, "A=1\x00b=2\x00path=C:\\x\x00\x00");
    try std.testing.expectEqualSlices(u16, expected, block);
    const empty = try envBlock(gpa, &.{});
    try std.testing.expectEqualSlices(u16, &.{ 0, 0 }, empty);
    try std.testing.expectError(error.InvalidArgument, parseEnv(gpa, "{\"k\": 1}"));
    try std.testing.expectError(error.InvalidArgument, parseEnv(gpa, "{\"a=b\": \"1\"}"));
    try std.testing.expectError(error.InvalidArgument, parseEnv(gpa, "[\"a\"]"));
    try std.testing.expectError(error.InvalidArgument, parseEnv(gpa, "{"));
}

test "command line quoting follows CommandLineToArgvW rules" {
    const gpa = std.testing.allocator;
    const cases = [_]struct { argv: []const []const u8, expected: []const u8 }{
        .{ .argv = &.{ "cmd", "/c", "timeout 30" }, .expected = "cmd /c \"timeout 30\"" },
        .{ .argv = &.{ "C:\\Program Files\\x.exe", "" }, .expected = "\"C:\\Program Files\\x.exe\" \"\"" },
        .{ .argv = &.{ "a", "say \"hi\"" }, .expected = "a \"say \\\"hi\\\"\"" },
        .{ .argv = &.{ "a", "back\\slash\\" }, .expected = "a back\\slash\\" },
        .{ .argv = &.{ "a", "trail \\" }, .expected = "a \"trail \\\\\"" },
        .{ .argv = &.{ "a", "\\\"" }, .expected = "a \"\\\\\\\"\"" },
    };
    for (cases) |case| {
        const wide = try commandLine(gpa, case.argv);
        defer gpa.free(wide);
        const narrow = try std.unicode.utf16LeToUtf8Alloc(gpa, wide);
        defer gpa.free(narrow);
        try std.testing.expectEqualStrings(case.expected, narrow);
    }
    try std.testing.expectError(error.InvalidArgument, commandLine(gpa, &.{}));
    try std.testing.expectError(error.InvalidArgument, commandLine(gpa, &.{"bad\"exe"}));
}

test "status mapping and free are stable across the abi" {
    try std.testing.expectEqual(@as(i32, 4), statusOf(error.Unsupported));
    try std.testing.expectEqual(@as(i32, 1), statusOf(error.InvalidArgument));
    try std.testing.expectEqual(@as(i32, 3), statusOf(error.OperationFailed));
    const buffer = try allocator.alloc(u8, 16);
    remedy_core_free(buffer.ptr, buffer.len);
    remedy_core_free(null, 0);
    setOsError(5);
    try std.testing.expectEqual(@as(u32, 5), remedy_core_last_os_error());
    setOsError(0);
}

test "encode png export validates its output slots" {
    var out_ptr: ?[*]u8 = null;
    var out_len: usize = 0;
    const px = [_]u8{ 9, 8, 7 };
    try std.testing.expectEqual(ok_status, remedy_core_encode_png(&px, px.len, 1, 1, 3, 3, &out_ptr, &out_len));
    try std.testing.expect(out_len > 40);
    remedy_core_free(out_ptr, out_len);
    try std.testing.expectEqual(invalid_status, remedy_core_encode_png(&px, px.len, 0, 1, 3, 3, &out_ptr, &out_len));
    try std.testing.expectEqual(@as(?[*]u8, null), out_ptr);
    try std.testing.expectEqual(invalid_status, remedy_core_encode_png(&px, px.len, 1, 1, 3, 3, null, &out_len));
}

test "host exports report unsupported off windows" {
    if (is_windows) return;
    if (is_linux) {
        // Linux implements desktop host + process control; pid 0/1 stay invalid.
        try std.testing.expectEqual(ok_status, remedy_core_dpi_awareness_enable());
        try std.testing.expectEqual(invalid_status, remedy_core_process_kill_tree(0));
        try std.testing.expectEqual(invalid_status, remedy_core_process_kill_tree(1));
        return;
    }
    try std.testing.expectEqual(unsupported_status, remedy_core_dpi_awareness_enable());
    try std.testing.expectEqual(unsupported_status, remedy_core_mouse_move(0, 0));
    try std.testing.expectEqual(unsupported_status, remedy_core_process_kill_tree(1));
    var ptr: ?[*]u8 = null;
    var len: usize = 0;
    try std.testing.expectEqual(unsupported_status, remedy_core_list_windows(10, &ptr, &len));
}

test {
    if (is_windows) _ = windows;
    if (is_linux) _ = linux;
}
