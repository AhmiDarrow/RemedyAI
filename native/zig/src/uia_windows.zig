//! Windows UI Automation via COM vtable calls (no typelib / comtypes).
//!
//! Mirrors the former Python `desktop_uia` walk and action semantics so the
//! JSON shapes stay field-compatible with `tests/fixtures/uia/`.

const std = @import("std");
const builtin = @import("builtin");
const host = @import("host.zig");

const Error = host.Error;
const allocator = host.allocator;

const BOOL = c_int;
const DWORD = u32;
const HRESULT = i32;
const HWND = ?*anyopaque;
const BSTR = ?[*:0]u16;
const ULONG = u32;

const S_OK: HRESULT = 0;
const S_FALSE: HRESULT = 1;
const COINIT_APARTMENTTHREADED: DWORD = 0x2;
const CLSCTX_INPROC_SERVER: DWORD = 0x1;
const RPC_E_CHANGED_MODE: HRESULT = -2147417850; // 0x80010106
const VT_EMPTY: u16 = 0;
const VT_I4: u16 = 3;
const VT_BSTR: u16 = 8;
const VT_BOOL: u16 = 11;
const VT_UNKNOWN: u16 = 13;

const TreeScope_Children: c_int = 2;
const TreeScope_Descendants: c_int = 4;

const UIA_BoundingRectanglePropertyId: c_int = 30001;
const UIA_ControlTypePropertyId: c_int = 30003;
const UIA_NamePropertyId: c_int = 30005;
const UIA_IsEnabledPropertyId: c_int = 30010;
const UIA_IsOffscreenPropertyId: c_int = 30022;
const UIA_ValueValuePropertyId: c_int = 30045;

const UIA_InvokePatternId: c_int = 10000;
const UIA_ValuePatternId: c_int = 10002;
const UIA_TextPatternId: c_int = 10014;
const UIA_TogglePatternId: c_int = 10015;
const UIA_ScrollItemPatternId: c_int = 10017;

const GUID = extern struct {
    Data1: u32,
    Data2: u16,
    Data3: u16,
    Data4: [8]u8,
};

const CLSID_CUIAutomation = GUID{
    .Data1 = 0xFF48DBA4,
    .Data2 = 0x60EF,
    .Data3 = 0x4201,
    .Data4 = .{ 0xAA, 0x87, 0x54, 0x10, 0x3E, 0xEF, 0x59, 0x4E },
};

const IID_IUnknown = GUID{
    .Data1 = 0x00000000,
    .Data2 = 0x0000,
    .Data3 = 0x0000,
    .Data4 = .{ 0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46 },
};

const IID_IUIAutomation = GUID{
    .Data1 = 0x30CBE57D,
    .Data2 = 0xD9D0,
    .Data3 = 0x452A,
    .Data4 = .{ 0xAB, 0x13, 0x7A, 0xC5, 0xAC, 0x48, 0x25, 0xEE },
};

const IID_IUIAutomationElement = GUID{
    .Data1 = 0xD22108AA,
    .Data2 = 0x8AC5,
    .Data3 = 0x49A5,
    .Data4 = .{ 0x83, 0x7B, 0x37, 0xBB, 0xB3, 0xD7, 0x59, 0x1E },
};

const IID_IUIAutomationInvokePattern = GUID{
    .Data1 = 0xFB377FBE,
    .Data2 = 0x8EA6,
    .Data3 = 0x46D5,
    .Data4 = .{ 0x9C, 0x73, 0x64, 0x99, 0x64, 0x2D, 0x30, 0x59 },
};

const IID_IUIAutomationValuePattern = GUID{
    .Data1 = 0xA94CD8B1,
    .Data2 = 0x0844,
    .Data3 = 0x4CD6,
    .Data4 = .{ 0x9D, 0x2D, 0x64, 0x05, 0x37, 0xAB, 0x39, 0xE9 },
};

const IID_IUIAutomationTextPattern = GUID{
    .Data1 = 0x32EBA289,
    .Data2 = 0x3583,
    .Data3 = 0x42C9,
    .Data4 = .{ 0x9C, 0x59, 0x3B, 0x6D, 0x9A, 0x1E, 0x9B, 0x6A },
};

const IID_IUIAutomationTogglePattern = GUID{
    .Data1 = 0x94CF8058,
    .Data2 = 0x9B8D,
    .Data3 = 0x4AB9,
    .Data4 = .{ 0x8B, 0xFD, 0x4C, 0xD0, 0xA3, 0x3C, 0x8C, 0x70 },
};

const IID_IUIAutomationScrollItemPattern = GUID{
    .Data1 = 0xB488300F,
    .Data2 = 0xD015,
    .Data3 = 0x4F19,
    .Data4 = .{ 0x9C, 0x29, 0xBB, 0x59, 0x5E, 0x36, 0x45, 0xEF },
};

const IID_IUIAutomationTextRange = GUID{
    .Data1 = 0xA543CC6A,
    .Data2 = 0xF4AE,
    .Data3 = 0x494B,
    .Data4 = .{ 0x82, 0x39, 0xC8, 0x14, 0x48, 0x11, 0x87, 0xA8 },
};

/// 24-byte VARIANT on Win64 (vt + 3 reserved words + 16-byte payload).
const VARIANT = extern struct {
    vt: u16 = VT_EMPTY,
    wReserved1: u16 = 0,
    wReserved2: u16 = 0,
    wReserved3: u16 = 0,
    payload: [16]u8 = .{0} ** 16,

    fn asI32(self: *const VARIANT) i32 {
        return std.mem.readInt(i32, self.payload[0..4], .little);
    }

    fn asBool(self: *const VARIANT) bool {
        const v = std.mem.readInt(i16, self.payload[0..2], .little);
        return v != 0;
    }

    fn asBstr(self: *const VARIANT) BSTR {
        const ptr = std.mem.readInt(usize, self.payload[0..8], .little);
        if (ptr == 0) return null;
        return @ptrFromInt(ptr);
    }

    fn asIUnknown(self: *const VARIANT) ?*IUnknown {
        const ptr = std.mem.readInt(usize, self.payload[0..8], .little);
        if (ptr == 0) return null;
        return @ptrFromInt(ptr);
    }

    fn setBstr(self: *VARIANT, value: BSTR) void {
        self.* = .{};
        self.vt = VT_BSTR;
        const ptr: usize = if (value) |p| @intFromPtr(p) else 0;
        std.mem.writeInt(usize, self.payload[0..8], ptr, .little);
    }
};

const RECT = extern struct {
    left: i32 = 0,
    top: i32 = 0,
    right: i32 = 0,
    bottom: i32 = 0,
};

const IUnknown = opaque {};
const IUIAutomation = opaque {};
const IUIAutomationElement = opaque {};
const IUIAutomationElementArray = opaque {};
const IUIAutomationCondition = opaque {};
const IUIAutomationInvokePattern = opaque {};
const IUIAutomationValuePattern = opaque {};
const IUIAutomationTextPattern = opaque {};
const IUIAutomationTogglePattern = opaque {};
const IUIAutomationScrollItemPattern = opaque {};
const IUIAutomationTextRange = opaque {};

