package compat

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

func TestHTTPCompatibilityHandlerForwardsOnlyToLoopback(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/health" {
			t.Errorf("path = %q", r.URL.Path)
		}
		w.WriteHeader(http.StatusAccepted)
		_, _ = w.Write([]byte("ready"))
	}))
	defer server.Close()
	handler, err := NewHTTPHandler(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(HTTPRequest{Method: http.MethodGet, Path: "/health"})
	frames, err := handler.Handle(context.Background(), protocol.Frame{Kind: protocol.KindToolRequest, Payload: payload})
	if err != nil {
		t.Fatal(err)
	}
	var response HTTPResponse
	if err := json.Unmarshal(frames[0].Payload, &response); err != nil {
		t.Fatal(err)
	}
	if response.Status != http.StatusAccepted || string(response.Body) != "ready" {
		t.Fatalf("response = %#v", response)
	}
	if _, err := NewHTTPHandler("https://example.com", nil); !errors.Is(err, ErrUnsafeTarget) {
		t.Fatalf("remote target = %v", err)
	}
	bad, _ := json.Marshal(HTTPRequest{Method: http.MethodGet, Path: "//example.com/escape"})
	if _, err := handler.Handle(context.Background(), protocol.Frame{Payload: bad}); !errors.Is(err, ErrUnsafeTarget) {
		t.Fatalf("unsafe path = %v", err)
	}
}

func TestHTTPCompatibilityHandlerRejectsExternalRedirect(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Redirect(w, &http.Request{}, "https://example.com/escape", http.StatusFound)
	}))
	defer server.Close()
	handler, err := NewHTTPHandler(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(HTTPRequest{Method: http.MethodGet, Path: "/redirect"})
	if _, err := handler.Handle(context.Background(), protocol.Frame{Payload: payload}); !errors.Is(err, ErrUnsafeTarget) {
		t.Fatalf("redirect = %v", err)
	}
}
