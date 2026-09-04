//go:build !windows

package secret

import "fmt"

// Unprotect is Windows-only; non-Windows builds cannot unwrap DPAPI envelopes.
func Unprotect(_ []byte) ([]byte, error) {
	return nil, fmt.Errorf("DPAPI envelopes are only supported on Windows")
}

// Protect is Windows-only.
func Protect(_ []byte) ([]byte, error) {
	return nil, fmt.Errorf("DPAPI envelopes are only supported on Windows")
}
