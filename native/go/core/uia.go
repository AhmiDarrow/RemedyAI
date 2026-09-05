package core

// UIAAvailable reports whether Windows UI Automation can be created on this thread.
// Non-Windows returns (false, nil) when the export is unsupported.
func UIAAvailable() (bool, error) {
	lib, err := Open()
	if err != nil {
		return false, err
	}
	var flag uint8
	status, err := lib.call("remedy_core_uia_available", uint8Ptr(&flag))
	if err != nil {
		return false, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return false, nil
	}
	if err := lib.check("uia_available", st); err != nil {
		return false, err
	}
	return flag != 0, nil
}

// UIAControlSnapshotJSON returns UTF-8 JSON (array or null) from Zig UIA walk.
// hwnd 0 = desktop root. maxElements 0 defaults inside Zig (80). preferredOnly
// keeps the product filter used by the former Python desktop_uia walker.
func UIAControlSnapshotJSON(hwnd uint64, maxElements uint32, preferredOnly bool) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	pref := uint8(0)
	if preferredOnly {
		pref = 1
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_uia_control_snapshot",
		uintptr(hwnd),
		uintptr(maxElements),
		uintptr(pref),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("uia_control_snapshot", int32(status)); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// UIAFocusedElementJSON returns UTF-8 JSON {name,role,value} or the JSON
// literal null when nothing is focused / UIA is unavailable. Windows only;
// other platforms return ErrUnsupported.
func UIAFocusedElementJSON() ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_uia_focused_element",
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return nil, ErrUnsupported
	}
	if err := lib.check("uia_focused_element", st); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// UIAReadWindowTextJSON returns UTF-8 JSON {title,text,fields:[...]} or null.
// hwnd 0 is invalid for a useful read (Zig still accepts it). maxChars 0
// defaults inside Zig. Windows only; other platforms return ErrUnsupported.
func UIAReadWindowTextJSON(hwnd uint64, maxChars uint32) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_uia_read_window_text",
		uintptr(hwnd),
		uintptr(maxChars),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return nil, ErrUnsupported
	}
	if err := lib.check("uia_read_window_text", st); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// UIAElementActionJSON runs invoke|set_value|toggle|scroll_into_view on a
// live element matched by hwnd+name+role. Always returns a JSON object
// {ok,message,verified?}. Windows only; other platforms return ErrUnsupported.
func UIAElementActionJSON(hwnd uint64, name, role, action, text string) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	nameRaw := []byte(name)
	roleRaw := []byte(role)
	actionRaw := []byte(action)
	textRaw := []byte(text)
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_uia_element_action",
		uintptr(hwnd),
		bytesPtr(nameRaw), uintptr(len(nameRaw)),
		bytesPtr(roleRaw), uintptr(len(roleRaw)),
		bytesPtr(actionRaw), uintptr(len(actionRaw)),
		bytesPtr(textRaw), uintptr(len(textRaw)),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return nil, ErrUnsupported
	}
	if err := lib.check("uia_element_action", st); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// A11ySnapshotJSON returns UTF-8 JSON array of AT-SPI clickables (Linux).
// Other platforms return ErrUnsupported (or a HostError with StatusUnsupported).
func A11ySnapshotJSON(limit uint32) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_a11y_snapshot",
		uintptr(limit),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return nil, ErrUnsupported
	}
	if err := lib.check("a11y_snapshot", st); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}
