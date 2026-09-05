package httpapi

import "testing"

func TestLooksLikePathTitle(t *testing.T) {
	paths := []string{
		`C:\Users\me\Pictures\shot.png`,
		`D:/projects/remedy/notes.md`,
		`\\fileserver\share\report.pdf`,
		"/Users/me/Desktop/thing.png",
		"/home/me/downloads/scan.pdf",
		`some\folder\invoice.pdf`,
		`deep\path\photo.JPEG`,
		"Screenshot 2026-08-19 143022.png",
	}
	for _, text := range paths {
		if !looksLikePathTitle(text) {
			t.Fatalf("expected path title: %q", text)
		}
	}
	ordinary := []string{
		"",
		"   ",
		"help me plan the week",
		"what is 2 + 2",
		"Screenshot the login page for me",
		"notes.md",
		"screenshot_final.webp",
		"C: is nearly full",
		"read /etc/hosts and tell me what you see",
	}
	for _, text := range ordinary {
		if looksLikePathTitle(text) {
			t.Fatalf("expected ordinary message: %q", text)
		}
	}
}

func TestTitleFromAttachmentName(t *testing.T) {
	cases := []struct {
		name string
		want string
	}{
		{`C:\Users\me\Pictures\holiday_photo.png`, "holiday photo"},
		{"/home/me/quarterly-report.pdf", "quarterly report.pdf"},
		{"my_notes.md", "my notes.md"},
		{"invoice-2026.jpeg", "invoice 2026"},
		{"multi__under___scores.png", "multi under scores"},
		{"  spaced   out.webp  ", "spaced out"},
		{"sunset.HEIC", "sunset"},
	}
	for _, tc := range cases {
		if got := titleFromAttachmentName(tc.name, 52); got != tc.want {
			t.Fatalf("titleFromAttachmentName(%q)=%q want %q", tc.name, got, tc.want)
		}
	}
}

func TestTitleFromPrompt(t *testing.T) {
	if got := titleFromPrompt("plan my week around the dentist", 52, nil); got != "plan my week around the dentist" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt("plan   my\n\nweek", 52, nil); got != "plan my week" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt("", 52, nil); got != "New Session" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt(`C:\Users\me\Pictures\holiday_photo.png`, 52, nil); got != "holiday photo" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt("look at this 📎 shot.png (1.2 MB)", 52, nil); got != "look at this" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt("(see attached)", 52, nil); got != "Attachments" {
		t.Fatalf("got %q", got)
	}
	if got := titleFromPrompt("(See Attached)", 52, []map[string]any{{"name": "scan.png"}}); got != "scan" {
		t.Fatalf("got %q", got)
	}
}

func TestShouldRefreshLivingTitle(t *testing.T) {
	if !shouldRefreshLivingTitle("New Session", "update catalog, we have guitarremedy now") {
		t.Fatal("expected refresh from placeholder")
	}
	if shouldRefreshLivingTitle("Oracle pack is ready", "ok") {
		t.Fatal("ack must not retitle")
	}
	if shouldRefreshLivingTitle("Oracle pack is ready", "continue") {
		t.Fatal("continue must not retitle")
	}
	if shouldRefreshLivingTitle("Oracle pack is ready", "Hi reme") {
		t.Fatal("short greeting must not retitle")
	}
}
