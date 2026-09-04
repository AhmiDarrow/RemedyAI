package gateway

import (
	"crypto/tls"
	"net"
	"time"
)

func tlsDial(d net.Dialer, addr, serverName string) (net.Conn, error) {
	raw, err := d.Dial("tcp", addr)
	if err != nil {
		return nil, err
	}
	cfg := &tls.Config{ServerName: serverName, MinVersion: tls.VersionTLS12}
	tc := tls.Client(raw, cfg)
	_ = tc.SetDeadline(time.Now().Add(d.Timeout))
	if d.Timeout <= 0 {
		_ = tc.SetDeadline(time.Now().Add(15 * time.Second))
	}
	if err := tc.Handshake(); err != nil {
		_ = raw.Close()
		return nil, err
	}
	_ = tc.SetDeadline(time.Time{})
	return tc, nil
}
