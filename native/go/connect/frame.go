package connect

import (
	"encoding/binary"
	"errors"
	"fmt"
	"strings"
)

const (
	InnerVersion = 1
	InnerHdrLen  = 11

	TypeHTTPReq = 0x01
	TypeHTTPRes = 0x02
	TypePing    = 0x10
	TypePong    = 0x11
	TypeRekey   = 0x20

	FlagFIN = 0x01
)

// ErrInner is a framing / size error for multiplex inner frames.
var ErrInner = errors.New("inner frame error")

// InnerFrame is one multiplexed message fragment.
type InnerFrame struct {
	Type    uint8
	ID      uint32
	Flags   uint8
	Payload []byte
}

func (f InnerFrame) Fin() bool { return f.Flags&FlagFIN != 0 }

// EncodeInner builds u8 ver|u8 type|u32be id|u8 flags|u32be len|payload.
func EncodeInner(typ uint8, id uint32, payload []byte, fin bool) ([]byte, error) {
	if payload == nil {
		payload = []byte{}
	}
	if len(payload) > MaxPlaintext {
		return nil, fmt.Errorf("%w: payload exceeds MaxPlaintext", ErrInner)
	}
	flags := uint8(0)
	if fin {
		flags = FlagFIN
	}
	out := make([]byte, InnerHdrLen+len(payload))
	out[0] = InnerVersion
	out[1] = typ
	binary.BigEndian.PutUint32(out[2:6], id)
	out[6] = flags
	binary.BigEndian.PutUint32(out[7:11], uint32(len(payload)))
	copy(out[InnerHdrLen:], payload)
	return out, nil
}

// DecodeInner parses one complete inner frame. Trailing bytes are rejected.
func DecodeInner(raw []byte) (InnerFrame, error) {
	if len(raw) < InnerHdrLen {
		return InnerFrame{}, fmt.Errorf("%w: short inner frame", ErrInner)
	}
	if raw[0] != InnerVersion {
		return InnerFrame{}, fmt.Errorf("%w: inner version %d", ErrInner, raw[0])
	}
	typ := raw[1]
	id := binary.BigEndian.Uint32(raw[2:6])
	flags := raw[6]
	ln := binary.BigEndian.Uint32(raw[7:11])
	if int(ln) > MaxPlaintext {
		return InnerFrame{}, fmt.Errorf("%w: payload too large", ErrInner)
	}
	if InnerHdrLen+int(ln) > len(raw) {
		return InnerFrame{}, fmt.Errorf("%w: short inner payload", ErrInner)
	}
	if InnerHdrLen+int(ln) != len(raw) {
		return InnerFrame{}, fmt.Errorf("%w: trailing inner frame data", ErrInner)
	}
	payload := append([]byte(nil), raw[InnerHdrLen:InnerHdrLen+int(ln)]...)
	return InnerFrame{Type: typ, ID: id, Flags: flags, Payload: payload}, nil
}

// FragmentInner splits payload into inner frames that each fit under maxPlain bytes
// (including the 11-byte header). chunk = max(1, maxPlain-11).
func FragmentInner(typ uint8, id uint32, payload []byte, maxPlain int, fin bool) ([][]byte, error) {
	if maxPlain <= 0 {
		maxPlain = MaxPlaintext
	}
	chunk := maxPlain - InnerHdrLen
	if chunk < 1 {
		chunk = 1
	}
	if len(payload) <= chunk {
		frame, err := EncodeInner(typ, id, payload, fin)
		if err != nil {
			return nil, err
		}
		return [][]byte{frame}, nil
	}
	out := make([][]byte, 0, (len(payload)+chunk-1)/chunk)
	for offset := 0; offset < len(payload); {
		end := offset + chunk
		if end > len(payload) {
			end = len(payload)
		}
		last := end == len(payload)
		frame, err := EncodeInner(typ, id, payload[offset:end], fin && last)
		if err != nil {
			return nil, err
		}
		out = append(out, frame)
		offset = end
	}
	return out, nil
}

// HTTPRequest is the binary request payload inside TYPE_HTTP_REQ.
type HTTPRequest struct {
	Method  string
	Target  string
	Headers string
	Body    []byte
}

// EncodeHTTPRequest packs method/target/headers/body (Android HttpFrame layout).
func EncodeHTTPRequest(req HTTPRequest) ([]byte, error) {
	method := []byte(req.Method)
	target := []byte(req.Target)
	headers := []byte(req.Headers)
	body := req.Body
	if body == nil {
		body = []byte{}
	}
	if len(method) > 255 {
		return nil, fmt.Errorf("%w: HTTP method too long", ErrInner)
	}
	if len(target) > 65535 || len(headers) > 65535 {
		return nil, fmt.Errorf("%w: HTTP target/headers too long", ErrInner)
	}
	out := make([]byte, 1+len(method)+2+len(target)+2+len(headers)+4+len(body))
	pos := 0
	out[pos] = byte(len(method))
	pos++
	copy(out[pos:], method)
	pos += len(method)
	binary.BigEndian.PutUint16(out[pos:pos+2], uint16(len(target)))
	pos += 2
	copy(out[pos:], target)
	pos += len(target)
	binary.BigEndian.PutUint16(out[pos:pos+2], uint16(len(headers)))
	pos += 2
	copy(out[pos:], headers)
	pos += len(headers)
	binary.BigEndian.PutUint32(out[pos:pos+4], uint32(len(body)))
	pos += 4
	copy(out[pos:], body)
	return out, nil
}

