// Package compat keeps the existing loopback HTTP product reachable during cutover.
package compat

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

var ErrUnsafeTarget = errors.New("compatibility HTTP target must remain on loopback")

type HTTPRequest struct {
	Method string            `json:"method"`
	Path   string            `json:"path"`
	Header map[string]string `json:"header,omitempty"`
	Body   []byte            `json:"body,omitempty"`
}

type HTTPResponse struct {
	Status int                 `json:"status"`
	Header map[string][]string `json:"header,omitempty"`
	Body   []byte              `json:"body,omitempty"`
}

type HTTPHandler struct {
	Base   *url.URL
	Client *http.Client
}

func NewHTTPHandler(baseURL string, client *http.Client) (*HTTPHandler, error) {
	base, err := url.Parse(baseURL)
	if err != nil || base.Scheme != "http" || base.User != nil {
		return nil, ErrUnsafeTarget
	}
	host := base.Hostname()
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
		return nil, ErrUnsafeTarget
	}
	if client == nil {
		client = &http.Client{}
	}
	guarded := *client
	priorRedirect := client.CheckRedirect
	guarded.CheckRedirect = func(request *http.Request, via []*http.Request) error {
		if request.URL.Scheme != base.Scheme || !strings.EqualFold(request.URL.Host, base.Host) {
			return ErrUnsafeTarget
		}
		if priorRedirect != nil {
			return priorRedirect(request, via)
		}
		return nil
	}
	return &HTTPHandler{Base: base, Client: &guarded}, nil
}

func (h *HTTPHandler) Handle(ctx context.Context, frame protocol.Frame) ([]protocol.Frame, error) {
	var request HTTPRequest
	if err := json.Unmarshal(frame.Payload, &request); err != nil {
		return nil, err
	}
	if request.Path == "" || !strings.HasPrefix(request.Path, "/") || strings.HasPrefix(request.Path, "//") {
		return nil, ErrUnsafeTarget
	}
	relative, err := url.ParseRequestURI(request.Path)
	if err != nil || relative.IsAbs() || relative.Host != "" || relative.User != nil || !strings.HasPrefix(relative.Path, "/") {
		return nil, ErrUnsafeTarget
	}
	target := *h.Base
	target.Path = relative.Path
	target.RawPath = relative.RawPath
	target.RawQuery = relative.RawQuery
	target.Fragment = ""
	httpRequest, err := http.NewRequestWithContext(ctx, request.Method, target.String(), bytes.NewReader(request.Body))
	if err != nil {
		return nil, err
	}
	for key, value := range request.Header {
		httpRequest.Header.Set(key, value)
	}
	response, err := h.Client.Do(httpRequest)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, protocol.MaxPayloadSize+1))
	if err != nil {
		return nil, err
	}
	if len(body) > protocol.MaxPayloadSize {
		return nil, protocol.ErrPayloadTooLarge
	}
	payload, err := json.Marshal(HTTPResponse{Status: response.StatusCode, Header: response.Header, Body: body})
	if err != nil {
		return nil, err
	}
	if len(payload) > protocol.MaxPayloadSize {
		return nil, protocol.ErrPayloadTooLarge
	}
	return []protocol.Frame{{Kind: protocol.KindToolResult, Payload: payload}}, nil
}
