package httpapi

import (
	"context"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestRedactPersonalRemovesEmailsAndPhones(t *testing.T) {
	in := "Write to test.user@example.org or call +49 30 1234567, (555) 123-4567 or 555-123-4567."
	got := redactPersonal(in)
	for _, leak := range []string{"example.org", "1234567", "123-4567"} {
		if strings.Contains(got, leak) {
			t.Fatalf("%q still in %q", leak, got)
		}
	}
	if strings.Count(got, redactedEmail) != 1 || strings.Count(got, redactedPhone) != 3 {
		t.Fatalf("got %q", got)
	}
}

func TestRedactPersonalLeavesOrdinaryNumbers(t *testing.T) {
	for _, keep := range []string{
		"commit 8f3a2c1d9e7b4a6c5d0e", "port 127.0.0.1:7400", "1732665600", "v2.0.0",
		"2026-11-26 21:21:01", "lines 1384-1540", "3.14159", "size 16000000 bytes",
	} {
		if got := redactPersonal(keep); got != keep {
			t.Fatalf("%q became %q", keep, got)
		}
	}
}

type fixedTool struct{ res cognition.ToolResult }

func (f fixedTool) Execute(context.Context, cognition.ToolCall) cognition.ToolResult { return f.res }

func TestRedactingToolsCoversOutputErrorAndBlocks(t *testing.T) {
	tools := &redactingTools{inner: fixedTool{res: cognition.ToolResult{
		Output: []byte("mail a@b.co"), Err: "no route to a@b.co",
		Blocks: []cognition.Block{{Type: cognition.BlockText, Text: "a@b.co"}},
	}}}
	res := tools.Execute(context.Background(), cognition.ToolCall{})
	if strings.Contains(string(res.Output)+res.Err+res.Blocks[0].Text, "a@b.co") {
		t.Fatalf("address left in %+v", res)
	}
}
