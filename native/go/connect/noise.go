// Package connect implements RemedyConnect Noise_IK transport primitives.
//
// Protocol: Noise_IK_25519_ChaChaPoly_BLAKE2s (revision 34), matching
// Python remedy.connect.noise and Android groveconnect.core.
package connect

import (
	"crypto/ecdh"
	"crypto/hmac"
	"crypto/rand"
	"crypto/subtle"
	"encoding/binary"
	"errors"
	"fmt"
	"hash"

	"golang.org/x/crypto/blake2s"
	"golang.org/x/crypto/chacha20poly1305"
)

const (
	// Prologue is the product Noise prologue mixed before the IK pre-message.
	Prologue = "remedy-connect/1"
	// ProtocolName is the Noise protocol name string.
	ProtocolName = "Noise_IK_25519_ChaChaPoly_BLAKE2s"

	DHLen        = 32
	HashLen      = 32
	CipherKeyLen = 32
	TagLen       = 16
	maxNonce     = (1 << 64) - 1
)

var (
	// ErrNoise is a handshake or AEAD failure. Messages never contain key or payload bytes.
	ErrNoise = errors.New("noise failure")

	protocolNameBytes = []byte(ProtocolName)
	defaultPrologue   = []byte(Prologue)
	zeroKeyCheck      = make([]byte, DHLen)

	ikMessages = [][]string{
		{"e", "es", "s", "ss"},
		{"e", "ee", "se"},
	}
)

// KeyPair is an X25519 static or ephemeral key pair.
type KeyPair struct {
	Private []byte
	Public  []byte
}

// GenerateKeyPair returns a fresh X25519 key pair.
func GenerateKeyPair() (KeyPair, error) {
	priv := make([]byte, DHLen)
	if _, err := rand.Read(priv); err != nil {
		return KeyPair{}, err
	}
	return KeyPairFromPrivate(priv)
}

// KeyPairFromPrivate derives the X25519 public key for a 32-byte private scalar.
func KeyPairFromPrivate(sk []byte) (KeyPair, error) {
	if len(sk) != DHLen {
		return KeyPair{}, fmt.Errorf("%w: X25519 private key must be 32 bytes", ErrNoise)
	}
	curve := ecdh.X25519()
	priv, err := curve.NewPrivateKey(sk)
	if err != nil {
		return KeyPair{}, fmt.Errorf("%w: invalid private key", ErrNoise)
	}
	pub := priv.PublicKey().Bytes()
	outPriv := make([]byte, DHLen)
	copy(outPriv, sk)
	return KeyPair{Private: outPriv, Public: pub}, nil
}

// CipherState is a Noise CipherState: IETF ChaCha20-Poly1305, nonce strictly increasing.
type CipherState struct {
	k []byte // nil = no key
	n uint64
}

// NewCipherState returns a CipherState. key may be nil (passthrough until MixKey).
func NewCipherState(key []byte) (*CipherState, error) {
	cs := &CipherState{}
	if key != nil {
		if len(key) != CipherKeyLen {
			return nil, fmt.Errorf("%w: cipher key must be 32 bytes", ErrNoise)
		}
		cs.k = append([]byte(nil), key...)
	}
	return cs, nil
}

// HasKey reports whether an AEAD key is set.
func (c *CipherState) HasKey() bool { return c != nil && c.k != nil }

// Nonce returns the next nonce counter value.
func (c *CipherState) Nonce() uint64 {
	if c == nil {
		return 0
	}
	return c.n
}

// EncryptWithAd encrypts plaintext with associated data, or returns plaintext if unkeyed.
func (c *CipherState) EncryptWithAd(ad, plaintext []byte) ([]byte, error) {
	if c == nil || c.k == nil {
		return append([]byte(nil), plaintext...), nil
	}
	if c.n >= maxNonce {
		return nil, fmt.Errorf("%w: nonce exhausted", ErrNoise)
	}
	out, err := aeadEncrypt(c.k, c.n, ad, plaintext)
	if err != nil {
		return nil, err
	}
	c.n++
	return out, nil
}

