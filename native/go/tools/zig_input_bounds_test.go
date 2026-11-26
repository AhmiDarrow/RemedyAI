package tools

import (
	"context"
	"errors"
	"testing"
)

// Out-of-range coordinates and scroll counts are refused before they reach
// the native input layer, where int32 wrapping moved the pointer to a real
// position, drag maths overflowed, and a huge scroll froze input.
func TestComputerInputRefusesOutOfRangeValues(t *testing.T) {
	cases := []struct {
		name string
		run  func(context.Context, Request) (Result, error)
		in   string
	}{
		{"click wraps", executeComputerClick, `{"x":4294967396,"y":10}`},
		{"move wraps", executeComputerMove, `{"x":10,"y":-4294967196}`},
		{"scroll huge", executeComputerScroll, `{"x":10,"y":10,"dy":2000000000}`},
		{"scroll min int", executeComputerScroll, `{"x":10,"y":10,"dx":-2147483648}`},
		{"drag overflow", executeComputerDrag, `{"x1":-2147483648,"y1":0,"x2":2147483647,"y2":0}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := tc.run(context.Background(), Request{Input: []byte(tc.in)})
			if !errors.Is(err, ErrInvalidInput) {
				t.Fatalf("err = %v, want ErrInvalidInput", err)
			}
		})
	}
}
