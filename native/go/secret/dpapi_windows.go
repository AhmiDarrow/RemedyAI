//go:build windows

package secret

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

// Unprotect decrypts a user-scoped DPAPI blob (CRYPTPROTECT_UI_FORBIDDEN).
func Unprotect(cipher []byte) ([]byte, error) {
	if len(cipher) == 0 {
		return nil, fmt.Errorf("empty DPAPI ciphertext")
	}
	in := windows.DataBlob{
		Size: uint32(len(cipher)),
		Data: &cipher[0],
	}
	var out windows.DataBlob
	err := windows.CryptUnprotectData(
		&in,
		nil,
		nil,
		0,
		nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN,
		&out,
	)
	if err != nil {
		return nil, fmt.Errorf("CryptUnprotectData: %w", err)
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	plain := make([]byte, out.Size)
	copy(plain, unsafe.Slice(out.Data, out.Size))
	return plain, nil
}

// Protect seals plaintext with user-scoped DPAPI (tests / writers).
func Protect(plain []byte) ([]byte, error) {
	if len(plain) == 0 {
		return nil, fmt.Errorf("empty plaintext")
	}
	in := windows.DataBlob{
		Size: uint32(len(plain)),
		Data: &plain[0],
	}
	var out windows.DataBlob
	name, err := windows.UTF16PtrFromString("Remedy provider keys")
	if err != nil {
		return nil, err
	}
	err = windows.CryptProtectData(
		&in,
		name,
		nil,
		0,
		nil,
		windows.CRYPTPROTECT_UI_FORBIDDEN,
		&out,
	)
	if err != nil {
		return nil, fmt.Errorf("CryptProtectData: %w", err)
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(out.Data)))
	sealed := make([]byte, out.Size)
	copy(sealed, unsafe.Slice(out.Data, out.Size))
	return sealed, nil
}
