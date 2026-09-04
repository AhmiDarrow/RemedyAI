package connect

import (
	"errors"
	"fmt"
	"net"
	"strings"
)

// ErrBind is a Connect bind-policy failure (wildcard / empty / not chosen IPv4).
var ErrBind = errors.New("connect bind refused")

// WildcardHosts that must never be used for the Connect listener.
var WildcardHosts = map[string]struct{}{
	"0.0.0.0":         {},
	"::":              {},
	"[::]":            {},
	"*":               {},
	"0:0:0:0:0:0:0:0": {},
}

// IsWildcardBind reports whether host is an unspecified / catch-all bind.
func IsWildcardBind(host string) bool {
	text := strings.TrimSpace(host)
	if text == "" {
		return false
	}
	if _, ok := WildcardHosts[text]; ok {
		return true
	}
	inner := text
	if strings.HasPrefix(text, "[") && strings.HasSuffix(text, "]") {
		inner = text[1 : len(text)-1]
	}
	if _, ok := WildcardHosts[inner]; ok {
		return true
	}
	ip := net.ParseIP(inner)
	if ip == nil {
		return false
	}
	return ip.IsUnspecified()
}

// AssertChosenBind refuses empty and wildcard hosts. Returns the stripped host.
func AssertChosenBind(host string) (string, error) {
	text := strings.TrimSpace(host)
	if text == "" {
		return "", fmt.Errorf("%w: bind host is empty", ErrBind)
	}
	if IsWildcardBind(text) {
		return "", fmt.Errorf("%w: wildcard bind is not allowed", ErrBind)
	}
	return text, nil
}

// IsChosenIPv4 is true for a unicast IPv4 (including 127.0.0.1).
func IsChosenIPv4(host string) bool {
	text := strings.TrimSpace(host)
	if text == "" || IsWildcardBind(text) {
		return false
	}
	ip := net.ParseIP(text)
	if ip == nil {
		return false
	}
	v4 := ip.To4()
	if v4 == nil {
		return false
	}
	if ip.IsUnspecified() || ip.IsMulticast() {
		return false
	}
	if v4[0] == 255 && v4[1] == 255 && v4[2] == 255 && v4[3] == 255 {
		return false
	}
	return true
}
