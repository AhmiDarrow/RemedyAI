package httpapi

import (
	"net/http"
	"os"
	"strings"
)

// DefaultCORSOrigins cover the Tauri desktop webview and Vite dev server.
var DefaultCORSOrigins = []string{
	"http://localhost:1420",
	"http://127.0.0.1:1420",
	"tauri://localhost",
	"http://tauri.localhost",
	"https://tauri.localhost",
}

func allowedOrigins() map[string]struct{} {
	out := make(map[string]struct{}, len(DefaultCORSOrigins)+8)
	if env := strings.TrimSpace(os.Getenv("REMEDY_CORS_ORIGINS")); env != "" && env != "*" {
		for _, part := range strings.Split(env, ",") {
			if o := strings.TrimSpace(part); o != "" {
				out[o] = struct{}{}
			}
		}
		if len(out) > 0 {
			return out
		}
	}
	for _, o := range DefaultCORSOrigins {
		out[o] = struct{}{}
	}
	return out
}

func withCORS(next http.Handler) http.Handler {
	allowed := allowedOrigins()
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin != "" {
			if _, ok := allowed[origin]; ok {
				w.Header().Set("Access-Control-Allow-Origin", origin)
				w.Header().Set("Access-Control-Allow-Credentials", "true")
				w.Header().Set("Vary", "Origin")
				w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD")
				w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Remedy-Token, X-Remedy-Session-Select")
			}
		}
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