const IUnknownVtbl = extern struct {
    QueryInterface: *const fn (*IUnknown, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUnknown) callconv(.winapi) ULONG,
    Release: *const fn (*IUnknown) callconv(.winapi) ULONG,
};

const IUIAutomationVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomation, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomation) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomation) callconv(.winapi) ULONG,
    CompareElements: *const fn (*IUIAutomation, ?*IUIAutomationElement, ?*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    CompareRuntimeIds: *const fn (*IUIAutomation, ?*anyopaque, ?*anyopaque, *BOOL) callconv(.winapi) HRESULT,
    GetRootElement: *const fn (*IUIAutomation, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    ElementFromHandle: *const fn (*IUIAutomation, HWND, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    ElementFromPoint: *const fn (*IUIAutomation, i64, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetFocusedElement: *const fn (*IUIAutomation, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetRootElementBuildCache: *const fn (*IUIAutomation, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    ElementFromHandleBuildCache: *const fn (*IUIAutomation, HWND, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    ElementFromPointBuildCache: *const fn (*IUIAutomation, i64, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetFocusedElementBuildCache: *const fn (*IUIAutomation, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    CreateTreeWalker: *const fn (*IUIAutomation, ?*IUIAutomationCondition, *?*anyopaque) callconv(.winapi) HRESULT,
    get_ControlViewWalker: *const fn (*IUIAutomation, *?*anyopaque) callconv(.winapi) HRESULT,
    get_ContentViewWalker: *const fn (*IUIAutomation, *?*anyopaque) callconv(.winapi) HRESULT,
    get_RawViewWalker: *const fn (*IUIAutomation, *?*anyopaque) callconv(.winapi) HRESULT,
    get_RawViewCondition: *const fn (*IUIAutomation, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
    get_ControlViewCondition: *const fn (*IUIAutomation, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
    get_ContentViewCondition: *const fn (*IUIAutomation, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
    CreateCacheRequest: *const fn (*IUIAutomation, *?*anyopaque) callconv(.winapi) HRESULT,
    CreateTrueCondition: *const fn (*IUIAutomation, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
    CreateFalseCondition: *const fn (*IUIAutomation, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
    // VARIANT is 24 bytes; Win64 passes it by hidden pointer.
    CreatePropertyCondition: *const fn (*IUIAutomation, c_int, *const VARIANT, *?*IUIAutomationCondition) callconv(.winapi) HRESULT,
};

const IUIAutomationElementVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationElement, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationElement) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationElement) callconv(.winapi) ULONG,
    SetFocus: *const fn (*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetRuntimeId: *const fn (*IUIAutomationElement, *?*anyopaque) callconv(.winapi) HRESULT,
    FindFirst: *const fn (*IUIAutomationElement, c_int, ?*IUIAutomationCondition, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    FindAll: *const fn (*IUIAutomationElement, c_int, ?*IUIAutomationCondition, *?*IUIAutomationElementArray) callconv(.winapi) HRESULT,
    FindFirstBuildCache: *const fn (*IUIAutomationElement, c_int, ?*IUIAutomationCondition, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    FindAllBuildCache: *const fn (*IUIAutomationElement, c_int, ?*IUIAutomationCondition, ?*anyopaque, *?*IUIAutomationElementArray) callconv(.winapi) HRESULT,
    BuildUpdatedCache: *const fn (*IUIAutomationElement, ?*anyopaque, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetCurrentPropertyValue: *const fn (*IUIAutomationElement, c_int, *VARIANT) callconv(.winapi) HRESULT,
    GetCurrentPropertyValueEx: *const fn (*IUIAutomationElement, c_int, BOOL, *VARIANT) callconv(.winapi) HRESULT,
    GetCachedPropertyValue: *const fn (*IUIAutomationElement, c_int, *VARIANT) callconv(.winapi) HRESULT,
    GetCachedPropertyValueEx: *const fn (*IUIAutomationElement, c_int, BOOL, *VARIANT) callconv(.winapi) HRESULT,
    GetCurrentPatternAs: *const fn (*IUIAutomationElement, c_int, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    GetCachedPatternAs: *const fn (*IUIAutomationElement, c_int, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    GetCurrentPattern: *const fn (*IUIAutomationElement, c_int, *?*IUnknown) callconv(.winapi) HRESULT,
    GetCachedPattern: *const fn (*IUIAutomationElement, c_int, *?*IUnknown) callconv(.winapi) HRESULT,
    GetCachedParent: *const fn (*IUIAutomationElement, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetCachedChildren: *const fn (*IUIAutomationElement, *?*IUIAutomationElementArray) callconv(.winapi) HRESULT,
    get_CurrentProcessId: *const fn (*IUIAutomationElement, *c_int) callconv(.winapi) HRESULT,
    get_CurrentControlType: *const fn (*IUIAutomationElement, *c_int) callconv(.winapi) HRESULT,
    get_CurrentLocalizedControlType: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentName: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentAcceleratorKey: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentAccessKey: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentHasKeyboardFocus: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentIsKeyboardFocusable: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentIsEnabled: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentAutomationId: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentClassName: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentHelpText: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentCulture: *const fn (*IUIAutomationElement, *c_int) callconv(.winapi) HRESULT,
    get_CurrentIsControlElement: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentIsContentElement: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentIsPassword: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentNativeWindowHandle: *const fn (*IUIAutomationElement, *HWND) callconv(.winapi) HRESULT,
    get_CurrentItemType: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentIsOffscreen: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentOrientation: *const fn (*IUIAutomationElement, *c_int) callconv(.winapi) HRESULT,
    get_CurrentFrameworkId: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentIsRequiredForForm: *const fn (*IUIAutomationElement, *BOOL) callconv(.winapi) HRESULT,
    get_CurrentItemStatus: *const fn (*IUIAutomationElement, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentBoundingRectangle: *const fn (*IUIAutomationElement, *RECT) callconv(.winapi) HRESULT,
};

const IUIAutomationElementArrayVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationElementArray, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationElementArray) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationElementArray) callconv(.winapi) ULONG,
    get_Length: *const fn (*IUIAutomationElementArray, *c_int) callconv(.winapi) HRESULT,
    GetElement: *const fn (*IUIAutomationElementArray, c_int, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
};

const IUIAutomationInvokePatternVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationInvokePattern, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationInvokePattern) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationInvokePattern) callconv(.winapi) ULONG,
    Invoke: *const fn (*IUIAutomationInvokePattern) callconv(.winapi) HRESULT,
};

const IUIAutomationValuePatternVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationValuePattern, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationValuePattern) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationValuePattern) callconv(.winapi) ULONG,
    SetValue: *const fn (*IUIAutomationValuePattern, BSTR) callconv(.winapi) HRESULT,
    get_CurrentValue: *const fn (*IUIAutomationValuePattern, *BSTR) callconv(.winapi) HRESULT,
    get_CurrentIsReadOnly: *const fn (*IUIAutomationValuePattern, *BOOL) callconv(.winapi) HRESULT,
};

const IUIAutomationTextPatternVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationTextPattern, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationTextPattern) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationTextPattern) callconv(.winapi) ULONG,
    RangeFromPoint: *const fn (*IUIAutomationTextPattern, i64, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
    RangeFromChild: *const fn (*IUIAutomationTextPattern, ?*IUIAutomationElement, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
    GetSelection: *const fn (*IUIAutomationTextPattern, *?*anyopaque) callconv(.winapi) HRESULT,
    GetVisibleRanges: *const fn (*IUIAutomationTextPattern, *?*anyopaque) callconv(.winapi) HRESULT,
    get_DocumentRange: *const fn (*IUIAutomationTextPattern, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
};

const IUIAutomationTogglePatternVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationTogglePattern, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationTogglePattern) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationTogglePattern) callconv(.winapi) ULONG,
    Toggle: *const fn (*IUIAutomationTogglePattern) callconv(.winapi) HRESULT,
    get_CurrentToggleState: *const fn (*IUIAutomationTogglePattern, *c_int) callconv(.winapi) HRESULT,
};

const IUIAutomationScrollItemPatternVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationScrollItemPattern, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationScrollItemPattern) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationScrollItemPattern) callconv(.winapi) ULONG,
    ScrollIntoView: *const fn (*IUIAutomationScrollItemPattern) callconv(.winapi) HRESULT,
};

const IUIAutomationTextRangeVtbl = extern struct {
    QueryInterface: *const fn (*IUIAutomationTextRange, *const GUID, *?*anyopaque) callconv(.winapi) HRESULT,
    AddRef: *const fn (*IUIAutomationTextRange) callconv(.winapi) ULONG,
    Release: *const fn (*IUIAutomationTextRange) callconv(.winapi) ULONG,
    Clone: *const fn (*IUIAutomationTextRange, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
    Compare: *const fn (*IUIAutomationTextRange, ?*IUIAutomationTextRange, *BOOL) callconv(.winapi) HRESULT,
    CompareEndpoints: *const fn (*IUIAutomationTextRange, c_int, ?*IUIAutomationTextRange, c_int, *c_int) callconv(.winapi) HRESULT,
    ExpandToEnclosingUnit: *const fn (*IUIAutomationTextRange, c_int) callconv(.winapi) HRESULT,
    FindAttribute: *const fn (*IUIAutomationTextRange, c_int, VARIANT, BOOL, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
    FindText: *const fn (*IUIAutomationTextRange, BSTR, BOOL, BOOL, *?*IUIAutomationTextRange) callconv(.winapi) HRESULT,
    GetAttributeValue: *const fn (*IUIAutomationTextRange, c_int, *VARIANT) callconv(.winapi) HRESULT,
    GetBoundingRectangles: *const fn (*IUIAutomationTextRange, *?*anyopaque) callconv(.winapi) HRESULT,
    GetEnclosingElement: *const fn (*IUIAutomationTextRange, *?*IUIAutomationElement) callconv(.winapi) HRESULT,
    GetText: *const fn (*IUIAutomationTextRange, c_int, *BSTR) callconv(.winapi) HRESULT,
};

extern "ole32" fn CoInitializeEx(pvReserved: ?*anyopaque, dwCoInit: DWORD) callconv(.winapi) HRESULT;
extern "ole32" fn CoCreateInstance(
    rclsid: *const GUID,
    pUnkOuter: ?*IUnknown,
    dwClsContext: DWORD,
    riid: *const GUID,
    ppv: *?*anyopaque,
) callconv(.winapi) HRESULT;
extern "oleaut32" fn SysAllocStringLen(str: ?[*]const u16, len: UINT) callconv(.winapi) BSTR;
extern "oleaut32" fn SysFreeString(bstr: BSTR) callconv(.winapi) void;
extern "oleaut32" fn SysStringLen(bstr: BSTR) callconv(.winapi) UINT;
extern "oleaut32" fn VariantClear(pvarg: *VARIANT) callconv(.winapi) HRESULT;
extern "oleaut32" fn VariantInit(pvarg: *VARIANT) callconv(.winapi) void;
extern "kernel32" fn GetTickCount64() callconv(.winapi) u64;

const UINT = c_uint;

fn succeeded(hr: HRESULT) bool {
    return hr >= 0;
}

fn vtable(comptime T: type, obj: *anyopaque) *const T {
    const pp: *const *const T = @ptrCast(@alignCast(obj));
    return pp.*;
}

fn releaseUnknown(obj: ?*IUnknown) void {
    const raw = obj orelse return;
    _ = vtable(IUnknownVtbl, raw).Release(raw);
}

fn releaseAutomation(obj: ?*IUIAutomation) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationVtbl, raw).Release(raw);
}

fn releaseElement(obj: ?*IUIAutomationElement) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationElementVtbl, raw).Release(raw);
}

fn releaseElementArray(obj: ?*IUIAutomationElementArray) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationElementArrayVtbl, raw).Release(raw);
}

