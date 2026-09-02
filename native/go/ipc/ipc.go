// Package ipc provides Remedy's framed local request transport.
package ipc

import (
	"context"
	"errors"
	"io"
	"net"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

var (
	ErrInvalidEndpoint = errors.New("invalid Remedy IPC endpoint")
	ErrDisconnected    = errors.New("Remedy IPC disconnected")
)

type Handler interface {
	Handle(context.Context, protocol.Frame) ([]protocol.Frame, error)
}
type HandlerFunc func(context.Context, protocol.Frame) ([]protocol.Frame, error)

func (f HandlerFunc) Handle(ctx context.Context, frame protocol.Frame) ([]protocol.Frame, error) {
	return f(ctx, frame)
}

func Serve(ctx context.Context, listener net.Listener, handler Handler) error {
	go func() { <-ctx.Done(); _ = listener.Close() }()
	for {
		conn, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		go ServeConn(ctx, conn, handler)
	}
}

func ServeConn(ctx context.Context, conn net.Conn, handler Handler) {
	defer conn.Close()
	var writeMu sync.Mutex
	var callsMu sync.Mutex
	calls := make(map[[16]byte]context.CancelFunc)
	defer func() {
		callsMu.Lock()
		defer callsMu.Unlock()
		for _, cancel := range calls {
			cancel()
		}
	}()
	for {
		frame, err := protocol.ReadFrame(conn)
		if err != nil {
			return
		}
		if frame.Kind == protocol.KindCancel {
			callsMu.Lock()
			cancel := calls[frame.CorrelationID]
			callsMu.Unlock()
			if cancel != nil {
				cancel()
			}
			continue
		}
		callCtx, cancel := context.WithCancel(ctx)
		callsMu.Lock()
		calls[frame.CorrelationID] = cancel
		callsMu.Unlock()
		go func(request protocol.Frame) {
			defer cancel()
			defer func() { callsMu.Lock(); delete(calls, request.CorrelationID); callsMu.Unlock() }()
			responses, err := handler.Handle(callCtx, request)
			if err != nil {
				responses = []protocol.Frame{{Kind: protocol.KindToolResult, Flags: 1, Payload: []byte(err.Error())}}
			}
			writeMu.Lock()
			defer writeMu.Unlock()
			for _, response := range responses {
				response.CorrelationID = request.CorrelationID
				if protocol.WriteFrame(conn, response) != nil {
					return
				}
			}
		}(frame)
	}
}

type Client struct {
	conn    net.Conn
	writeMu sync.Mutex
	mu      sync.Mutex
	pending map[[16]byte]chan protocol.Frame
	done    chan struct{}
	once    sync.Once
}

func NewClient(conn net.Conn) *Client {
	c := &Client{conn: conn, pending: make(map[[16]byte]chan protocol.Frame), done: make(chan struct{})}
	go c.readLoop()
	return c
}

func (c *Client) Close() error { c.shutdown(); return c.conn.Close() }

func (c *Client) Call(ctx context.Context, request protocol.Frame) (protocol.Frame, error) {
	response := make(chan protocol.Frame, 1)
	c.mu.Lock()
	if _, exists := c.pending[request.CorrelationID]; exists {
		c.mu.Unlock()
		return protocol.Frame{}, errors.New("duplicate correlation ID")
	}
	c.pending[request.CorrelationID] = response
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, request.CorrelationID); c.mu.Unlock() }()
	c.writeMu.Lock()
	err := protocol.WriteFrame(c.conn, request)
	c.writeMu.Unlock()
	if err != nil {
		return protocol.Frame{}, err
	}
	select {
	case frame := <-response:
		return frame, nil
	case <-ctx.Done():
		c.writeMu.Lock()
		_ = protocol.WriteFrame(c.conn, protocol.Frame{Kind: protocol.KindCancel, CorrelationID: request.CorrelationID})
		c.writeMu.Unlock()
		return protocol.Frame{}, ctx.Err()
	case <-c.done:
		return protocol.Frame{}, ErrDisconnected
	}
}

func (c *Client) readLoop() {
	defer c.shutdown()
	for {
		frame, err := protocol.ReadFrame(c.conn)
		if err != nil {
			if !errors.Is(err, io.EOF) {
				_ = err
			}
			return
		}
		c.mu.Lock()
		target := c.pending[frame.CorrelationID]
		c.mu.Unlock()
		if target != nil {
			select {
			case target <- frame:
			default:
			}
		}
	}
}

func (c *Client) shutdown() { c.once.Do(func() { close(c.done) }) }
