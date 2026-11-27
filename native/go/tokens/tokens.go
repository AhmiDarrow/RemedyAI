// Package tokens counts tokens with a real tokenizer: the o200k_base
// byte-pair encoding from github.com/tiktoken-go/tokenizer (MIT, pure Go,
// vocabulary compiled in, no network).
//
// It is exact for the models that use o200k_base and a close estimate for
// every other provider, whose own tokenizers are not published. What a
// provider bills is whatever its API reports; this count decides when the
// engine compacts a transcript.
package tokens

import (
	"sync"

	"github.com/tiktoken-go/tokenizer/codec"
)

// Method names the encoding the counts come from.
const Method = "o200k_base"

var (
	enc = sync.OnceValue(codec.NewO200kBase)

	// A transcript is counted again on every model round while its earlier
	// messages stay the same, so long texts are remembered.
	mu     sync.Mutex
	memo   = map[string]int{}
	memoSz int
)

const (
	memoMinLen   = 256
	memoMaxBytes = 32 << 20
)

// Count returns how many tokens text is.
func Count(text string) int {
	if text == "" {
		return 0
	}
	if len(text) < memoMinLen {
		return count(text)
	}
	mu.Lock()
	n, ok := memo[text]
	mu.Unlock()
	if ok {
		return n
	}
	n = count(text)
	mu.Lock()
	if memoSz+len(text) > memoMaxBytes {
		memo, memoSz = map[string]int{}, 0
	}
	memo[text] = n
	memoSz += len(text)
	mu.Unlock()
	return n
}

func count(text string) int {
	n, err := enc().Count(text)
	if err != nil {
		// The encoder refuses nothing a string can hold; four bytes a token
		// is the usual rough figure should that ever change.
		return (len(text) + 3) / 4
	}
	return n
}
