//go:build windows

package secret

import (
	"encoding/binary"
	"errors"
	"net/netip"
	"unsafe"

	"golang.org/x/sys/windows"
)

var (
	modIPHelper             = windows.NewLazySystemDLL("iphlpapi.dll")
	procGetExtendedTcpTable = modIPHelper.NewProc("GetExtendedTcpTable")
)

const tcpTableOwnerPIDAll = 5

// sameUserPeer finds the PID owning the client end of the connection with
// GetExtendedTcpTable, then compares that process's token user SID with ours.
func SameUserPeer(client, server netip.AddrPort) (bool, error) {
	pid, err := tcpOwnerPID(client, server)
	if err != nil {
		return false, err
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		// A process we may not query (another user, or elevated) is not us.
		return false, nil
	}
	defer windows.CloseHandle(h)
	var tok windows.Token
	if err := windows.OpenProcessToken(h, windows.TOKEN_QUERY, &tok); err != nil {
		return false, nil
	}
	defer tok.Close()
	peer, err := tok.GetTokenUser()
	if err != nil {
		return false, nil
	}
	me, err := windows.GetCurrentProcessToken().GetTokenUser()
	if err != nil {
		return false, err
	}
	return windows.EqualSid(peer.User.Sid, me.User.Sid), nil
}

func tcpOwnerPID(client, server netip.AddrPort) (uint32, error) {
	family := uint32(windows.AF_INET)
	if client.Addr().Is6() {
		family = windows.AF_INET6
	}
	var size uint32
	buf := make([]byte, 64*1024)
	for attempt := 0; attempt < 4; attempt++ {
		size = uint32(len(buf))
		r, _, _ := procGetExtendedTcpTable.Call(
			uintptr(unsafe.Pointer(&buf[0])),
			uintptr(unsafe.Pointer(&size)),
			0,
			uintptr(family),
			tcpTableOwnerPIDAll,
			0,
		)
		if r == 0 {
			return findOwnerPID(buf[:size], family == windows.AF_INET6, client, server)
		}
		if windows.Errno(r) != windows.ERROR_INSUFFICIENT_BUFFER {
			return 0, windows.Errno(r)
		}
		buf = make([]byte, size+4096)
	}
	return 0, errors.New("tcp table kept growing")
}

// findOwnerPID walks MIB_TCPTABLE_OWNER_PID / MIB_TCP6TABLE_OWNER_PID.
func findOwnerPID(tbl []byte, v6 bool, client, server netip.AddrPort) (uint32, error) {
	if len(tbl) < 4 {
		return 0, errors.New("short tcp table")
	}
	n := int(binary.LittleEndian.Uint32(tbl[:4]))
	rowSize, off := 24, 4
	if v6 {
		rowSize = 56
	}
	port := func(dw []byte) uint16 { return binary.BigEndian.Uint16(dw[:2]) }
	for i := 0; i < n; i++ {
		row := tbl[off+i*rowSize:]
		if len(row) < rowSize {
			break
		}
		var la, ra netip.AddrPort
		var pid uint32
		if v6 {
			l, _ := netip.AddrFromSlice(row[0:16])
			r, _ := netip.AddrFromSlice(row[24:40])
			la = netip.AddrPortFrom(l.Unmap(), port(row[20:24]))
			ra = netip.AddrPortFrom(r.Unmap(), port(row[44:48]))
			pid = binary.LittleEndian.Uint32(row[52:56])
		} else {
			l, _ := netip.AddrFromSlice(row[4:8])
			r, _ := netip.AddrFromSlice(row[12:16])
			la = netip.AddrPortFrom(l, port(row[8:12]))
			ra = netip.AddrPortFrom(r, port(row[16:20]))
			pid = binary.LittleEndian.Uint32(row[20:24])
		}
		if la == client && ra == server {
			return pid, nil
		}
	}
	return 0, errors.New("peer connection not found in tcp table")
}
