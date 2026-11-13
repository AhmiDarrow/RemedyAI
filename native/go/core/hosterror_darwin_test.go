package core

import (
	"strings"
	"testing"
)

func TestHostErrorNamesMacOSPermissions(t *testing.T) {
	ax := &HostError{Function: "a11y_snapshot", Status: StatusAccessDenied, OSError: OSErrAccessibility}
	if !strings.Contains(ax.Error(), "Accessibility permission is required") {
		t.Fatalf("accessibility: %s", ax.Error())
	}
	if !strings.Contains(ax.Error(), "Privacy & Security → Accessibility") {
		t.Fatalf("accessibility pane: %s", ax.Error())
	}
	sc := &HostError{Function: "capture_region", Status: StatusAccessDenied, OSError: OSErrScreenRecording}
	if !strings.Contains(sc.Error(), "Screen Recording permission is required") {
		t.Fatalf("screen recording: %s", sc.Error())
	}
	if !strings.Contains(sc.Error(), "Privacy & Security → Screen Recording") {
		t.Fatalf("screen recording pane: %s", sc.Error())
	}
}
