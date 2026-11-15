//go:build !linux && !windows

package secret

import "net/netip"

// macOS and the BSDs have no stable, unprivileged API in pure Go that maps a
// TCP peer to its uid; bootstrap there needs a one-time link instead.
func SameUserPeer(client, server netip.AddrPort) (bool, error) {
	return false, ErrPeerUnsupported
}
