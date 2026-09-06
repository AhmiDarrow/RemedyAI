package gateway

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGoogleChatRefreshAccessToken(t *testing.T) {
	var gotForm string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		gotForm = string(body)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"access_token": "new-access",
			"expires_in":   3600,
			"token_type":   "Bearer",
		})
	}))
	defer srv.Close()

	ch := NewGoogleChat(nil, GoogleChatConfig{
		AccessToken:  "old-access",
		RefreshToken: "refresh-me",
		ClientID:     "cid",
		ClientSecret: "csecret",
	})
	ch.tokenURL = srv.URL
	ch.client = srv.Client()

	if err := ch.refreshAccessToken(context.Background()); err != nil {
		t.Fatal(err)
	}
	if ch.currentAccessToken() != "new-access" {
		t.Fatalf("token=%q", ch.currentAccessToken())
	}
	if !strings.Contains(gotForm, "grant_type=refresh_token") || !strings.Contains(gotForm, "refresh_token=refresh-me") {
		t.Fatalf("form=%q", gotForm)
	}
}

func TestGoogleChatSendRefreshesOn401(t *testing.T) {
	calls := 0
	mux := http.NewServeMux()
	mux.HandleFunc("/token", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"access_token": "fresh", "expires_in": 3600})
	})
	mux.HandleFunc("/spaces/AAA/messages", func(w http.ResponseWriter, r *http.Request) {
		calls++
		auth := r.Header.Get("Authorization")
		if calls == 1 {
			if auth != "Bearer stale" {
				t.Fatalf("first auth=%q", auth)
			}
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if auth != "Bearer fresh" {
			t.Fatalf("retry auth=%q", auth)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{}`))
	})
	srv := httptest.NewServer(mux)
	defer srv.Close()

	ch := NewGoogleChat(nil, GoogleChatConfig{
		AccessToken:  "stale",
		RefreshToken: "rt",
		ClientID:     "cid",
		ClientSecret: "sec",
		SpaceID:      "AAA",
		AllowAll:     true,
	})
	ch.tokenURL = srv.URL + "/token"
	ch.apiBase = srv.URL
	ch.client = srv.Client()

	ok, err := ch.Send(context.Background(), "hi", "AAA")
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("expected ok after refresh retry")
	}
	if calls != 2 {
		t.Fatalf("calls=%d", calls)
	}
}

func TestGoogleChatCanRefresh(t *testing.T) {
	ch := NewGoogleChat(nil, GoogleChatConfig{RefreshToken: "r", ClientID: "c", ClientSecret: "s"})
	if !ch.canRefresh() {
		t.Fatal("expected canRefresh")
	}
	ch2 := NewGoogleChat(nil, GoogleChatConfig{RefreshToken: "r", ClientID: "c"})
	if ch2.canRefresh() {
		t.Fatal("missing secret")
	}
}
