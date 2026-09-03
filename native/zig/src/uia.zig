//! UI Automation C ABI (ABI 3). Windows implementation in `uia_windows.zig`;
//! other targets export the same symbols as `unsupported`.

const std = @import("std");
const builtin = @import("builtin");
const root = @import("root.zig");
const host = @import("host.zig");

pub const is_windows = builtin.os.tag == .windows;
const windows = if (is_windows) @import("uia_windows.zig") else struct {};

const Status = root.Status;
const Error = host.Error;

const ok_status: i32 = @intFromEnum(Status.ok);
const invalid_status: i32 = @intFromEnum(Status.invalid_argument);
const unsupported_status: i32 = @intFromEnum(Status.unsupported);

fn statusOf(err: Error) i32 {
    return host.statusOf(err);
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

fn slice(ptr: ?[*]const u8, len: usize) []const u8 {
    const raw = ptr orelse return "";
    return raw[0..len];
}

export fn remedy_core_uia_available(out_available: ?*u8) callconv(.c) i32 {
    const output = out_available orelse return invalid_status;
    if (!is_windows) {
        output.* = 0;
        return unsupported_status;
    }
    output.* = @intFromBool(windows.available());
    return ok_status;
}

export fn remedy_core_uia_control_snapshot(
    hwnd: u64,
    max_elements: u32,
    preferred_only: u8,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return deliverBytes(
        windows.controlSnapshot(hwnd, max_elements, preferred_only != 0),
        out_json,
        out_len,
    );
}

export fn remedy_core_uia_read_window_text(
    hwnd: u64,
    max_chars: u32,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return deliverBytes(windows.readWindowText(hwnd, max_chars), out_json, out_len);
}

export fn remedy_core_uia_focused_element(
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return deliverBytes(windows.focusedElement(), out_json, out_len);
}

export fn remedy_core_uia_element_action(
    hwnd: u64,
    name: ?[*]const u8,
    name_len: usize,
    role: ?[*]const u8,
    role_len: usize,
    action: ?[*]const u8,
    action_len: usize,
    text: ?[*]const u8,
    text_len: usize,
    out_json: ?*?[*]u8,
    out_len: ?*usize,
) callconv(.c) i32 {
    if (!is_windows) return unsupported_status;
    return deliverBytes(
        windows.elementAction(
            hwnd,
            slice(name, name_len),
            slice(role, role_len),
            slice(action, action_len),
            slice(text, text_len),
        ),
        out_json,
        out_len,
    );
}

test {
    if (is_windows) _ = windows;
}
