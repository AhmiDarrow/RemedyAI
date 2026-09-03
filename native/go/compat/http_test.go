package compat

import (
	"bytes"
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

func TestHTTPCompatibilityHandlerPreservesQuery(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/sessions" || r.URL.Query().Get("limit") != "20" {
			t.Errorf("request URI = %q", r.URL.RequestURI())
		}
		_, _ = w.Write([]byte("ok"))
	}))
	defer server.Close()
	handler, err := NewHTTPHandler(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(HTTPRequest{Method: http.MethodGet, Path: "/sessions?limit=20"})
	if _, err := handler.Handle(context.Background(), protocol.Frame{Payload: payload}); err != nil {
		t.Fatal(err)
	}
}

func TestHTTPCompatibilityHandlerRejectsDifferentLoopbackOriginRedirect(t *testing.T) {
	var redirected bool
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { redirected = true }))
	defer target.Close()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL+"/capture", http.StatusTemporaryRedirect)
	}))
	defer origin.Close()
	handler, err := NewHTTPHandler(origin.URL, origin.Client())
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(HTTPRequest{Method: http.MethodPost, Path: "/redirect", Header: map[string]string{"Authorization": "test-only"}, Body: []byte("private")})
	if _, err := handler.Handle(context.Background(), protocol.Frame{Payload: payload}); !errors.Is(err, ErrUnsafeTarget) {
		t.Fatalf("redirect = %v", err)
	}
	if redirected {
		t.Fatal("request reached a different loopback origin")
	}
}

func TestHTTPCompatibilityHandlerRejectsEncodedResponseOverFrameLimit(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write(bytes.Repeat([]byte{'x'}, 13<<20))
	}))
	defer server.Close()
	handler, err := NewHTTPHandler(server.URL, server.Client())
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(HTTPRequest{Method: http.MethodGet, Path: "/large"})
	if _, err := handler.Handle(context.Background(), protocol.Frame{Payload: payload}); !errors.Is(err, protocol.ErrPayloadTooLarge) {
		t.Fatalf("large encoded response = %v", err)
	}
}
