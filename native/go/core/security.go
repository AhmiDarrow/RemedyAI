package core

import (
	"crypto/rand"
	"encoding/json"
	"fmt"
	"sync"
	"time"
)

var (
	signingMu    sync.Mutex
	signingReady bool
)

// SecuritySetSigningKey installs the first 32 bytes as the HMAC key.
func SecuritySetSigningKey(key []byte) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	if len(key) < 32 {
		return fmt.Errorf("signing key must be at least 32 bytes")
	}
	buf := append([]byte(nil), key[:32]...)
	status, err := lib.call("remedy_core_security_set_signing_key", bytesPtr(buf), uintptr(len(buf)))
	if err != nil {
		return err
	}
	if err := lib.check("security_set_signing_key", int32(status)); err != nil {
		return err
	}
	signingMu.Lock()
	signingReady = true
	signingMu.Unlock()
	return nil
}

// EnsureSigningKey installs key into Zig when not yet ready.
func EnsureSigningKey(key []byte) error {
	signingMu.Lock()
	ready := signingReady
	signingMu.Unlock()
	if ready {
		return nil
	}
	return SecuritySetSigningKey(key)
}

// PolicyHashArgv returns the 32-byte Zig argv hash.
func PolicyHashArgv(argv []string) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	argvRaw, err := json.Marshal(argv)
	if err != nil {
		return nil, err
	}
	out := make([]byte, 32)
	status, err := lib.call(
		"remedy_core_policy_hash_argv",
		bytesPtr(argvRaw), uintptr(len(argvRaw)),
		bytesPtr(out), uintptr(len(out)),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("policy_hash_argv", int32(status)); err != nil {
		return nil, err
	}
	return out, nil
}

// CapabilityIssue issues a v2 capability token (169 bytes).
func CapabilityIssue(
	operationHash []byte,
	rightsBits uint64,
	subject, scope string,
	issuedAtMS, expiresAtMS uint64,
	nonce []byte,
) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	if len(operationHash) != 32 {
		return nil, fmt.Errorf("operation_hash must be 32 bytes")
	}
	if len(nonce) != 16 {
		return nil, fmt.Errorf("nonce must be 16 bytes")
	}
	if subject == "" {
		subject = DefaultSpawnSubject
	}
	if scope == "" {
		scope = DefaultSpawnScope
	}
	subjectRaw := []byte(subject)
	scopeRaw := []byte(scope)
	op := append([]byte(nil), operationHash...)
	nonceBuf := append([]byte(nil), nonce...)
	out := make([]byte, CapabilityTokenSize)
	status, err := lib.call(
		"remedy_core_capability_issue",
		bytesPtr(subjectRaw), uintptr(len(subjectRaw)),
		bytesPtr(scopeRaw), uintptr(len(scopeRaw)),
		bytesPtr(op), uintptr(len(op)),
		uintptr(rightsBits),
		uintptr(issuedAtMS),
		uintptr(expiresAtMS),
		bytesPtr(nonceBuf), uintptr(len(nonceBuf)),
		bytesPtr(out), uintptr(len(out)),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("capability_issue", int32(status)); err != nil {
		return nil, err
	}
	return out, nil
}

// IssueProcessSpawnToken returns (token, nowMS) for argv.
func IssueProcessSpawnToken(argv []string, ownerCheckpoint bool) ([]byte, uint64, error) {
	digest, err := PolicyHashArgv(argv)
	if err != nil {
		return nil, 0, err
	}
	now := uint64(time.Now().UnixMilli())
	rights := ProcessSpawnRight
	if ownerCheckpoint {
		rights |= OwnerCheckpointRight
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return nil, 0, err
	}
	token, err := CapabilityIssue(
		digest, rights,
		DefaultSpawnSubject, DefaultSpawnScope,
		now, now+TokenLifetimeMS, nonce,
	)
	if err != nil {
		return nil, 0, err
	}
	return token, now, nil
}

// WriteJailSetRoots installs write roots (empty = Full / unbound).
func WriteJailSetRoots(roots []string) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	if roots == nil {
		roots = []string{}
	}
	payload, err := json.Marshal(roots)
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_write_jail_set_roots", bytesPtr(payload), uintptr(len(payload)))
	if err != nil {
		return err
	}
	return lib.check("write_jail_set_roots", int32(status))
}
