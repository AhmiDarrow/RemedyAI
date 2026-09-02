//go:build windows

package ipc

import (
	"context"
	"fmt"
	"net"
	"strings"

	winio "github.com/Microsoft/go-winio"
	"golang.org/x/sys/windows"
)

func Listen(endpoint string) (net.Listener, error) {
	if !strings.HasPrefix(endpoint, `\\.\pipe\remedy-`) {
		return nil, ErrInvalidEndpoint
	}
	user, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return nil, err
	}
	sddl := fmt.Sprintf("D:P(A;;GA;;;%s)", user.User.Sid.String())
	return winio.ListenPipe(endpoint, &winio.PipeConfig{SecurityDescriptor: sddl, InputBufferSize: 64 * 1024, OutputBufferSize: 64 * 1024})
}

func Dial(ctx context.Context, endpoint string) (net.Conn, error) {
	if !strings.HasPrefix(endpoint, `\\.\pipe\remedy-`) {
		return nil, ErrInvalidEndpoint
	}
	return winio.DialPipeContext(ctx, endpoint)
}