fn releaseCondition(obj: ?*IUIAutomationCondition) void {
    const raw = obj orelse return;
    _ = vtable(IUnknownVtbl, @ptrCast(raw)).Release(@ptrCast(raw));
}

fn releaseInvoke(obj: ?*IUIAutomationInvokePattern) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationInvokePatternVtbl, raw).Release(raw);
}

fn releaseValue(obj: ?*IUIAutomationValuePattern) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationValuePatternVtbl, raw).Release(raw);
}

fn releaseText(obj: ?*IUIAutomationTextPattern) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationTextPatternVtbl, raw).Release(raw);
}

fn releaseToggle(obj: ?*IUIAutomationTogglePattern) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationTogglePatternVtbl, raw).Release(raw);
}

fn releaseScrollItem(obj: ?*IUIAutomationScrollItemPattern) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationScrollItemPatternVtbl, raw).Release(raw);
}

fn releaseTextRange(obj: ?*IUIAutomationTextRange) void {
    const raw = obj orelse return;
    _ = vtable(IUIAutomationTextRangeVtbl, raw).Release(raw);
}

fn freeBstr(bstr: BSTR) void {
    if (bstr != null) SysFreeString(bstr);
}

fn ensureCom() void {
    const hr = CoInitializeEx(null, COINIT_APARTMENTTHREADED);
    // S_OK, S_FALSE (already init), RPC_E_CHANGED_MODE (other model) are fine.
    _ = hr;
}

fn createAutomation() Error!*IUIAutomation {
    ensureCom();
    var raw: ?*anyopaque = null;
    const hr = CoCreateInstance(&CLSID_CUIAutomation, null, CLSCTX_INPROC_SERVER, &IID_IUIAutomation, &raw);
    if (!succeeded(hr) or raw == null) {
        host.setOsError(@bitCast(hr));
        return error.OperationFailed;
    }
    return @ptrCast(raw.?);
}

