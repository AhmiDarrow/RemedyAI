package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"regexp"
	"strconv"
)

var (
	benchmarkLine      = regexp.MustCompile(`^(Benchmark\S+)\s+\d+\s+([0-9.]+)\s+ns/op`)
	benchmarkCPUSuffix = regexp.MustCompile(`-\d+$`)
)

func parseBenchmarkLine(line string) (string, float64, bool) {
	match := benchmarkLine.FindStringSubmatch(line)
	if match == nil {
		return "", 0, false
	}
	value, err := strconv.ParseFloat(match[2], 64)
	if err != nil {
		return "", 0, false
	}
	return benchmarkCPUSuffix.ReplaceAllString(match[1], ""), value, true
}

func main() {
	budgetPath := flag.String("budgets", "../benchmarks/budgets.json", "budget file")
	inputPath := flag.String("input", "../benchmarks/latest.txt", "benchmark output")
	flag.Parse()
	budgetRaw, err := os.ReadFile(*budgetPath)
	if err != nil {
		fatal(err)
	}
	var budgets map[string]float64
	if err := json.Unmarshal(budgetRaw, &budgets); err != nil {
		fatal(err)
	}
	input := os.Stdin
	if *inputPath != "-" {
		input, err = os.Open(*inputPath)
		if err != nil {
			fatal(err)
		}
		defer input.Close()
	}
	seen := make(map[string]bool)
	failed := false
	scanner := bufio.NewScanner(input)
	for scanner.Scan() {
		name, value, ok := parseBenchmarkLine(scanner.Text())
		if !ok {
			continue
		}
		limit, ok := budgets[name]
		if !ok {
			continue
		}
		seen[name] = true
		if value > limit {
			fmt.Fprintf(os.Stderr, "%s %.0f ns/op exceeds %.0f ns/op\n", name, value, limit)
			failed = true
		}
	}
	if err := scanner.Err(); err != nil {
		fatal(err)
	}
	for name := range budgets {
		if !seen[name] {
			fmt.Fprintln(os.Stderr, "missing benchmark", name)
			failed = true
		}
	}
	if failed {
		os.Exit(1)
	}
}
func fatal(err error) { fmt.Fprintln(os.Stderr, err); os.Exit(2) }
