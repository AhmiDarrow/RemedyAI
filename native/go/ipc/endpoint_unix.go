//go:build !windows

package ipc

import (
	"context"
	"net"
	"os"
	"path/filepath"
	"strings"
)

type unixListener struct {
	net.Listener
	path string
}

func (l *unixListener) Close() error { err := l.Listener.Close(); _ = os.Remove(l.path); return err }

func Listen(endpoint string) (net.Listener, error) {
	if !filepath.IsAbs(endpoint) || !strings.Contains(filepath.Base(endpoint), "remedy-") {
		return nil, ErrInvalidEndpoint
	}
	if info, err := os.Lstat(endpoint); err == nil {
		if info.Mode()&os.ModeSocket == 0 {
			return nil, ErrInvalidEndpoint
		}
		if err := os.Remove(endpoint); err != nil {
			return nil, err
		}
	} else if !os.IsNotExist(err) {
		return nil, err
	}
	listener, err := net.Listen("unix", endpoint)
	if err != nil {
		return nil, err
	}
	if err := os.Chmod(endpoint, 0o600); err != nil {
		listener.Close()
		return nil, err
	}
	return &unixListener{Listener: listener, path: endpoint}, nil
}

func Dial(ctx context.Context, endpoint string) (net.Conn, error) {
	if !filepath.IsAbs(endpoint) || !strings.Contains(filepath.Base(endpoint), "remedy-") {
		return nil, ErrInvalidEndpoint
	}
	return (&net.Dialer{}).DialContext(ctx, "unix", endpoint)
}