// DecryptWithAd decrypts ciphertext with associated data, or returns ciphertext if unkeyed.
// Authentication failure does not increment n (Noise §5.1).
func (c *CipherState) DecryptWithAd(ad, ciphertext []byte) ([]byte, error) {
	if c == nil || c.k == nil {
		return append([]byte(nil), ciphertext...), nil
	}
	if c.n >= maxNonce {
		return nil, fmt.Errorf("%w: nonce exhausted", ErrNoise)
	}
	pt, err := aeadDecrypt(c.k, c.n, ad, ciphertext)
	if err != nil {
		return nil, err
	}
	c.n++
	return pt, nil
}

// Rekey sets k = ENCRYPT(k, 2^64-1, empty AD, 32 zero bytes)[:32] and resets n.
func (c *CipherState) Rekey() error {
	if c == nil || c.k == nil {
		return fmt.Errorf("%w: cannot rekey an empty CipherState", ErrNoise)
	}
	zeros := make([]byte, CipherKeyLen)
	out, err := aeadEncrypt(c.k, maxNonce, nil, zeros)
	if err != nil {
		return err
	}
	c.k = append([]byte(nil), out[:CipherKeyLen]...)
	c.n = 0
	return nil
}

// HandshakeState is a Noise_IK initiator (phone) or responder (host).
type HandshakeState struct {
	initiator bool
	s         KeyPair
	e         *KeyPair
	rs        []byte
	re        []byte
	msgI      int
	done      bool
	splitDone bool
	h         []byte
	ck        []byte
	cs        *CipherState
}

// HandshakeConfig configures a Noise_IK handshake.
type HandshakeConfig struct {
	Initiator    bool
	Local        KeyPair
	RemoteStatic []byte // required for initiator (host static from QR hp=)
	Prologue     []byte // nil → product prologue
}

// NewHandshakeState builds a Noise_IK HandshakeState.
func NewHandshakeState(cfg HandshakeConfig) (*HandshakeState, error) {
	if len(cfg.Local.Private) != DHLen || len(cfg.Local.Public) != DHLen {
		return nil, fmt.Errorf("%w: local static must be 32-byte X25519 keys", ErrNoise)
	}
	prologue := cfg.Prologue
	if prologue == nil {
		prologue = defaultPrologue
	}
	hs := &HandshakeState{
		initiator: cfg.Initiator,
		s: KeyPair{
			Private: append([]byte(nil), cfg.Local.Private...),
			Public:  append([]byte(nil), cfg.Local.Public...),
		},
		cs: &CipherState{},
	}
	if cfg.Initiator {
		if len(cfg.RemoteStatic) != DHLen {
			return nil, fmt.Errorf("%w: IK initiator requires the 32-byte host static public", ErrNoise)
		}
		hs.rs = append([]byte(nil), cfg.RemoteStatic...)
	} else if cfg.RemoteStatic != nil {
		if len(cfg.RemoteStatic) != DHLen {
			return nil, fmt.Errorf("%w: remote static public must be 32 bytes", ErrNoise)
		}
		hs.rs = append([]byte(nil), cfg.RemoteStatic...)
	}

	if len(protocolNameBytes) <= HashLen {
		hs.h = make([]byte, HashLen)
		copy(hs.h, protocolNameBytes)
	} else {
		hs.h = blake2sHash(protocolNameBytes)
	}
	hs.ck = append([]byte(nil), hs.h...)
	hs.mixHash(prologue)
	// Pre-message: <- s (responder static, known to initiator from QR).
	if cfg.Initiator {
		hs.mixHash(hs.rs)
	} else {
		hs.mixHash(hs.s.Public)
	}
	return hs, nil
}

// SetEphemeralForTest forces the ephemeral private key (Noise test vectors).
func (hs *HandshakeState) SetEphemeralForTest(private []byte) error {
	kp, err := KeyPairFromPrivate(private)
	if err != nil {
		return err
	}
	hs.e = &kp
	return nil
}