fn bstrToUtf8(gpa: std.mem.Allocator, bstr: BSTR) Error![]u8 {
    const ptr = bstr orelse return gpa.dupe(u8, "") catch return error.OutOfMemory;
    const len = SysStringLen(bstr);
    if (len == 0) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    const wide = ptr[0..len];
    return host.utf16ToUtf8(gpa, wide);
}

fn utf8ToBstr(utf8: []const u8) Error!BSTR {
    const wide = host.utf8ToUtf16Z(allocator, utf8) catch |err| return err;
    defer allocator.free(wide);
    const bstr = SysAllocStringLen(wide.ptr, @intCast(wide.len));
    if (bstr == null) return error.OutOfMemory;
    return bstr;
}

fn trimOwned(gpa: std.mem.Allocator, text: []u8) Error![]u8 {
    const trimmed = host.trimWhitespace(text);
    if (trimmed.ptr == text.ptr and trimmed.len == text.len) return text;
    const copy = gpa.dupe(u8, trimmed) catch {
        gpa.free(text);
        return error.OutOfMemory;
    };
    gpa.free(text);
    return copy;
}

fn clipUtf8(text: []const u8, max_bytes: usize) []const u8 {
    if (text.len <= max_bytes) return text;
    // Prefer a codepoint boundary when truncating.
    var index: usize = max_bytes;
    while (index > 0 and (text[index] & 0xC0) == 0x80) index -= 1;
    return text[0..index];
}

const ControlTypeMap = struct {
    id: i32,
    name: []const u8,
};

const control_types = [_]ControlTypeMap{
    .{ .id = 50000, .name = "button" },
    .{ .id = 50001, .name = "calendar" },
    .{ .id = 50002, .name = "checkbox" },
    .{ .id = 50003, .name = "combobox" },
    .{ .id = 50004, .name = "edit" },
    .{ .id = 50005, .name = "hyperlink" },
    .{ .id = 50006, .name = "image" },
    .{ .id = 50007, .name = "listitem" },
    .{ .id = 50008, .name = "list" },
    .{ .id = 50009, .name = "menu" },
    .{ .id = 50010, .name = "menubar" },
    .{ .id = 50011, .name = "menuitem" },
    .{ .id = 50012, .name = "progressbar" },
    .{ .id = 50013, .name = "radiobutton" },
    .{ .id = 50014, .name = "scrollbar" },
    .{ .id = 50015, .name = "slider" },
    .{ .id = 50016, .name = "spinner" },
    .{ .id = 50018, .name = "tab" },
    .{ .id = 50019, .name = "tabitem" },
    .{ .id = 50020, .name = "text" },
    .{ .id = 50021, .name = "toolbar" },
    .{ .id = 50022, .name = "tooltip" },
    .{ .id = 50023, .name = "tree" },
    .{ .id = 50024, .name = "treeitem" },
    .{ .id = 50025, .name = "custom" },
    .{ .id = 50026, .name = "group" },
    .{ .id = 50027, .name = "thumb" },
    .{ .id = 50028, .name = "datagrid" },
    .{ .id = 50029, .name = "dataitem" },
    .{ .id = 50030, .name = "document" },
    .{ .id = 50031, .name = "splitbutton" },
    .{ .id = 50032, .name = "window" },
    .{ .id = 50033, .name = "pane" },
    .{ .id = 50034, .name = "header" },
    .{ .id = 50035, .name = "headeritem" },
    .{ .id = 50036, .name = "table" },
    .{ .id = 50037, .name = "titlebar" },
};

const preferred_types = [_]i32{ 50000, 50002, 50003, 50004, 50005, 50007, 50011, 50013, 50019, 50024, 50029, 50031 };
const shallow_extra = [_]i32{ 50020, 50025, 50026, 50032, 50033 };

pub fn roleName(ctrl: i32) []const u8 {
    for (control_types) |entry| {
        if (entry.id == ctrl) return entry.name;
    }
    return "";
}

fn isPreferred(ctrl: i32) bool {
    for (preferred_types) |id| if (id == ctrl) return true;
    return false;
}

fn isShallowExtra(ctrl: i32) bool {
    for (shallow_extra) |id| if (id == ctrl) return true;
    return false;
}

fn elName(el: *IUIAutomationElement, gpa: std.mem.Allocator) Error![]u8 {
    var bstr: BSTR = null;
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentName(el, &bstr);
    defer freeBstr(bstr);
    if (!succeeded(hr)) return gpa.dupe(u8, "") catch return error.OutOfMemory;
    const raw = try bstrToUtf8(gpa, bstr);
    return trimOwned(gpa, raw);
}

fn elControlType(el: *IUIAutomationElement) i32 {
    var ctrl: c_int = 0;
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentControlType(el, &ctrl);
    if (!succeeded(hr)) return 0;
    return ctrl;
}

fn elRole(el: *IUIAutomationElement) []const u8 {
    return roleName(elControlType(el));
}

fn elEnabled(el: *IUIAutomationElement) bool {
    var value: BOOL = 1;
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentIsEnabled(el, &value);
    if (!succeeded(hr)) return true;
    return value != 0;
}

fn elOffscreen(el: *IUIAutomationElement) bool {
    var value: BOOL = 0;
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentIsOffscreen(el, &value);
    if (!succeeded(hr)) return false;
    return value != 0;
}

fn elBounds(el: *IUIAutomationElement) struct { left: i32, top: i32, width: i32, height: i32 } {
    var rect: RECT = .{};
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentBoundingRectangle(el, &rect);
    if (!succeeded(hr)) return .{ .left = 0, .top = 0, .width = 0, .height = 0 };
    return .{
        .left = rect.left,
        .top = rect.top,
        .width = rect.right - rect.left,
        .height = rect.bottom - rect.top,
    };
}

fn elNativeHwnd(el: *IUIAutomationElement) ?u64 {
    var hwnd: HWND = null;
    const hr = vtable(IUIAutomationElementVtbl, el).get_CurrentNativeWindowHandle(el, &hwnd);
    if (!succeeded(hr) or hwnd == null) return null;
    return @intFromPtr(hwnd);
}

