package tokens

import (
	"strings"
	"testing"
)

func TestCountIsTheEncodingsCount(t *testing.T) {
	// Known o200k_base counts.
	for text, want := range map[string]int{
		"":              0,
		"hello world":   2,
		"Hello, world!": 4,
	} {
		if got := Count(text); got != want {
			t.Fatalf("Count(%q) = %d, want %d", text, got, want)
		}
	}
}

func TestLongTextIsRememberedAndStable(t *testing.T) {
	long := strings.Repeat("The quick brown fox jumps over the lazy dog. ", 40)
	first := Count(long)
	if first < 300 || first > 500 {
		t.Fatalf("count %d is not near ten tokens a sentence", first)
	}
	if again := Count(long); again != first {
		t.Fatalf("second count %d != first %d", again, first)
	}
	mu.Lock()
	_, kept := memo[long]
	mu.Unlock()
	if !kept {
		t.Fatal("a long text was not remembered")
	}
}
