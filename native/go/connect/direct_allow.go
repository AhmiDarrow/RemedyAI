package connect

import (
	"crypto/ecdh"
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"sync"
)

// Who may use the away-from-home endpoint: the paired phones and nobody
// else, decided before any stream is opened.
//
// A phone's identity on that path is not something it tells the PC. Both
// sides derive it from the secret they already share (the X25519 agreement
// of the phone's and the PC's long-term keys), so the PC can list the exact
// identities to accept from its own device records, and a phone that was
// paired before this existed is covered without pairing again. A stranger
// who reaches the port can finish a QUIC handshake and is then dropped: no
// stream reaches the gateway. Pairing a new phone therefore has to happen on
// the home network.

// directKeyLabel domain-separates the derivation. Change it and every
// phone's direct identity changes.
const directKeyLabel = "remedy-direct/1"

// DirectIdentitySeed is the 32-byte seed of the phone's endpoint key for
// this PC: SHA-256(label || X25519(own private, peer public)). The phone
// computes the same value with its private key and the PC's public key.
func DirectIdentitySeed(ownPrivate, peerPublic []byte) ([]byte, error) {
	curve := ecdh.X25519()
	priv, err := curve.NewPrivateKey(ownPrivate)
	if err != nil {
		return nil, err
	}
	pub, err := curve.NewPublicKey(peerPublic)
	if err != nil {
		return nil, err
	}
	shared, err := priv.ECDH(pub)
	if err != nil {
		return nil, err
	}
	h := sha256.New()
	h.Write([]byte(directKeyLabel))
	h.Write(shared)
	return h.Sum(nil), nil
}

// DirectEndpointID is the endpoint id (lowercase hex of the Ed25519 public
// key) a paired phone presents on the away-from-home path.
func DirectEndpointID(hostPrivate, devicePublic []byte) (string, error) {
	seed, err := DirectIdentitySeed(hostPrivate, devicePublic)
	if err != nil {
		return "", err
	}
	pub := ed25519.NewKeyFromSeed(seed).Public().(ed25519.PublicKey)
	return hex.EncodeToString(pub), nil
}

// DirectAllowList is the endpoint ids of every paired, unrevoked phone.
func DirectAllowList(home string) ([]string, error) {
	host, err := LoadOrCreateHostKeyPair(home)
	if err != nil {
		return nil, err
	}
	devices, err := ListDevices(home, false)
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(devices))
	for _, d := range devices {
		pub, err := hex.DecodeString(d.PublicHex)
		if err != nil || d.Revoked {
			continue
		}
		id, err := DirectEndpointID(host.Private, pub)
		if err != nil {
			continue
		}
		ids = append(ids, id)
	}
	return ids, nil
}

var (
	directAllowMu   sync.Mutex
	directAllowSend func(ids []string)
	directAllowHome string
)

// setDirectAllowSender is how the running endpoint takes a new allow-list
// (nil when it stops).
func setDirectAllowSender(home string, send func(ids []string)) {
	directAllowMu.Lock()
	directAllowSend, directAllowHome = send, home
	directAllowMu.Unlock()
}

// refreshDirectAllow recomputes the allow-list and hands it to the running
// endpoint. Called whenever a device record changes.
func refreshDirectAllow(home string) {
	directAllowMu.Lock()
	send, running := directAllowSend, directAllowHome
	directAllowMu.Unlock()
	if send == nil || running != home {
		return
	}
	ids, err := DirectAllowList(home)
	if err != nil {
		// Fail closed: with no list we can vouch for, nobody is let in.
		ids = []string{}
	}
	send(ids)
}