fn elValue(el: *IUIAutomationElement, gpa: std.mem.Allocator) Error![]u8 {
    // ValueValue property first.
    var prop: VARIANT = .{};
    VariantInit(&prop);
    const phr = vtable(IUIAutomationElementVtbl, el).GetCurrentPropertyValue(el, UIA_ValueValuePropertyId, &prop);
    defer _ = VariantClear(&prop);
    if (succeeded(phr) and prop.vt == VT_BSTR) {
        const text = try bstrToUtf8(gpa, prop.asBstr());
        if (text.len > 0) return text;
        gpa.free(text);
    }

    // ValuePattern.
    var unk: ?*IUnknown = null;
    const vhr = vtable(IUIAutomationElementVtbl, el).GetCurrentPattern(el, UIA_ValuePatternId, &unk);
    if (succeeded(vhr) and unk != null) {
        defer releaseUnknown(unk);
        var pattern: ?*anyopaque = null;
        const qhr = vtable(IUnknownVtbl, unk.?).QueryInterface(unk.?, &IID_IUIAutomationValuePattern, &pattern);
        if (succeeded(qhr) and pattern != null) {
            const vp: *IUIAutomationValuePattern = @ptrCast(pattern.?);
            defer releaseValue(vp);
            var bstr: BSTR = null;
            const ghr = vtable(IUIAutomationValuePatternVtbl, vp).get_CurrentValue(vp, &bstr);
            defer freeBstr(bstr);
            if (succeeded(ghr)) {
                const text = try bstrToUtf8(gpa, bstr);
                if (text.len > 0) return text;
                gpa.free(text);
            }
        }
    }

    // TextPattern DocumentRange.
    var tunk: ?*IUnknown = null;
    const thr = vtable(IUIAutomationElementVtbl, el).GetCurrentPattern(el, UIA_TextPatternId, &tunk);
    if (succeeded(thr) and tunk != null) {
        defer releaseUnknown(tunk);
        var pattern: ?*anyopaque = null;
        const qhr = vtable(IUnknownVtbl, tunk.?).QueryInterface(tunk.?, &IID_IUIAutomationTextPattern, &pattern);
        if (succeeded(qhr) and pattern != null) {
            const tp: *IUIAutomationTextPattern = @ptrCast(pattern.?);
            defer releaseText(tp);
            var range: ?*IUIAutomationTextRange = null;
            const rhr = vtable(IUIAutomationTextPatternVtbl, tp).get_DocumentRange(tp, &range);
            if (succeeded(rhr) and range != null) {
                defer releaseTextRange(range);
                var bstr: BSTR = null;
                const ghr = vtable(IUIAutomationTextRangeVtbl, range.?).GetText(range.?, 20000, &bstr);
                defer freeBstr(bstr);
                if (succeeded(ghr)) {
                    const text = try bstrToUtf8(gpa, bstr);
                    if (text.len > 0) return text;
                    gpa.free(text);
                }
            }
        }
    }

    return gpa.dupe(u8, "") catch return error.OutOfMemory;
}

fn findChildren(el: *IUIAutomationElement, uia: *IUIAutomation, scope: c_int) ?*IUIAutomationElementArray {
    var cond: ?*IUIAutomationCondition = null;
    const chr = vtable(IUIAutomationVtbl, uia).CreateTrueCondition(uia, &cond);
    if (!succeeded(chr) or cond == null) return null;
    defer releaseCondition(cond);
    var kids: ?*IUIAutomationElementArray = null;
    const fhr = vtable(IUIAutomationElementVtbl, el).FindAll(el, scope, cond, &kids);
    if (!succeeded(fhr)) {
        releaseElementArray(kids);
        return null;
    }
    return kids;
}

fn arrayLen(arr: *IUIAutomationElementArray) i32 {
    var n: c_int = 0;
    const hr = vtable(IUIAutomationElementArrayVtbl, arr).get_Length(arr, &n);
    if (!succeeded(hr) or n < 0) return 0;
    return n;
}

fn arrayGet(arr: *IUIAutomationElementArray, index: c_int) ?*IUIAutomationElement {
    var el: ?*IUIAutomationElement = null;
    const hr = vtable(IUIAutomationElementArrayVtbl, arr).GetElement(arr, index, &el);
    if (!succeeded(hr)) {
        releaseElement(el);
        return null;
    }
    return el;
}

fn jsonNull() Error![]u8 {
    return allocator.dupe(u8, "null") catch return error.OutOfMemory;
}

const json_opts = std.json.Stringify.Options{ .emit_null_optional_fields = false };

fn jsonValue(value: anytype) Error![]u8 {
    return std.json.Stringify.valueAlloc(allocator, value, json_opts) catch return error.OutOfMemory;
}

// ---------------------------------------------------------------------------
// Public operations
// ---------------------------------------------------------------------------

pub fn available() bool {
    const uia = createAutomation() catch return false;
    releaseAutomation(uia);
    return true;
}

const FieldJson = struct {
    name: []const u8,
    role: []const u8,
    value: []const u8,
};

const ReadWindowJson = struct {
    title: []const u8,
    text: []const u8,
    fields: []FieldJson,
};

const name_only_roles = [_][]const u8{
    "text", "button", "checkbox", "radiobutton", "hyperlink",
    "listitem", "menuitem", "tabitem", "header", "titlebar",
};

fn roleIn(role: []const u8, set: []const []const u8) bool {
    for (set) |item| if (std.mem.eql(u8, item, role)) return true;
    return false;
}