// WriteMessage writes the next handshake message, encrypting payload under the pattern.
func (hs *HandshakeState) WriteMessage(payload []byte) ([]byte, error) {
	if hs.done {
		return nil, fmt.Errorf("%w: handshake is complete", ErrNoise)
	}
	if !hs.writeTurn() {
		return nil, fmt.Errorf("%w: not this role's turn to write", ErrNoise)
	}
	tokens := ikMessages[hs.msgI]
	var buf []byte
	for _, token := range tokens {
		chunk, err := hs.writeToken(token)
		if err != nil {
			return nil, err
		}
		buf = append(buf, chunk...)
	}
	enc, err := hs.encryptAndHash(payload)
	if err != nil {
		return nil, err
	}
	buf = append(buf, enc...)
	hs.advance()
	return buf, nil
}

// ReadMessage consumes a peer handshake message and returns the decrypted payload.
func (hs *HandshakeState) ReadMessage(message []byte) ([]byte, error) {
	if hs.done {
		return nil, fmt.Errorf("%w: handshake is complete", ErrNoise)
	}
	if hs.writeTurn() {
		return nil, fmt.Errorf("%w: not this role's turn to read", ErrNoise)
	}
	tokens := ikMessages[hs.msgI]
	pos := 0
	take := func(n int) ([]byte, error) {
		if n < 0 || pos+n > len(message) {
			return nil, fmt.Errorf("%w: truncated handshake message", ErrNoise)
		}
		chunk := message[pos : pos+n]
		pos += n
		return chunk, nil
	}
	for _, token := range tokens {
		if err := hs.readToken(token, take); err != nil {
			return nil, err
		}
	}
	payload, err := hs.decryptAndHash(message[pos:])
	if err != nil {
		return nil, err
	}
	hs.advance()
	return payload, nil
}

// Split derives the transport CipherStates after a completed handshake.
// Returns (send, recv) for this role.
func (hs *HandshakeState) Split() (send, recv *CipherState, err error) {
	if !hs.done {
		return nil, nil, fmt.Errorf("%w: handshake is not complete", ErrNoise)
	}
	if hs.splitDone {
		return nil, nil, fmt.Errorf("%w: handshake already split", ErrNoise)
	}
	out, err := hkdf(hs.ck, nil, 2)
	if err != nil {
		return nil, nil, err
	}
	hs.splitDone = true
	c1, err := NewCipherState(out[0])
	if err != nil {
		return nil, nil, err
	}
	c2, err := NewCipherState(out[1])
	if err != nil {
		return nil, nil, err
	}
	if hs.initiator {
		return c1, c2, nil
	}
	return c2, c1, nil
}

// RemoteStaticPublic returns the peer static public key once known.
func (hs *HandshakeState) RemoteStaticPublic() []byte {
	if hs.rs == nil {
		return nil
	}
	return append([]byte(nil), hs.rs...)
}

// VerifyPairSecret constant-time compares a decrypted first payload to expected.
// Mirrors Android HandshakeState.verifyPairSecret / Python hmac.compare_digest.
func VerifyPairSecret(got, expected []byte) bool {
	if len(got) != DHLen || len(expected) != DHLen {
		return false
	}
	return subtle.ConstantTimeCompare(got, expected) == 1
}

func (hs *HandshakeState) writeTurn() bool {
	return (hs.msgI%2 == 0) == hs.initiator
}

func (hs *HandshakeState) advance() {
	hs.msgI++
	if hs.msgI >= len(ikMessages) {
		hs.done = true
	}
}

func (hs *HandshakeState) mixHash(data []byte) {
	hs.h = blake2sHash(append(append([]byte{}, hs.h...), data...))
}

func (hs *HandshakeState) mixKey(ikm []byte) error {
	out, err := hkdf(hs.ck, ikm, 2)
	if err != nil {
		return err
	}
	hs.ck = out[0]
	cs, err := NewCipherState(out[1])
	if err != nil {
		return err
	}
	hs.cs = cs
	return nil
}

