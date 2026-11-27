package httpapi

import (
	"context"
	"regexp"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// Privacy mode (Settings, System, Privacy): email addresses and phone numbers
// in what a tool returns are replaced before the model reads them. The tool
// itself still works on the real text; only the copy sent to the provider
// changes. It is pattern matching, so it misses a number written in words
// and can blank a figure that only looks like a phone number.

var (
	emailPattern = regexp.MustCompile(`[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+`)
	// +49 30 1234567, (555) 123-4567, 555-123-4567, 030 1234 5678. A run of
	// digits with no separators (an id, a hash, a timestamp) is left alone
	// unless it starts with +.
	phonePattern = regexp.MustCompile(
		`\+\d[\d\s().\-]{6,18}\d|\(?\b\d{2,4}\)?[\s.\-]\d{3,4}[\s.\-]\d{3,5}\b`)
)

const (
	redactedEmail = "[email removed]"
	redactedPhone = "[phone removed]"
)

// redactPersonal replaces email addresses and phone numbers in text.
func redactPersonal(text string) string {
	if text == "" {
		return text
	}
	text = emailPattern.ReplaceAllString(text, redactedEmail)
	return phonePattern.ReplaceAllString(text, redactedPhone)
}

// redactingTools runs the wrapped tools and redacts what they return.
type redactingTools struct {
	inner cognition.ToolExecutor
}

func (t *redactingTools) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	res := t.inner.Execute(ctx, call)
	if len(res.Output) > 0 {
		res.Output = []byte(redactPersonal(string(res.Output)))
	}
	res.Err = redactPersonal(res.Err)
	for i := range res.Blocks {
		res.Blocks[i].Text = redactPersonal(res.Blocks[i].Text)
	}
	return res
}
