package benchmarks

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"path/filepath"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/events"
	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/memory"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
	remedyruntime "github.com/AhmiDarrow/RemedyAI/native/go/runtime"
	"github.com/AhmiDarrow/RemedyAI/native/go/scheduler"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func BenchmarkFrame1KiBRoundTrip(b *testing.B) {
	frame := protocol.Frame{Kind: protocol.KindToolResult, Payload: make([]byte, 1024)}
	b.ReportAllocs()
	for b.Loop() {
		raw, _ := frame.MarshalBinary()
		_, _ = protocol.Parse(raw)
	}
}
func BenchmarkRuntimeStartStop(b *testing.B) {
	for b.Loop() {
		runtime := remedyruntime.New()
		_ = runtime.Start(context.Background())
		_ = runtime.Stop()
	}
}
func BenchmarkIPCRoundTrip(b *testing.B) {
	server, clientConn := net.Pipe()
	go ipc.ServeConn(context.Background(), server, ipc.HandlerFunc(func(_ context.Context, request protocol.Frame) ([]protocol.Frame, error) {
		return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: request.Payload}}, nil
	}))
	client := ipc.NewClient(clientConn)
	defer client.Close()
	b.ReportAllocs()
	var n uint64
	for b.Loop() {
		n++
		var id [16]byte
		for i := 0; i < 8; i++ {
			id[i] = byte(n >> (8 * i))
		}
		_, _ = client.Call(context.Background(), protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: id, Payload: []byte("ping")})
	}
}
func BenchmarkToolDispatchValidated(b *testing.B) {
	schema := json.RawMessage(`{"type":"object","properties":{"value":{"type":"integer"}},"required":["value"],"additionalProperties":false}`)
	registry := tools.NewRegistry()
	_ = registry.Register(tools.Descriptor{ID: "bench", Version: 1, Description: "benchmark", Runtime: tools.RuntimeGo, InputSchema: schema, OutputSchema: schema}, tools.ExecutorFunc(func(_ context.Context, request tools.Request) (tools.Result, error) {
		return tools.Result{Output: request.Input}, nil
	}))
	request := tools.Request{ToolID: "bench", Version: 1, Input: json.RawMessage(`{"value":1}`)}
	b.ReportAllocs()
	for b.Loop() {
		_, _ = registry.Execute(context.Background(), request)
	}
}
func BenchmarkMemorySearch1000(b *testing.B) {
	store, _ := memory.Open(filepath.Join(b.TempDir(), "memory.log"))
	defer store.Close()
	for i := 0; i < 1000; i++ {
		_ = store.Append(memory.Record{ID: fmt.Sprint(i), Namespace: "bench", Kind: memory.Semantic, Key: fmt.Sprintf("key-%d", i), Content: "native retrieval benchmark", CreatedAt: time.Unix(int64(i), 0)})
	}
	query := memory.Query{Namespace: "bench", Text: "retrieval", Limit: 20}
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		_ = store.Search(query)
	}
}
func BenchmarkEventPublishDurable(b *testing.B) {
	bus, _ := events.Open(filepath.Join(b.TempDir(), "events.log"))
	defer bus.Close()
	b.ReportAllocs()
	b.ResetTimer()
	for b.Loop() {
		_, _ = bus.Publish(events.Event{Type: "Benchmark", Source: "test", Data: json.RawMessage(`{"ok":true}`)})
	}
}
func BenchmarkSchedulerTick(b *testing.B) {
	now := time.Unix(1, 0)
	s := scheduler.New(scheduler.ExecutorFunc(func(context.Context, scheduler.Job) error { return nil }), func() time.Time { return now })
	_ = s.Add(scheduler.Job{ID: "recurring", Trigger: scheduler.Recurring, Interval: time.Nanosecond})
	b.ReportAllocs()
	for b.Loop() {
		now = now.Add(time.Nanosecond)
		_ = s.Tick(context.Background(), now)
	}
}

type completeModel struct{}

func (completeModel) Stream(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error) {
	ch := make(chan cognition.ModelEvent, 1)
	ch <- cognition.ModelEvent{Text: "done", Done: true}
	close(ch)
	return ch, nil
}

type noTools struct{}

func (noTools) Execute(context.Context, cognition.ToolCall) cognition.ToolResult {
	return cognition.ToolResult{}
}

type allow struct{}

func (allow) Decide(context.Context, cognition.ToolCall) cognition.Decision { return cognition.Allow }
func BenchmarkReActCompleteTurn(b *testing.B) {
	engine := cognition.Engine{Model: completeModel{}, Tools: noTools{}, Policy: allow{}, Now: func() time.Time { return time.Unix(1, 0) }}
	b.ReportAllocs()
	for b.Loop() {
		_ = engine.Run(context.Background(), "goal")
	}
}