func (hs *HandshakeState) encryptAndHash(plaintext []byte) ([]byte, error) {
	ct, err := hs.cs.EncryptWithAd(hs.h, plaintext)
	if err != nil {
		return nil, err
	}
	hs.mixHash(ct)
	return ct, nil
}

func (hs *HandshakeState) decryptAndHash(ciphertext []byte) ([]byte, error) {
	pt, err := hs.cs.DecryptWithAd(hs.h, ciphertext)
	if err != nil {
		return nil, err
	}
	hs.mixHash(ciphertext)
	return pt, nil
}

func (hs *HandshakeState) ctLen(plaintextLen int) int {
	extra := 0
	if hs.cs.HasKey() {
		extra = TagLen
	}
	return plaintextLen + extra
}

func (hs *HandshakeState) writeToken(token string) ([]byte, error) {
	switch token {
	case "e":
		if hs.e == nil {
			kp, err := GenerateKeyPair()
			if err != nil {
				return nil, err
			}
			hs.e = &kp
		}
		hs.mixHash(hs.e.Public)
		return append([]byte(nil), hs.e.Public...), nil
	case "s":
		return hs.encryptAndHash(hs.s.Public)
	default:
		if err := hs.mixDH(token); err != nil {
			return nil, err
		}
		return nil, nil
	}
}

func (hs *HandshakeState) readToken(token string, take func(int) ([]byte, error)) error {
	switch token {
	case "e":
		re, err := take(DHLen)
		if err != nil {
			return err
		}
		hs.re = append([]byte(nil), re...)
		hs.mixHash(hs.re)
		return nil
	case "s":
		raw, err := take(hs.ctLen(DHLen))
		if err != nil {
			return err
		}
		rs, err := hs.decryptAndHash(raw)
		if err != nil {
			return err
		}
		if len(rs) != DHLen {
			return fmt.Errorf("%w: invalid remote static", ErrNoise)
		}
		hs.rs = append([]byte(nil), rs...)
		return nil
	default:
		return hs.mixDH(token)
	}
}

func (hs *HandshakeState) mixDH(token string) error {
	switch token {
	case "ee":
		if hs.e == nil || hs.re == nil {
			return fmt.Errorf("%w: missing ephemeral for ee", ErrNoise)
		}
		shared, err := dh(*hs.e, hs.re)
		if err != nil {
			return err
		}
		return hs.mixKey(shared)
	case "es":
		if hs.initiator {
			if hs.e == nil || hs.rs == nil {
				return fmt.Errorf("%w: missing keys for es", ErrNoise)
			}
			shared, err := dh(*hs.e, hs.rs)
			if err != nil {
				return err
			}
			return hs.mixKey(shared)
		}
		if hs.re == nil {
			return fmt.Errorf("%w: missing keys for es", ErrNoise)
		}
		shared, err := dh(hs.s, hs.re)
		if err != nil {
			return err
		}
		return hs.mixKey(shared)
	case "se":
		if hs.initiator {
			if hs.re == nil {
				return fmt.Errorf("%w: missing keys for se", ErrNoise)
			}
			shared, err := dh(hs.s, hs.re)
			if err != nil {
				return err
			}
			return hs.mixKey(shared)
		}
		if hs.e == nil || hs.rs == nil {
			return fmt.Errorf("%w: missing keys for se", ErrNoise)
		}
		shared, err := dh(*hs.e, hs.rs)
		if err != nil {
			return err
		}
		return hs.mixKey(shared)
	case "ss":
		if hs.rs == nil {
			return fmt.Errorf("%w: missing keys for ss", ErrNoise)
		}
		shared, err := dh(hs.s, hs.rs)
		if err != nil {
			return err
		}
		return hs.mixKey(shared)
	default:
		return fmt.Errorf("%w: unknown handshake token", ErrNoise)
	}
}

func blake2sHash(data []byte) []byte {
	sum := blake2s.Sum256(data)
	return sum[:]
}

func hmacBlake2s(key, data []byte) []byte {
	mac := hmac.New(func() hash.Hash {
		h, err := blake2s.New256(nil)
		if err != nil {
			panic(err)
		}
		return h
	}, key)
	_, _ = mac.Write(data)
	return mac.Sum(nil)
}

