package httpapi

import (
	"errors"
	"net"
	"net/http"
	"net/netip"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// errPeerUnsupported: see secret.ErrPeerUnsupported.
var errPeerUnsupported = secret.ErrPeerUnsupported

// connEndpoints returns the client (remote) and server (local) address of the
// TCP connection that carried r.
func connEndpoints(r *http.Request) (client, server netip.AddrPort, err error) {
	client, err = netip.ParseAddrPort(r.RemoteAddr)
	if err != nil {
		return client, server, err
	}
	la, ok := r.Context().Value(http.LocalAddrContextKey).(net.Addr)
	if !ok || la == nil {
		return client, server, errors.New("no local address on request")
	}
	server, err = netip.ParseAddrPort(la.String())
	return unmapPort(client), unmapPort(server), err
}

func unmapPort(ap netip.AddrPort) netip.AddrPort {
	return netip.AddrPortFrom(ap.Addr().Unmap(), ap.Port())
}

// peerIsSameUser reports whether the process on the other end of r's loopback
// connection runs as the same OS user as this server. err is non-nil when the
// answer is unknown (unsupported OS, lookup failed); callers must not treat
// that as "same user".
var peerIsSameUser = func(r *http.Request) (bool, error) {
	client, server, err := connEndpoints(r)
	if err != nil {
		return false, err
	}
	return secret.SameUserPeer(client, server)
}
