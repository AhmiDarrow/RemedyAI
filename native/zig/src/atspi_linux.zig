//! AT-SPI2 tree walk for Linux accessibility snapshots (ABI 3).
//!
//! Mirrors the Python `_walk_atspi_tree` contract: BFS depth ≤ 12, ≤ 500
//! visited nodes, ≤ 80 children per node, clickable-role filter, JSON array of
//! `{x,y,w,h,area,name,role,source:"atspi"}`.

const std = @import("std");
const host = @import("host.zig");

const Error = host.Error;

const GError = opaque {};
const AtspiAccessible = opaque {};
const AtspiComponent = opaque {};

const AtspiRect = extern struct {
    x: c_int,
    y: c_int,
    width: c_int,
    height: c_int,
};

const AtspiCoordType = enum(c_int) {
    screen = 0,
    window = 1,
    parent = 2,
};

extern "atspi" fn atspi_init() callconv(.c) c_int;
extern "atspi" fn atspi_get_desktop(i: c_int) callconv(.c) ?*AtspiAccessible;
extern "atspi" fn atspi_accessible_get_name(obj: *AtspiAccessible, err: ?*?*GError) callconv(.c) ?[*:0]u8;
extern "atspi" fn atspi_accessible_get_role_name(obj: *AtspiAccessible, err: ?*?*GError) callconv(.c) ?[*:0]u8;
extern "atspi" fn atspi_accessible_get_child_count(obj: *AtspiAccessible, err: ?*?*GError) callconv(.c) c_int;
extern "atspi" fn atspi_accessible_get_child_at_index(obj: *AtspiAccessible, index: c_int, err: ?*?*GError) callconv(.c) ?*AtspiAccessible;
extern "atspi" fn atspi_accessible_get_component_iface(obj: *AtspiAccessible) callconv(.c) ?*AtspiComponent;
extern "atspi" fn atspi_component_get_extents(
    obj: *AtspiComponent,
    typ: AtspiCoordType,
    err: ?*?*GError,
) callconv(.c) ?*AtspiRect;

extern "glib-2.0" fn g_free(mem: ?*anyopaque) callconv(.c) void;
extern "gobject-2.0" fn g_object_unref(object: ?*anyopaque) callconv(.c) void;

const Candidate = struct {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    area: i32,
    name: []const u8,
    role: []const u8,
    source: []const u8 = "atspi",
};

const click_roles = [_][]const u8{
    "push button", "button",         "toggle button",  "toggle",
    "radio button", "radio",         "check box",      "checkbox",
    "menu item",    "check menu item", "radio menu item", "link",
    "hyperlink",    "entry",         "password text",  "text",
    "edit",         "editbar",       "spin button",    "combo box",
    "combobox",     "slider",        "page tab",       "tab",
    "list item",    "tree item",     "heading",        "image",
    "icon",         "split button",  "tool bar",       "toolbar",
};

const skip_roles = [_][]const u8{
    "application",     "frame",           "window",          "desktop frame",
    "filler",          "separator",       "scroll bar",      "scrollbar",
    "redundant object", "bounding box",   "layered pane",    "html container",
    "document web",    "document frame",  "page tab list",   "menu bar",
    "menubar",         "status bar",      "statusbar",       "split pane",
    "panel",           "unknown",         "invalid",
};

fn roleIn(role: []const u8, list: []const []const u8) bool {
    for (list) |item| {
        if (std.ascii.eqlIgnoreCase(role, item)) return true;
    }
    return false;
}

fn takeCString(gpa: std.mem.Allocator, raw: ?[*:0]u8) Error![]u8 {
    const ptr = raw orelse return gpa.dupe(u8, "") catch return error.OutOfMemory;
    defer g_free(ptr);
    return gpa.dupe(u8, std.mem.span(ptr)) catch return error.OutOfMemory;
}

fn extentsOf(acc: *AtspiAccessible) ?AtspiRect {
    const component = atspi_accessible_get_component_iface(acc) orelse return null;
    // Component iface is borrowed; do not unref.
    const rect_ptr = atspi_component_get_extents(component, .screen, null) orelse return null;
    defer g_free(rect_ptr);
    const rect = rect_ptr.*;
    if (rect.width < 4 or rect.height < 4) return null;
    return rect;
}

