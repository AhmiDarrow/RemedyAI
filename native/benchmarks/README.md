# Native performance scorecard

Run correctness gates first, then capture the native microbenchmark scorecard:

```text
cd native/go
go test -run=^$ -bench=. -benchmem ./benchmarks > ../benchmarks/latest.txt
go run ./cmd/benchcheck -budgets ../benchmarks/budgets.json -input ../benchmarks/latest.txt
```

`latest.txt` is local evidence and is intentionally gitignored. Budgets are broad
catastrophic-regression ceilings, not promises of end-to-end task latency. Product claims
require a separate controlled comparison of startup, complete tool paths, model streaming,
CPU, and resident memory against the Python compatibility path.

Zig filesystem/process primitives are exercised separately with the same fixtures because
the production Go-to-Zig adapter remains behind the Phase 17 cutover gate.