pub fn readWindowText(hwnd: u64, max_chars: u32) Error![]u8 {
    if (hwnd == 0) return jsonNull();
    const uia = try createAutomation();
    defer releaseAutomation(uia);

    var root: ?*IUIAutomationElement = null;
    const ehr = vtable(IUIAutomationVtbl, uia).ElementFromHandle(uia, @ptrFromInt(hwnd), &root);
    if (!succeeded(ehr) or root == null) return jsonNull();
    defer releaseElement(root);

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const title = try elName(root.?, gpa);
    var lines: std.ArrayList([]const u8) = .empty;
    var fields: std.ArrayList(FieldJson) = .empty;
    const budget: usize = @max(1000, @as(usize, max_chars));
    var seen: usize = 0;
    const deadline = GetTickCount64() + 8_000;

    const Walk = struct {
        fn go(
            self: *@This(),
            el: *IUIAutomationElement,
            depth: u32,
        ) Error!void {
            if (depth > 14 or self.seen >= self.budget) return;
            if (GetTickCount64() > self.deadline) return;

            const role = elRole(el);
            const name = try elName(el, self.gpa);

            if (roleIn(role, &.{ "edit", "document", "combobox", "spinner" })) {
                const val = try elValue(el, self.gpa);
                if (val.len > 0) {
                    const label = if (name.len > 0) name else role;
                    const fname = clipUtf8(label, 80);
                    const fval = clipUtf8(val, 4000);
                    try self.fields.append(self.gpa, .{
                        .name = try self.gpa.dupe(u8, fname),
                        .role = role,
                        .value = try self.gpa.dupe(u8, fval),
                    });
                    var entry_buf: std.ArrayList(u8) = .empty;
                    if (name.len > 0) {
                        try entry_buf.appendSlice(self.gpa, "[");
                        try entry_buf.appendSlice(self.gpa, label);
                        try entry_buf.appendSlice(self.gpa, "]: ");
                        try entry_buf.appendSlice(self.gpa, val);
                    } else {
                        try entry_buf.appendSlice(self.gpa, val);
                    }
                    const remain = if (self.seen >= self.budget) 0 else self.budget - self.seen;
                    const clipped = clipUtf8(entry_buf.items, remain);
                    const owned = try self.gpa.dupe(u8, clipped);
                    try self.lines.append(self.gpa, owned);
                    self.seen += owned.len;
                } else if (name.len > 0) {
                    try self.fields.append(self.gpa, .{
                        .name = try self.gpa.dupe(u8, clipUtf8(name, 80)),
                        .role = role,
                        .value = "",
                    });
                }
            } else if (name.len > 0 and roleIn(role, &name_only_roles)) {
                const remain = if (self.seen >= self.budget) 0 else self.budget - self.seen;
                const clipped = clipUtf8(name, remain);
                const owned = try self.gpa.dupe(u8, clipped);
                try self.lines.append(self.gpa, owned);
                self.seen += owned.len;
            }

            const kids = findChildren(el, self.uia, TreeScope_Children) orelse return;
            defer releaseElementArray(kids);
            const n = arrayLen(kids);
            var i: c_int = 0;
            while (i < n and i < 60) : (i += 1) {
                if (self.seen >= self.budget) break;
                if (GetTickCount64() > self.deadline) break;
                const child = arrayGet(kids, i) orelse continue;
                defer releaseElement(child);
                try self.go(child, depth + 1);
            }
        }

        gpa: std.mem.Allocator,
        uia: *IUIAutomation,
        lines: *std.ArrayList([]const u8),
        fields: *std.ArrayList(FieldJson),
        budget: usize,
        seen: usize,
        deadline: u64,
    };

    var walk = Walk{
        .gpa = gpa,
        .uia = uia,
        .lines = &lines,
        .fields = &fields,
        .budget = budget,
        .seen = 0,
        .deadline = deadline,
    };
    try walk.go(root.?, 0);
    seen = walk.seen;

    var out_lines: std.ArrayList([]const u8) = .empty;
    for (lines.items) |ln| {
        const s = host.trimWhitespace(ln);
        if (s.len == 0) continue;
        if (out_lines.items.len > 0 and std.mem.eql(u8, out_lines.items[out_lines.items.len - 1], s)) continue;
        try out_lines.append(gpa, try gpa.dupe(u8, s));
    }

    var text_buf: std.ArrayList(u8) = .empty;
    for (out_lines.items, 0..) |ln, idx| {
        if (idx > 0) try text_buf.append(gpa, '\n');
        try text_buf.appendSlice(gpa, ln);
    }
    const text_clipped = clipUtf8(text_buf.items, max_chars);

    const field_slice = fields.items[0..@min(fields.items.len, 40)];
    const payload = ReadWindowJson{
        .title = title,
        .text = text_clipped,
        .fields = field_slice,
    };
    return jsonValue(payload);
}

const FocusJson = struct {
    name: []const u8,
    role: []const u8,
    value: []const u8,
};

pub fn focusedElement() Error![]u8 {
    const uia = try createAutomation();
    defer releaseAutomation(uia);
    var el: ?*IUIAutomationElement = null;
    const hr = vtable(IUIAutomationVtbl, uia).GetFocusedElement(uia, &el);
    if (!succeeded(hr) or el == null) return jsonNull();
    defer releaseElement(el);

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const name = try elName(el.?, gpa);
    const role_raw = elRole(el.?);
    const role = if (role_raw.len == 0) "unknown" else role_raw;
    const value = try elValue(el.?, gpa);
    const payload = FocusJson{
        .name = clipUtf8(name, 120),
        .role = role,
        .value = clipUtf8(value, 400),
    };
    return jsonValue(payload);
}

const BoundsJson = struct { left: i32, top: i32, right: i32, bottom: i32 };

const ControlJson = struct {
    ref: []const u8,
    tag: []const u8,
    role: []const u8,
    name: []const u8,
    x: i32,
    y: i32,
    w: i32,
    h: i32,
    hwnd: ?u64,
    bounds: BoundsJson,
    uia: bool = true,
    offscreen: ?bool = null,
};

const clickable_unnamed = [_][]const u8{ "button", "edit", "hyperlink", "checkbox", "menuitem" };

pub fn controlSnapshot(hwnd: u64, max_elements: u32, preferred_only: bool) Error![]u8 {
    const uia = try createAutomation();
    defer releaseAutomation(uia);

    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const max_n: usize = @max(1, @min(if (max_elements == 0) 80 else max_elements, 120));
    var elements: std.ArrayList(ControlJson) = .empty;
    const deadline = GetTickCount64() + 8_000;

    const Walk = struct {
        fn addFrom(
            self: *@This(),
            element: *IUIAutomationElement,
            depth: u32,
            prefix_hwnd: ?u64,
        ) Error!void {
            if (self.elements.items.len >= self.max_n or depth > 12) return;
            if (GetTickCount64() > self.deadline) return;

            const ctrl = elControlType(element);
            const enabled = elEnabled(element);
            const offscreen = elOffscreen(element);
            const name = try elName(element, self.gpa);
            const bounds = elBounds(element);
            const width = bounds.width;
            const height = bounds.height;

            var include = true;
            if (self.preferred_only and ctrl != 0 and !isPreferred(ctrl)) {
                include = name.len > 0 and depth <= 2 and isShallowExtra(ctrl);
            }
            const usable_size = (width >= 4 and height >= 4) or offscreen;
            if (include and enabled and usable_size) {
                var role_buf: [32]u8 = undefined;
                const role: []const u8 = blk: {
                    const mapped = roleName(ctrl);
                    if (mapped.len > 0) break :blk mapped;
                    break :blk std.fmt.bufPrint(&role_buf, "type_{d}", .{ctrl}) catch "type_0";
                };
                const role_owned = try self.gpa.dupe(u8, role);
                if (name.len > 0 or roleIn(role_owned, &clickable_unnamed)) {
                    const display = if (name.len > 0) name else role_owned;
                    const cx = bounds.left + @divTrunc(width, 2);
                    const cy = bounds.top + @divTrunc(height, 2);
                    var ref_buf: [16]u8 = undefined;
                    const ref = try self.gpa.dupe(
                        u8,
                        std.fmt.bufPrint(&ref_buf, "c{d}", .{self.elements.items.len + 1}) catch "c0",
                    );
                    try self.elements.append(self.gpa, .{
                        .ref = ref,
                        .tag = role_owned,
                        .role = role_owned,
                        .name = try self.gpa.dupe(u8, clipUtf8(display, 120)),
                        .x = cx,
                        .y = cy,
                        .w = width,
                        .h = height,
                        .hwnd = prefix_hwnd,
                        .bounds = .{
                            .left = bounds.left,
                            .top = bounds.top,
                            .right = bounds.left + width,
                            .bottom = bounds.top + height,
                        },
                        .uia = true,
                        .offscreen = if (offscreen) true else null,
                    });
                }
            }

            const kids = findChildren(element, self.uia, TreeScope_Children) orelse return;
            defer releaseElementArray(kids);
            const n = arrayLen(kids);
            var i: c_int = 0;
            while (i < n and i < 40) : (i += 1) {
                if (self.elements.items.len >= self.max_n) break;
                if (GetTickCount64() > self.deadline) break;
                const child = arrayGet(kids, i) orelse continue;
                defer releaseElement(child);
                try self.addFrom(child, depth + 1, prefix_hwnd);
            }
        }

        gpa: std.mem.Allocator,
        uia: *IUIAutomation,
        elements: *std.ArrayList(ControlJson),
        max_n: usize,
        preferred_only: bool,
        deadline: u64,
    };

    var walk = Walk{
        .gpa = gpa,
        .uia = uia,
        .elements = &elements,
        .max_n = max_n,
        .preferred_only = preferred_only,
        .deadline = deadline,
    };

    if (hwnd != 0) {
        var root: ?*IUIAutomationElement = null;
        const ehr = vtable(IUIAutomationVtbl, uia).ElementFromHandle(uia, @ptrFromInt(hwnd), &root);
        if (!succeeded(ehr) or root == null) return jsonNull();
        defer releaseElement(root);
        try walk.addFrom(root.?, 0, hwnd);
    } else {
        var root: ?*IUIAutomationElement = null;
        const rhr = vtable(IUIAutomationVtbl, uia).GetRootElement(uia, &root);
        if (!succeeded(rhr) or root == null) return jsonNull();
        defer releaseElement(root);

        const tops = findChildren(root.?, uia, TreeScope_Children);
        if (tops) |arr| {
            defer releaseElementArray(arr);
            const n = arrayLen(arr);
            var i: c_int = 0;
            while (i < n and i < 15) : (i += 1) {
                if (elements.items.len >= max_n) break;
                const win_el = arrayGet(arr, i) orelse continue;
                defer releaseElement(win_el);
                const name = elName(win_el, gpa) catch continue;
                if (name.len == 0) continue;
                const wh = elNativeHwnd(win_el);
                try walk.addFrom(win_el, 0, wh);
            }
        } else {
            try walk.addFrom(root.?, 0, null);
        }
    }

    if (elements.items.len == 0) return jsonNull();
    return jsonValue(elements.items);
}