// DecodeHTTPRequest parses a binary request payload.
// Method is uppercased; absolute URIs are refused (host-side policy).
func DecodeHTTPRequest(payload []byte) (HTTPRequest, error) {
	if len(payload) < 1 {
		return HTTPRequest{}, fmt.Errorf("%w: short http req", ErrInner)
	}
	mlen := int(payload[0])
	pos := 1
	if pos+mlen+2 > len(payload) {
		return HTTPRequest{}, fmt.Errorf("%w: short http req", ErrInner)
	}
	method := strings.ToUpper(strings.TrimSpace(string(payload[pos : pos+mlen])))
	pos += mlen
	tlen := int(binary.BigEndian.Uint16(payload[pos : pos+2]))
	pos += 2
	if pos+tlen+2 > len(payload) {
		return HTTPRequest{}, fmt.Errorf("%w: short http req", ErrInner)
	}
	target := string(payload[pos : pos+tlen])
	if target == "" {
		target = "/"
	}
	pos += tlen
	hlen := int(binary.BigEndian.Uint16(payload[pos : pos+2]))
	pos += 2
	if pos+hlen+4 > len(payload) {
		return HTTPRequest{}, fmt.Errorf("%w: short http req", ErrInner)
	}
	headers := string(payload[pos : pos+hlen])
	pos += hlen
	blen := int(binary.BigEndian.Uint32(payload[pos : pos+4]))
	pos += 4
	if blen < 0 || pos+blen > len(payload) {
		return HTTPRequest{}, fmt.Errorf("%w: short http body", ErrInner)
	}
	body := append([]byte(nil), payload[pos:pos+blen]...)
	if strings.Contains(target, "://") || strings.HasPrefix(target, "//") {
		return HTTPRequest{}, fmt.Errorf("%w: absolute URI refused", ErrInner)
	}
	return HTTPRequest{Method: method, Target: target, Headers: headers, Body: body}, nil
}

// HTTPResponse is the binary response payload inside TYPE_HTTP_RES.
type HTTPResponse struct {
	Status  int
	Headers string
	Body    []byte
}

// EncodeHTTPResponse packs status/headers/body (binary shape).
func EncodeHTTPResponse(res HTTPResponse) ([]byte, error) {
	if res.Status < 100 || res.Status > 999 {
		return nil, fmt.Errorf("%w: invalid HTTP status", ErrInner)
	}
	headers := []byte(res.Headers)
	body := res.Body
	if body == nil {
		body = []byte{}
	}
	if len(headers) > 65535 {
		return nil, fmt.Errorf("%w: HTTP headers too long", ErrInner)
	}
	out := make([]byte, 2+2+len(headers)+4+len(body))
	binary.BigEndian.PutUint16(out[0:2], uint16(res.Status))
	binary.BigEndian.PutUint16(out[2:4], uint16(len(headers)))
	copy(out[4:], headers)
	pos := 4 + len(headers)
	binary.BigEndian.PutUint32(out[pos:pos+4], uint32(len(body)))
	copy(out[pos+4:], body)
	return out, nil
}

// DecodeHTTPResponse parses the binary response payload (not HTTP/1.1 text).
func DecodeHTTPResponse(payload []byte) (HTTPResponse, error) {
	if len(payload) >= 5 && string(payload[:5]) == "HTTP/" {
		return HTTPResponse{}, fmt.Errorf("%w: HTTP/1.1 response decode not in this slice", ErrInner)
	}
	if len(payload) < 8 {
		return HTTPResponse{}, fmt.Errorf("%w: short http res", ErrInner)
	}
	status := int(binary.BigEndian.Uint16(payload[0:2]))
	hlen := int(binary.BigEndian.Uint16(payload[2:4]))
	pos := 4
	if pos+hlen+4 > len(payload) {
		return HTTPResponse{}, fmt.Errorf("%w: short http res", ErrInner)
	}
	headers := string(payload[pos : pos+hlen])
	pos += hlen
	blen := int(binary.BigEndian.Uint32(payload[pos : pos+4]))
	pos += 4
	if blen < 0 || pos+blen > len(payload) {
		return HTTPResponse{}, fmt.Errorf("%w: short http body", ErrInner)
	}
	body := append([]byte(nil), payload[pos:pos+blen]...)
	return HTTPResponse{Status: status, Headers: headers, Body: body}, nil
}
