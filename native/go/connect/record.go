package connect

import (
	"crypto/subtle"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
)

const (
	// MaxRecord is the maximum size of nonce12||ciphertext (64 KiB).
	MaxRecord = 65536
	// NonceLen is the on-wire Noise transport nonce length.
	NonceLen = 12
	// MaxPlaintext is MaxRecord minus nonce and Poly1305 tag.
	MaxPlaintext = MaxRecord - NonceLen - TagLen
)

// ErrRecord is a framing / size error for length-prefixed transport records.
var ErrRecord = errors.New("record framing error")

// PackRecord builds u32be(len(nonce||ct)) || nonce12 || ciphertext.
func PackRecord(nonce12, ciphertext []byte) ([]byte, error) {
	if len(nonce12) != NonceLen {
		return nil, fmt.Errorf("%w: record nonce must be 12 bytes", ErrRecord)
	}
	bodyLen := NonceLen + len(ciphertext)
	if bodyLen > MaxRecord {
		return nil, fmt.Errorf("%w: record exceeds 64 KiB", ErrRecord)
	}
	out := make([]byte, 4+bodyLen)
	binary.BigEndian.PutUint32(out[:4], uint32(bodyLen))
	copy(out[4:4+NonceLen], nonce12)
	copy(out[4+NonceLen:], ciphertext)
	return out, nil
}

// UnpackRecord parses a complete packed record blob into nonce12 and ciphertext.
func UnpackRecord(blob []byte) (nonce12, ciphertext []byte, err error) {
	if len(blob) < 4+NonceLen {
		return nil, nil, fmt.Errorf("%w: truncated record", ErrRecord)
	}
	length := binary.BigEndian.Uint32(blob[:4])
	if length > MaxRecord {
		return nil, nil, fmt.Errorf("%w: record exceeds 64 KiB", ErrRecord)
	}
	if length < NonceLen {
		return nil, nil, fmt.Errorf("%w: record too short", ErrRecord)
	}
	if len(blob) != 4+int(length) {
		return nil, nil, fmt.Errorf("%w: record length mismatch", ErrRecord)
	}
	body := blob[4:]
	nonce := append([]byte(nil), body[:NonceLen]...)
	ct := append([]byte(nil), body[NonceLen:]...)
	return nonce, ct, nil
}

// ReadRecord reads one length-prefixed record from r (header then body).
func ReadRecord(r io.Reader) (nonce12, ciphertext []byte, err error) {
	var header [4]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, nil, err
	}
	length := binary.BigEndian.Uint32(header[:])
	if length > MaxRecord {
		return nil, nil, fmt.Errorf("%w: record exceeds 64 KiB", ErrRecord)
	}
	if length < NonceLen {
		return nil, nil, fmt.Errorf("%w: record too short", ErrRecord)
	}
	body := make([]byte, length)
	if _, err := io.ReadFull(r, body); err != nil {
		return nil, nil, err
	}
	nonce := append([]byte(nil), body[:NonceLen]...)
	ct := append([]byte(nil), body[NonceLen:]...)
	return nonce, ct, nil
}

// WriteRecord writes a packed record blob (u32be|nonce12|ct) to w.
func WriteRecord(w io.Writer, packed []byte) error {
	if len(packed) < 4+NonceLen {
		return fmt.Errorf("%w: truncated record", ErrRecord)
	}
	length := binary.BigEndian.Uint32(packed[:4])
	if length > MaxRecord {
		return fmt.Errorf("%w: record exceeds 64 KiB", ErrRecord)
	}
	if int(length) != len(packed)-4 {
		return fmt.Errorf("%w: record length mismatch", ErrRecord)
	}
	_, err := w.Write(packed)
	return err
}

// EncryptRecord encrypts plaintext with AD=b"" and packs u32be|nonce12|ct.
func EncryptRecord(cs *CipherState, plaintext []byte) ([]byte, error) {
	if cs == nil {
		return nil, fmt.Errorf("%w: nil CipherState", ErrRecord)
	}
	if len(plaintext) > MaxPlaintext {
		return nil, fmt.Errorf("%w: record exceeds 64 KiB", ErrRecord)
	}
	nonce12 := EncodeNonce(cs.Nonce())
	ciphertext, err := cs.EncryptWithAd(nil, plaintext)
	if err != nil {
		return nil, err
	}
	return PackRecord(nonce12, ciphertext)
}

// DecryptRecord unpacks blob, checks on-wire nonce matches cs.Nonce(), then decrypts.
func DecryptRecord(cs *CipherState, blob []byte) ([]byte, error) {
	if cs == nil {
		return nil, fmt.Errorf("%w: nil CipherState", ErrRecord)
	}
	nonce12, ciphertext, err := UnpackRecord(blob)
	if err != nil {
		return nil, err
	}
	expected := EncodeNonce(cs.Nonce())
	if subtle.ConstantTimeCompare(nonce12, expected) != 1 {
		return nil, fmt.Errorf("%w: record nonce replay or out of order", ErrNoise)
	}
	return cs.DecryptWithAd(nil, ciphertext)
}