const ActionJson = struct {
    ok: bool,
    message: []const u8,
    verified: ?bool = null,
};

fn findLiveElement(
    uia: *IUIAutomation,
    hwnd: u64,
    name: []const u8,
    role: []const u8,
) Error!?*IUIAutomationElement {
    if (hwnd == 0) return null;
    var root: ?*IUIAutomationElement = null;
    const ehr = vtable(IUIAutomationVtbl, uia).ElementFromHandle(uia, @ptrFromInt(hwnd), &root);
    if (!succeeded(ehr) or root == null) return null;
    defer releaseElement(root);

    const name_bstr = try utf8ToBstr(name);
    defer freeBstr(name_bstr);
    var condition_var: VARIANT = .{};
    condition_var.setBstr(name_bstr);
    // CreatePropertyCondition copies the VARIANT; clear without freeing our BSTR twice.
    // After the call, clear vt so VariantClear does not free the BSTR we still own.
    var cond: ?*IUIAutomationCondition = null;
    const chr = vtable(IUIAutomationVtbl, uia).CreatePropertyCondition(uia, UIA_NamePropertyId, &condition_var, &cond);
    // CreatePropertyCondition copies the VARIANT; keep name_bstr for our own free.
    if (!succeeded(chr) or cond == null) return null;
    defer releaseCondition(cond);

    var found: ?*IUIAutomationElementArray = null;
    const fhr = vtable(IUIAutomationElementVtbl, root.?).FindAll(root.?, TreeScope_Descendants, cond, &found);
    if (!succeeded(fhr) or found == null) {
        releaseElementArray(found);
        return null;
    }
    defer releaseElementArray(found);

    const want_role = host.trimWhitespace(role);
    var want_buf: [128]u8 = undefined;
    const want_lower = blk: {
        if (want_role.len == 0) break :blk want_role;
        const n = @min(want_role.len, want_buf.len);
        for (want_role[0..n], 0..) |c, i| want_buf[i] = std.ascii.toLower(c);
        break :blk want_buf[0..n];
    };

    var fallback: ?*IUIAutomationElement = null;
    const n = arrayLen(found.?);
    var i: c_int = 0;
    while (i < n and i < 20) : (i += 1) {
        const el = arrayGet(found.?, i) orelse continue;
        const erole = elRole(el);
        if (want_lower.len == 0 or std.ascii.eqlIgnoreCase(erole, want_lower)) {
            releaseElement(fallback);
            return el;
        }
        if (fallback == null) {
            fallback = el;
        } else {
            releaseElement(el);
        }
    }
    return fallback;
}

fn patternAs(comptime T: type, iid: *const GUID, el: *IUIAutomationElement, pattern_id: c_int) ?*T {
    var unk: ?*IUnknown = null;
    const hr = vtable(IUIAutomationElementVtbl, el).GetCurrentPattern(el, pattern_id, &unk);
    if (!succeeded(hr) or unk == null) {
        releaseUnknown(unk);
        return null;
    }
    defer releaseUnknown(unk);
    var out: ?*anyopaque = null;
    const qhr = vtable(IUnknownVtbl, unk.?).QueryInterface(unk.?, iid, &out);
    if (!succeeded(qhr) or out == null) return null;
    return @ptrCast(out.?);
}

fn actionMessage(gpa: std.mem.Allocator, comptime fmt: []const u8, args: anytype) Error![]u8 {
    return std.fmt.allocPrint(gpa, fmt, args) catch return error.OutOfMemory;
}

