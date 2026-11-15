package secret

import "errors"

// ErrPeerUnsupported means this OS has no reliable way here to map a TCP
// peer to its owning user; callers fall back to a one-time bootstrap link.
var ErrPeerUnsupported = errors.New("peer user lookup unsupported on this OS")
