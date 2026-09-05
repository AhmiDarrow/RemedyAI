package core

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
	"unsafe"
)

// Capture is raw BGR/BGRA pixel rows from a capture call.
type Capture struct {
	Pixels []byte
	Stride int
	Width  int
	Height int
	Left   int
	Top    int
}

func unsafePtrPtr(p *uintptr) uintptr {
	return uintptr(unsafe.Pointer(p))
}

// VirtualScreenRect returns virtual screen origin and size in physical pixels.
func VirtualScreenRect() (left, top, width, height int32, err error) {
	lib, err := Open()
	if err != nil {
		return 0, 0, 0, 0, err
	}
	status, err := lib.call(
		"remedy_core_virtual_screen_rect",
		int32Ptr(&left), int32Ptr(&top), int32Ptr(&width), int32Ptr(&height),
	)
	if err != nil {
		return 0, 0, 0, 0, err
	}
	if err := lib.check("virtual_screen_rect", int32(status)); err != nil {
		return 0, 0, 0, 0, err
	}
	return left, top, width, height, nil
}

// ListMonitorsJSON returns UTF-8 JSON array of monitor descriptors from Zig.
// Fail-closed when remedy_core is missing or the host export is unsupported.
func ListMonitorsJSON() ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call("remedy_core_list_monitors", unsafePtrPtr(&ptr), sizePtr(&length))
	if err != nil {
		return nil, err
	}
	if err := lib.check("list_monitors", int32(status)); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// ListWindowsJSON returns UTF-8 JSON array of visible titled windows (Zig).
// limit 0 defaults to 50 inside the Tool ABI wrapper; Zig accepts any limit.
func ListWindowsJSON(limit uint32) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_list_windows",
		uintptr(limit),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("list_windows", int32(status)); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// CaptureVirtualScreen captures the virtual screen (bytesPerPixel 3=BGR or 4=BGRA).
func CaptureVirtualScreen(bytesPerPixel uint32) (*Capture, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr uintptr
	var length, stride uintptr
	var width, height, left, top int32
	status, err := lib.call(
		"remedy_core_capture_virtual_screen",
		uintptr(bytesPerPixel),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
		int32Ptr(&width), int32Ptr(&height),
		sizePtr(&stride),
		int32Ptr(&left), int32Ptr(&top),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("capture_virtual_screen", int32(status)); err != nil {
		return nil, err
	}
	pixels := takeBytes(lib, ptr, length)
	return &Capture{
		Pixels: pixels,
		Stride: int(stride),
		Width:  int(width),
		Height: int(height),
		Left:   int(left),
		Top:    int(top),
	}, nil
}

// CaptureRegion captures a rectangle in screen coordinates.
func CaptureRegion(left, top, width, height int32, bytesPerPixel uint32) (*Capture, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr uintptr
	var length, stride uintptr
	status, err := lib.call(
		"remedy_core_capture_region",
		uintptr(left), uintptr(top),
		uintptr(width), uintptr(height),
		uintptr(bytesPerPixel),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
		sizePtr(&stride),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("capture_region", int32(status)); err != nil {
		return nil, err
	}
	pixels := takeBytes(lib, ptr, length)
	return &Capture{
		Pixels: pixels,
		Stride: int(stride),
		Width:  int(width),
		Height: int(height),
		Left:   int(left),
		Top:    int(top),
	}, nil
}

// EncodePNG encodes BGR/BGRA rows to PNG bytes.
func EncodePNG(pixels []byte, width, height int, stride int, bytesPerPixel uint32) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr uintptr
	var length uintptr
	status, err := lib.call(
		"remedy_core_encode_png",
		bytesPtr(pixels), uintptr(len(pixels)),
		uintptr(uint32(width)), uintptr(uint32(height)),
		uintptr(stride), uintptr(bytesPerPixel),
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("encode_png", int32(status)); err != nil {
		return nil, err
	}
	return takeBytes(lib, ptr, length), nil
}

// ScreenshotPNG captures the virtual screen to a PNG file under home/computer/shots.
func ScreenshotPNG(homeDir, label string) (map[string]any, error) {
	shot, err := CaptureVirtualScreen(3)
	if err != nil {
		return nil, err
	}
	png, err := EncodePNG(shot.Pixels, shot.Width, shot.Height, shot.Stride, 3)
	if err != nil {
		return nil, err
	}
	path, err := writeShot(homeDir, label, png)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"path":   path,
		"width":  shot.Width,
		"height": shot.Height,
		"origin": map[string]any{"x": shot.Left, "y": shot.Top},
	}, nil
}

// ScreenshotRegionPNG captures a CSS/logical region (scale = devicePixelRatio).
func ScreenshotRegionPNG(homeDir, label string, x, y, width, height int, scale float64) (map[string]any, error) {
	sc := scale
	if sc <= 0 {
		sc = 1
	}
	rx := int(float64(x) * sc)
	ry := int(float64(y) * sc)
	rw := int(float64(width) * sc)
	rh := int(float64(height) * sc)
	if rw < 1 {
		rw = 1
	}
	if rh < 1 {
		rh = 1
	}
	originX, originY, fullW, fullH, err := VirtualScreenRect()
	if err != nil {
		return nil, err
	}
	bx := rx - int(originX)
	by := ry - int(originY)
	if bx < 0 {
		rw += bx
		bx = 0
	}
	if by < 0 {
		rh += by
		by = 0
	}
	if bx >= int(fullW) || by >= int(fullH) || rw <= 0 || rh <= 0 {
		return nil, fmt.Errorf("region outside virtual screen")
	}
	if bx+rw > int(fullW) {
		rw = int(fullW) - bx
	}
	if by+rh > int(fullH) {
		rh = int(fullH) - by
	}
	crop, err := CaptureRegion(originX+int32(bx), originY+int32(by), int32(rw), int32(rh), 3)
	if err != nil {
		return nil, err
	}
	png, err := EncodePNG(crop.Pixels, crop.Width, crop.Height, crop.Stride, 3)
	if err != nil {
		return nil, err
	}
	path, err := writeShot(homeDir, label, png)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"path":   path,
		"width":  crop.Width,
		"height": crop.Height,
		"origin": map[string]any{"x": int(originX) + bx, "y": int(originY) + by},
		"requested": map[string]any{
			"x": x, "y": y, "width": width, "height": height, "scale": sc,
		},
	}, nil
}