pub fn elementAction(
    hwnd: u64,
    name: []const u8,
    role: []const u8,
    action: []const u8,
    text: []const u8,
) Error![]u8 {
    var arena = std.heap.ArenaAllocator.init(allocator);
    defer arena.deinit();
    const gpa = arena.allocator();

    const not_found = try actionMessage(
        gpa,
        "UIA element '{s}' not found in hwnd={d} (re-snapshot?)",
        .{ name, hwnd },
    );

    const uia = createAutomation() catch {
        return jsonValue(ActionJson{ .ok = false, .message = not_found });
    };
    defer releaseAutomation(uia);

    const el = findLiveElement(uia, hwnd, name, role) catch null;
    if (el == null) {
        return jsonValue(ActionJson{ .ok = false, .message = not_found });
    }
    defer releaseElement(el);

    const erole = elRole(el.?);
    const ename = elName(el.?, gpa) catch "";
    const label_role = if (erole.len == 0) "control" else erole;
    const label = try actionMessage(gpa, "{s} '{s}'", .{ label_role, ename });

    if (std.mem.eql(u8, action, "invoke")) {
        const pat = patternAs(IUIAutomationInvokePattern, &IID_IUIAutomationInvokePattern, el.?, UIA_InvokePatternId);
        if (pat == null) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "{s} is not invokable — use a click", .{label}),
            });
        }
        defer releaseInvoke(pat);
        const hr = vtable(IUIAutomationInvokePatternVtbl, pat.?).Invoke(pat.?);
        if (!succeeded(hr)) {
            host.setOsError(@bitCast(hr));
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "UIA invoke failed on {s}: HRESULT 0x{X}", .{ label, @as(u32, @bitCast(hr)) }),
            });
        }
        return jsonValue(ActionJson{
            .ok = true,
            .message = try actionMessage(gpa, "Invoked {s}", .{label}),
        });
    }

    if (std.mem.eql(u8, action, "set_value")) {
        const pat = patternAs(IUIAutomationValuePattern, &IID_IUIAutomationValuePattern, el.?, UIA_ValuePatternId);
        if (pat == null) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "{s} has no value pattern — click it and type instead", .{label}),
            });
        }
        defer releaseValue(pat);
        const text_bstr = try utf8ToBstr(text);
        defer freeBstr(text_bstr);
        const shr = vtable(IUIAutomationValuePatternVtbl, pat.?).SetValue(pat.?, text_bstr);
        if (!succeeded(shr)) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "UIA set_value failed on {s}: HRESULT 0x{X}", .{ label, @as(u32, @bitCast(shr)) }),
            });
        }
        var got_bstr: BSTR = null;
        const ghr = vtable(IUIAutomationValuePatternVtbl, pat.?).get_CurrentValue(pat.?, &got_bstr);
        defer freeBstr(got_bstr);
        var got: []const u8 = "";
        if (succeeded(ghr)) {
            got = bstrToUtf8(gpa, got_bstr) catch "";
        }
        const okv = std.mem.eql(u8, got, text);
        var role_l_buf: [64]u8 = undefined;
        var name_l_buf: [128]u8 = undefined;
        const role_l = asciiLower(erole, &role_l_buf);
        const name_l = asciiLower(ename, &name_l_buf);
        const is_password = std.mem.indexOf(u8, role_l, "password") != null or std.mem.indexOf(u8, name_l, "password") != null;
        if (is_password and !okv) {
            return jsonValue(ActionJson{
                .ok = true,
                .verified = false,
                .message = try actionMessage(gpa, "Set {s} value (unverified — password fields typically do not read back)", .{label}),
            });
        }
        return jsonValue(ActionJson{
            .ok = true,
            .verified = okv,
            .message = try actionMessage(
                gpa,
                "Set {s} value{s}",
                .{ label, if (okv) ", verified" else ", readback differs" },
            ),
        });
    }

    if (std.mem.eql(u8, action, "toggle")) {
        const pat = patternAs(IUIAutomationTogglePattern, &IID_IUIAutomationTogglePattern, el.?, UIA_TogglePatternId);
        if (pat == null) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "{s} is not toggleable", .{label}),
            });
        }
        defer releaseToggle(pat);
        const thr = vtable(IUIAutomationTogglePatternVtbl, pat.?).Toggle(pat.?);
        if (!succeeded(thr)) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "UIA toggle failed on {s}: HRESULT 0x{X}", .{ label, @as(u32, @bitCast(thr)) }),
            });
        }
        var state_code: c_int = -1;
        const shr = vtable(IUIAutomationTogglePatternVtbl, pat.?).get_CurrentToggleState(pat.?, &state_code);
        const state: []const u8 = if (!succeeded(shr))
            ""
        else switch (state_code) {
            0 => "off",
            1 => "on",
            2 => "indeterminate",
            else => "?",
        };
        return jsonValue(ActionJson{
            .ok = true,
            .message = try actionMessage(gpa, "Toggled {s} → {s}", .{ label, state }),
        });
    }

    if (std.mem.eql(u8, action, "scroll_into_view")) {
        const pat = patternAs(IUIAutomationScrollItemPattern, &IID_IUIAutomationScrollItemPattern, el.?, UIA_ScrollItemPatternId);
        if (pat == null) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "{s} has no scroll-item pattern", .{label}),
            });
        }
        defer releaseScrollItem(pat);
        const shr = vtable(IUIAutomationScrollItemPatternVtbl, pat.?).ScrollIntoView(pat.?);
        if (!succeeded(shr)) {
            return jsonValue(ActionJson{
                .ok = false,
                .message = try actionMessage(gpa, "UIA scroll_into_view failed on {s}: HRESULT 0x{X}", .{ label, @as(u32, @bitCast(shr)) }),
            });
        }
        return jsonValue(ActionJson{
            .ok = true,
            .message = try actionMessage(gpa, "Scrolled {s} into view", .{label}),
        });
    }

    return jsonValue(ActionJson{
        .ok = false,
        .message = try actionMessage(gpa, "Unknown UIA action '{s}'", .{action}),
    });
}

fn asciiLower(text: []const u8, buf: []u8) []const u8 {
    const n = @min(text.len, buf.len);
    for (text[0..n], 0..) |c, i| buf[i] = std.ascii.toLower(c);
    return buf[0..n];
}

// ---------------------------------------------------------------------------
// Unit tests (no live COM required)
// ---------------------------------------------------------------------------

test "control type map covers preferred ids" {
    try std.testing.expectEqualStrings("button", roleName(50000));
    try std.testing.expectEqualStrings("edit", roleName(50004));
    try std.testing.expectEqualStrings("document", roleName(50030));
    try std.testing.expectEqualStrings("", roleName(99999));
    try std.testing.expect(isPreferred(50000));
    try std.testing.expect(!isPreferred(50020));
    try std.testing.expect(isShallowExtra(50020));
}

test "max_elements clamp matches python" {
    const clamp = struct {
        fn go(asked: u32) u32 {
            if (asked == 0) return 80;
            return @max(1, @min(asked, 120));
        }
    }.go;
    try std.testing.expectEqual(@as(u32, 80), clamp(0));
    try std.testing.expectEqual(@as(u32, 5), clamp(5));
    try std.testing.expectEqual(@as(u32, 120), clamp(500));
    try std.testing.expectEqual(@as(u32, 1), clamp(1));
}

test "control json omits offscreen when null" {
    const sample = [_]ControlJson{.{
        .ref = "c1",
        .tag = "button",
        .role = "button",
        .name = "Save",
        .x = 10,
        .y = 20,
        .w = 80,
        .h = 28,
        .hwnd = 101,
        .bounds = .{ .left = 0, .top = 0, .right = 80, .bottom = 28 },
        .uia = true,
        .offscreen = null,
    }};
    const json = try jsonValue(sample[0..]);
    defer allocator.free(json);
    try std.testing.expect(std.mem.indexOf(u8, json, "offscreen") == null);
    try std.testing.expect(std.mem.indexOf(u8, json, "\"uia\":true") != null);

    const off = ControlJson{
        .ref = "c2",
        .tag = "listitem",
        .role = "listitem",
        .name = "Below",
        .x = 0,
        .y = 0,
        .w = 0,
        .h = 0,
        .hwnd = 101,
        .bounds = .{ .left = 0, .top = 0, .right = 0, .bottom = 0 },
        .uia = true,
        .offscreen = true,
    };
    const json2 = try jsonValue(off);
    defer allocator.free(json2);
    try std.testing.expect(std.mem.indexOf(u8, json2, "\"offscreen\":true") != null);
}