fn walk(gpa: std.mem.Allocator, root: *AtspiAccessible, limit: usize) Error![]Candidate {
    var out: std.ArrayList(Candidate) = .empty;
    errdefer {
        for (out.items) |c| {
            gpa.free(c.name);
            gpa.free(c.role);
        }
        out.deinit(gpa);
    }

    const Node = struct { acc: *AtspiAccessible, depth: u32, owned: bool };
    var queue: std.ArrayList(Node) = .empty;
    defer {
        for (queue.items) |node| {
            if (node.owned) g_object_unref(node.acc);
        }
        queue.deinit(gpa);
    }
    try queue.append(gpa, .{ .acc = root, .depth = 0, .owned = false });

    var visited: usize = 0;
    var index: usize = 0;
    while (index < queue.items.len and out.items.len < limit and visited < 500) : (index += 1) {
        const node = queue.items[index];
        visited += 1;
        if (node.depth > 12) continue;

        const role = try takeCString(gpa, atspi_accessible_get_role_name(node.acc, null));
        defer gpa.free(role);
        const name = try takeCString(gpa, atspi_accessible_get_name(node.acc, null));
        defer gpa.free(name);
        const role_l = std.ascii.allocLowerString(gpa, role) catch return error.OutOfMemory;
        defer gpa.free(role_l);

        if (extentsOf(node.acc)) |rect| {
            const interesting = !roleIn(role_l, &skip_roles) and
                (roleIn(role_l, &click_roles) or (name.len > 0 and rect.width < 800));
            if (interesting and rect.width < 4000 and rect.height < 3000) {
                const label = if (name.len > 0) name else role_l;
                const clipped = host.truncateCodepoints(label, 80);
                try out.append(gpa, .{
                    .x = rect.x + @divTrunc(rect.width, 2),
                    .y = rect.y + @divTrunc(rect.height, 2),
                    .w = rect.width,
                    .h = rect.height,
                    .area = rect.width *% rect.height,
                    .name = try gpa.dupe(u8, clipped),
                    .role = try gpa.dupe(u8, if (role_l.len > 0) role_l else "widget"),
                });
            }
        }

        if (node.depth < 12 and out.items.len < limit) {
            const count = atspi_accessible_get_child_count(node.acc, null);
            const n = @max(@as(c_int, 0), @min(count, @as(c_int, 80)));
            var i: c_int = 0;
            while (i < n) : (i += 1) {
                const child = atspi_accessible_get_child_at_index(node.acc, i, null) orelse continue;
                try queue.append(gpa, .{ .acc = child, .depth = node.depth + 1, .owned = true });
            }
        }
    }

    std.mem.sort(Candidate, out.items, {}, struct {
        fn less(_: void, a: Candidate, b: Candidate) bool {
            return a.area > b.area;
        }
    }.less);

    if (out.items.len > limit) {
        for (out.items[limit..]) |c| {
            gpa.free(c.name);
            gpa.free(c.role);
        }
        out.shrinkRetainingCapacity(limit);
    }
    return out.toOwnedSlice(gpa);
}

pub fn snapshotJson(gpa: std.mem.Allocator, limit: u32) Error![]u8 {
    _ = atspi_init();
    const desktop = atspi_get_desktop(0) orelse {
        return gpa.dupe(u8, "[]") catch return error.OutOfMemory;
    };
    // Desktop accessible is owned by the registry; do not unref.
    var arena = std.heap.ArenaAllocator.init(gpa);
    defer arena.deinit();
    const candidates = walk(arena.allocator(), desktop, @max(limit, 1)) catch |err| switch (err) {
        error.OutOfMemory => return error.OutOfMemory,
        else => return gpa.dupe(u8, "[]") catch return error.OutOfMemory,
    };
    return std.json.Stringify.valueAlloc(gpa, candidates, .{}) catch return error.OutOfMemory;
}