func hkdf(ck, ikm []byte, outputs int) ([][]byte, error) {
	if outputs != 2 && outputs != 3 {
		return nil, fmt.Errorf("%w: Noise HKDF yields 2 or 3 outputs", ErrNoise)
	}
	tempKey := hmacBlake2s(ck, ikm)
	out1 := hmacBlake2s(tempKey, []byte{0x01})
	out2 := hmacBlake2s(tempKey, append(append([]byte{}, out1...), 0x02))
	if outputs == 2 {
		return [][]byte{out1, out2}, nil
	}
	out3 := hmacBlake2s(tempKey, append(append([]byte{}, out2...), 0x03))
	return [][]byte{out1, out2, out3}, nil
}

func encodeNonce(n uint64) []byte {
	out := make([]byte, chacha20poly1305.NonceSize)
	binary.LittleEndian.PutUint64(out[4:], n)
	return out
}

// EncodeNonce returns the 12-byte IETF ChaCha20-Poly1305 nonce for counter n
// (4 zero bytes || uint64le). Matches Python remedy.connect.noise.encode_nonce.
func EncodeNonce(n uint64) []byte { return encodeNonce(n) }

func dh(local KeyPair, remotePublic []byte) ([]byte, error) {
	if len(remotePublic) != DHLen {
		return nil, fmt.Errorf("%w: invalid public key", ErrNoise)
	}
	curve := ecdh.X25519()
	priv, err := curve.NewPrivateKey(local.Private)
	if err != nil {
		return nil, fmt.Errorf("%w: invalid DH result", ErrNoise)
	}
	pub, err := curve.NewPublicKey(remotePublic)
	if err != nil {
		return nil, fmt.Errorf("%w: invalid DH result", ErrNoise)
	}
	shared, err := priv.ECDH(pub)
	if err != nil {
		return nil, fmt.Errorf("%w: invalid DH result", ErrNoise)
	}
	if subtle.ConstantTimeCompare(shared, zeroKeyCheck) == 1 {
		return nil, fmt.Errorf("%w: invalid DH result", ErrNoise)
	}
	return shared, nil
}

func aeadEncrypt(key []byte, n uint64, ad, plaintext []byte) ([]byte, error) {
	aead, err := chacha20poly1305.New(key)
	if err != nil {
		return nil, fmt.Errorf("%w: encrypt failed", ErrNoise)
	}
	nonce := encodeNonce(n)
	return aead.Seal(nil, nonce, plaintext, ad), nil
}

func aeadDecrypt(key []byte, n uint64, ad, ciphertext []byte) ([]byte, error) {
	if len(ciphertext) < TagLen {
		return nil, fmt.Errorf("%w: truncated ciphertext", ErrNoise)
	}
	aead, err := chacha20poly1305.New(key)
	if err != nil {
		return nil, fmt.Errorf("%w: decrypt failed", ErrNoise)
	}
	nonce := encodeNonce(n)
	pt, err := aead.Open(nil, nonce, ciphertext, ad)
	if err != nil {
		return nil, fmt.Errorf("%w: decrypt failed", ErrNoise)
	}
	return pt, nil
}

// HashBLAKE2s exposes BLAKE2s-256 for fixture/debug vectors.
func HashBLAKE2s(data []byte) []byte { return blake2sHash(data) }

// HKDF exposes Noise HKDF for fixture/debug vectors.
func HKDF(ck, ikm []byte, outputs int) ([][]byte, error) { return hkdf(ck, ikm, outputs) }

// DH exposes X25519 DH for fixture/debug vectors.
func DH(local KeyPair, remotePublic []byte) ([]byte, error) { return dh(local, remotePublic) }

// AEADEncrypt exposes IETF ChaCha20-Poly1305 seal for fixture/debug vectors.
func AEADEncrypt(key []byte, n uint64, ad, plaintext []byte) ([]byte, error) {
	return aeadEncrypt(key, n, ad, plaintext)
}
