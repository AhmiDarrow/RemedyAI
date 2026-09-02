// Package protocol defines the versioned language-neutral Remedy wire contract.
package protocol

import (
	"encoding/binary"
	"errors"
	"fmt"
)

const (
	Version        uint16 = 1
	HeaderSize            = 32
	MaxPayloadSize        = 16 << 20
)

var Magic = [4]byte{'R', 'M', 'D', 'Y'}

type Kind uint16

const (
	KindToolRequest Kind = iota + 1
	KindToolResult
	KindEvent
	KindControl
)

var (
	ErrShortFrame       = errors.New("remedy frame is shorter than its header")
	ErrBadMagic         = errors.New("remedy frame has invalid magic")
	ErrUnsupported      = errors.New("remedy frame version is unsupported")
	ErrInvalidKind      = errors.New("remedy frame kind is invalid")
	ErrPayloadTooLarge  = errors.New("remedy frame payload exceeds the limit")
	ErrPayloadTruncated = errors.New("remedy frame payload length does not match")
)

type Frame struct {
	Kind          Kind
	Flags         uint32
	CorrelationID [16]byte
	Payload       []byte
}

func (f Frame) MarshalBinary() ([]byte, error) {
	if f.Kind == 0 {
		return nil, ErrInvalidKind
	}
	if len(f.Payload) > MaxPayloadSize {
		return nil, ErrPayloadTooLarge
	}
	out := make([]byte, HeaderSize+len(f.Payload))
	copy(out[:4], Magic[:])
	binary.LittleEndian.PutUint16(out[4:6], Version)
	binary.LittleEndian.PutUint16(out[6:8], uint16(f.Kind))
	binary.LittleEndian.PutUint32(out[8:12], f.Flags)
	binary.LittleEndian.PutUint32(out[12:16], uint32(len(f.Payload)))
	copy(out[16:32], f.CorrelationID[:])
	copy(out[HeaderSize:], f.Payload)
	return out, nil
}

func Parse(raw []byte) (Frame, error) {
	if len(raw) < HeaderSize {
		return Frame{}, ErrShortFrame
	}
	if string(raw[:4]) != string(Magic[:]) {
		return Frame{}, ErrBadMagic
	}
	if version := binary.LittleEndian.Uint16(raw[4:6]); version != Version {
		return Frame{}, fmt.Errorf("%w: %d", ErrUnsupported, version)
	}
	kind := Kind(binary.LittleEndian.Uint16(raw[6:8]))
	if kind == 0 {
		return Frame{}, ErrInvalidKind
	}
	payloadLen := binary.LittleEndian.Uint32(raw[12:16])
	if payloadLen > MaxPayloadSize {
		return Frame{}, ErrPayloadTooLarge
	}
	if int(payloadLen) != len(raw)-HeaderSize {
		return Frame{}, ErrPayloadTruncated
	}
	var correlationID [16]byte
	copy(correlationID[:], raw[16:32])
	payload := append([]byte(nil), raw[HeaderSize:]...)
	return Frame{
		Kind:          kind,
		Flags:         binary.LittleEndian.Uint32(raw[8:12]),
		CorrelationID: correlationID,
		Payload:       payload,
	}, nil
}
