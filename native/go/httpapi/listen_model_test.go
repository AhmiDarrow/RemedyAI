package httpapi

import (
	"context"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestResolveListenModelFallsBackToScripted(t *testing.T) {
	home := t.TempDir()
	model := ResolveListenModel(home)
	ch, err := model.Stream(context.Background(), cognition.Turn{Goal: "hi", Iteration: 1})
	if err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for ev := range ch {
		b.WriteString(ev.Text)
	}
	if b.String() != "Hello world" {
		t.Fatalf("got %q", b.String())
	}
}
