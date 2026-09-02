package main

import "testing"

func TestParseBenchmarkLine(t *testing.T) {
	tests := []struct {
		name string
		line string
		want string
	}{
		{
			name: "cpu suffix",
			line: "BenchmarkIPCRoundTrip-20             20  18960 ns/op  942 B/op  15 allocs/op",
			want: "BenchmarkIPCRoundTrip",
		},
		{
			name: "nested benchmark",
			line: "BenchmarkDispatch/validated-8          100  8610.5 ns/op",
			want: "BenchmarkDispatch/validated",
		},
		{
			name: "no cpu suffix",
			line: "BenchmarkSchedulerTick                100  615.0 ns/op",
			want: "BenchmarkSchedulerTick",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, value, ok := parseBenchmarkLine(test.line)
			if !ok {
				t.Fatal("benchmark line was not parsed")
			}
			if got != test.want {
				t.Fatalf("name = %q, want %q", got, test.want)
			}
			if value <= 0 {
				t.Fatalf("value = %v, want positive", value)
			}
		})
	}
}

func TestParseBenchmarkLineRejectsNonBenchmarkOutput(t *testing.T) {
	if _, _, ok := parseBenchmarkLine("PASS"); ok {
		t.Fatal("non-benchmark output was accepted")
	}
}
