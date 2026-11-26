package httpapi

import (
	"bufio"
	"fmt"
	"net"
	"net/http"
	"testing"
	"time"
)

// The webhook origin is what the tunnel publishes. A sender that sends the
// headers and then stalls the body is answered and dropped, not held open.
func TestWebhookOriginDropsStalledBody(t *testing.T) {
	prev := webhookBodyTimeout
	webhookBodyTimeout = 200 * time.Millisecond
	t.Cleanup(func() { webhookBodyTimeout = prev })

	s, _ := newConnectTestServer(t)
	origin, err := s.startWebhookOrigin()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(origin.close)

	for _, tc := range []struct {
		path string
		want int
	}{
		{"/api/webhooks/whatsapp", http.StatusRequestTimeout},
		{"/api/status", http.StatusNotFound},
	} {
		conn, err := net.Dial("tcp", fmt.Sprintf("127.0.0.1:%d", origin.port))
		if err != nil {
			t.Fatal(err)
		}
		_, _ = fmt.Fprintf(conn, "POST %s HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{", tc.path)
		_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
		resp, err := http.ReadResponse(bufio.NewReader(conn), nil)
		if err != nil {
			_ = conn.Close()
			t.Fatalf("%s: stalled body held the connection open: %v", tc.path, err)
		}
		_ = resp.Body.Close()
		_ = conn.Close()
		if resp.StatusCode != tc.want {
			t.Fatalf("%s: status=%d, want %d", tc.path, resp.StatusCode, tc.want)
		}
	}
}
