package httpapi

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

type pausedLLMBody struct {
	reader  io.Reader
	entered chan struct{}
	resume  chan struct{}
	once    sync.Once
}

func (b *pausedLLMBody) Read(p []byte) (int, error) {
	b.once.Do(func() { close(b.entered); <-b.resume })
	return b.reader.Read(p)
}

func TestSessionLLMReservesSessionUntilResponse(t *testing.T) {
	for _, body := range []string{`{"provider":"openai","model":"gpt-4o-mini"}`, `{invalid`} {
		t.Run(body, func(t *testing.T) {
			s, _ := newSessionLLMTestServer(t)
			sid := createTestSession(t, s)
			paused := &pausedLLMBody{reader: strings.NewReader(body), entered: make(chan struct{}), resume: make(chan struct{})}
			req := httptest.NewRequest(http.MethodPut, "/api/sessions/"+sid+"/llm", paused)
			req.Header.Set("Authorization", "Bearer "+sessionLLMTestToken)
			req.Host = "127.0.0.1:7400"
			req.RemoteAddr = "127.0.0.1:12345"
			rr := httptest.NewRecorder()
			done := make(chan struct{})
			go func() { s.Handler().ServeHTTP(rr, req); close(done) }()
			defer func() {
				close(paused.resume)
				select {
				case <-done:
				case <-time.After(5 * time.Second):
					t.Error("model change did not finish")
				}
				if s.claims.IsClaimed(sid) {
					t.Error("model change leaked its session reservation")
				}
			}()
			select {
			case <-paused.entered:
			case <-done:
				t.Fatalf("handler returned before reading body: %d", rr.Code)
			case <-time.After(5 * time.Second):
				t.Fatal("handler did not read body")
			}
			if epoch, _, ok := s.claims.TryClaim(sid); ok {
				s.claims.Release(sid, &epoch)
				t.Error("a stream could start while the model was changing")
			}
			if epoch, _, ok := s.claims.TryClaim("other-session"); !ok {
				t.Error("model change blocked an unrelated session")
			} else {
				s.claims.Release("other-session", &epoch)
			}
		})
	}
}
