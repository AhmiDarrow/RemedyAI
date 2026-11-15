//go:build linux

package secret

import (
	"bufio"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"os"
	"strconv"
	"strings"
)

// sameUserPeer finds the client's socket in /proc/net/tcp{,6} (local = client
// endpoint, remote = server endpoint) and compares its owner uid with ours.
func SameUserPeer(client, server netip.AddrPort) (bool, error) {
	for _, path := range []string{"/proc/net/tcp", "/proc/net/tcp6"} {
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		uid, found, err := findSocketUID(f, client, server)
		_ = f.Close()
		if err != nil {
			return false, err
		}
		if found {
			return uid == uint32(os.Getuid()), nil
		}
	}
	return false, errors.New("peer socket not found in /proc/net/tcp")
}

// findSocketUID scans a /proc/net/tcp-format table for the socket whose local
// address is local and remote address is remote, returning its uid.
func findSocketUID(r io.Reader, local, remote netip.AddrPort) (uint32, bool, error) {
	sc := bufio.NewScanner(r)
	first := true
	for sc.Scan() {
		if first {
			first = false
			continue // header
		}
		fields := strings.Fields(sc.Text())
		if len(fields) < 8 {
			continue
		}
		la, err := parseProcAddr(fields[1])
		if err != nil || la != local {
			continue
		}
		ra, err := parseProcAddr(fields[2])
		if err != nil || ra != remote {
			continue
		}
		uid, err := strconv.ParseUint(fields[7], 10, 32)
		if err != nil {
			return 0, false, fmt.Errorf("bad uid %q", fields[7])
		}
		return uint32(uid), true, nil
	}
	return 0, false, sc.Err()
}

// parseProcAddr decodes "0100007F:1F48" (IPv4) or a 32-hex-digit IPv6 form.
// The kernel prints each 32-bit word of the address in host byte order.
func parseProcAddr(s string) (netip.AddrPort, error) {
	host, portHex, ok := strings.Cut(s, ":")
	if !ok {
		return netip.AddrPort{}, errors.New("no port")
	}
	port, err := strconv.ParseUint(portHex, 16, 16)
	if err != nil {
		return netip.AddrPort{}, err
	}
	raw, err := hex.DecodeString(host)
	if err != nil || (len(raw) != 4 && len(raw) != 16) {
		return netip.AddrPort{}, errors.New("bad address")
	}
	out := make([]byte, len(raw))
	for i := 0; i < len(raw); i += 4 {
		word := binary.BigEndian.Uint32(raw[i : i+4])
		binary.NativeEndian.PutUint32(out[i:i+4], word)
	}
	addr, _ := netip.AddrFromSlice(out)
	return netip.AddrPortFrom(addr.Unmap(), uint16(port)), nil
}