func writeShot(homeDir, label string, png []byte) (string, error) {
	if homeDir == "" {
		return "", fmt.Errorf("home required for screenshot")
	}
	if label == "" {
		label = "capture"
	}
	dir := filepath.Join(homeDir, "computer", "shots")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	name := fmt.Sprintf("%s_%d.png", sanitizeLabel(label), time.Now().UnixNano())
	path := filepath.Join(dir, name)
	if err := os.WriteFile(path, png, 0o600); err != nil {
		return "", err
	}
	return path, nil
}

func sanitizeLabel(s string) string {
	out := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		c := s[i]
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-' || c == '_' {
			out = append(out, c)
		} else if c == ' ' {
			out = append(out, '_')
		}
	}
	if len(out) == 0 {
		return "capture"
	}
	if len(out) > 40 {
		out = out[:40]
	}
	return string(out)
}

// Mouse button constants matching remedy_core_mouse_button.
const (
	MouseLeft   uint32 = 0
	MouseRight  uint32 = 1
	MouseMiddle uint32 = 2
)

// MouseClick moves to (x, y) then presses/releases via Zig SendInput / X11.
// Coordinates are virtual-screen physical pixels. Fail-closed without remedy_core.
func MouseClick(x, y int32, button, clicks uint32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	if clicks == 0 {
		clicks = 1
	}
	status, err := lib.call(
		"remedy_core_mouse_click",
		uintptr(x), uintptr(y), uintptr(button), uintptr(clicks),
	)
	if err != nil {
		return err
	}
	return lib.check("mouse_click", int32(status))
}

// TypeText types UTF-8 text via Zig KEYEVENTF_UNICODE / XTest.
// Fail-closed without remedy_core.
func TypeText(text string, perCharDelayMS uint32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	raw := []byte(text)
	status, err := lib.call(
		"remedy_core_type_text",
		bytesPtr(raw), uintptr(len(raw)), uintptr(perCharDelayMS),
	)
	if err != nil {
		return err
	}
	return lib.check("type_text", int32(status))
}

// MouseMove moves the pointer to virtual-screen physical pixels via Zig.
// Fail-closed without remedy_core.
func MouseMove(x, y int32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_mouse_move", uintptr(x), uintptr(y))
	if err != nil {
		return err
	}
	return lib.check("mouse_move", int32(status))
}

// MouseDrag presses at (x1,y1), interpolates through steps, releases at (x2,y2).
// Fail-closed without remedy_core.
func MouseDrag(x1, y1, x2, y2 int32, steps uint32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	if steps == 0 {
		steps = 12
	}
	status, err := lib.call(
		"remedy_core_mouse_drag",
		uintptr(x1), uintptr(y1), uintptr(x2), uintptr(y2), uintptr(steps),
	)
	if err != nil {
		return err
	}
	return lib.check("mouse_drag", int32(status))
}

// MouseScroll moves to (x,y) then scrolls; dy>0 up, dx>0 right (notch units).
// Fail-closed without remedy_core.
func MouseScroll(x, y, dx, dy int32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call(
		"remedy_core_mouse_scroll",
		uintptr(x), uintptr(y), uintptr(dx), uintptr(dy),
	)
	if err != nil {
		return err
	}
	return lib.check("mouse_scroll", int32(status))
}

// ClipboardGetText returns CF_UNICODETEXT / X11 CLIPBOARD as UTF-8.
// Empty clipboard yields "". Fail-closed without remedy_core.
func ClipboardGetText() (string, error) {
	lib, err := Open()
	if err != nil {
		return "", err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_clipboard_get_text",
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return "", err
	}
	if err := lib.check("clipboard_get_text", int32(status)); err != nil {
		return "", err
	}
	raw := takeBytes(lib, ptr, length)
	if len(raw) == 0 {
		return "", nil
	}
	return string(raw), nil
}

// ClipboardSetText replaces the OS text clipboard with UTF-8 contents.
// Fail-closed without remedy_core.
func ClipboardSetText(text string) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	raw := []byte(text)
	status, err := lib.call(
		"remedy_core_clipboard_set_text",
		bytesPtr(raw), uintptr(len(raw)),
	)
	if err != nil {
		return err
	}
	return lib.check("clipboard_set_text", int32(status))
}

