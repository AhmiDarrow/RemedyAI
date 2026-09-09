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
	ErrDuplicateCall   = errors.New("duplicate active correlation ID")
	ErrTooManyCalls    = errors.New("too many concurrent Remedy IPC calls")
)

const (
	maxConcurrentCalls = 256
	// writeQueueDepth bounds frames waiting for the writer goroutine. Callers
	// block on enqueue (respecting ctx) rather than growing memory when the
	// peer stops draining the pipe.
	writeQueueDepth = 64
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
	stopClose := context.AfterFunc(ctx, func() { _ = conn.Close() })
	defer stopClose()
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
		if _, exists := calls[frame.CorrelationID]; exists {
			callsMu.Unlock()
			cancel()
			writeError(conn, &writeMu, frame.CorrelationID, ErrDuplicateCall)
			continue
		}
		if len(calls) >= maxConcurrentCalls {
			callsMu.Unlock()
			cancel()
			writeError(conn, &writeMu, frame.CorrelationID, ErrTooManyCalls)
			continue
		}
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

func writeError(conn net.Conn, mu *sync.Mutex, id [16]byte, err error) {
	mu.Lock()
	defer mu.Unlock()
	_ = protocol.WriteFrame(conn, protocol.Frame{
		Kind:          protocol.KindToolResult,
		Flags:         1,
		CorrelationID: id,
		Payload:       []byte(err.Error()),
	})
}

// outbound is one frame queued for the writer goroutine. done (when non-nil)
// receives the write error so a Call can fail fast on a broken pipe.
type outbound struct {
	frame protocol.Frame
	done  chan error
}

// Client multiplexes correlated request/response frames over one connection.
//
// A single writer goroutine owns the socket for writes: Call enqueues onto a
// bounded queue and waits on ctx, so one slow write (a full pipe buffer while
// the worker is busy) never parks other callers on a mutex. Cancels are
// fire-and-forget through the same queue. Responses for unknown or already
// finished correlation IDs are dropped.
type Client struct {
	conn    net.Conn
	mu      sync.Mutex
	pending map[[16]byte]chan protocol.Frame
	queue   chan outbound
	done    chan struct{}
	once    sync.Once
}

func NewClient(conn net.Conn) *Client {
	c := &Client{
		conn:    conn,
		pending: make(map[[16]byte]chan protocol.Frame),
		queue:   make(chan outbound, writeQueueDepth),
		done:    make(chan struct{}),
	}
	go c.readLoop()
	go c.writeLoop()
	return c
}

// Close tears the connection down; pending and future calls fail with
// ErrDisconnected.
func (c *Client) Close() error { c.shutdown(); return c.conn.Close() }

// Done is closed once the connection is no longer usable (peer EOF, write
// failure, or Close). Supervisors wait on it to detect a dead worker.
func (c *Client) Done() <-chan struct{} { return c.done }

// Call sends request and waits for the correlated response. ctx cancellation
// sends a best-effort KindCancel and returns ctx.Err(); the late response, if
// any, is dropped.
func (c *Client) Call(ctx context.Context, request protocol.Frame) (protocol.Frame, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-c.done:
		return protocol.Frame{}, ErrDisconnected
	default:
	}
	response := make(chan protocol.Frame, 1)
	c.mu.Lock()
	if _, exists := c.pending[request.CorrelationID]; exists {
		c.mu.Unlock()
		return protocol.Frame{}, ErrDuplicateCall
	}
	c.pending[request.CorrelationID] = response
	c.mu.Unlock()
	defer func() { c.mu.Lock(); delete(c.pending, request.CorrelationID); c.mu.Unlock() }()

	written := make(chan error, 1)
	select {
	case c.queue <- outbound{frame: request, done: written}:
	case <-ctx.Done():
		return protocol.Frame{}, ctx.Err()
	case <-c.done:
		return protocol.Frame{}, ErrDisconnected
	}
	select {
	case frame := <-response:
		return frame, nil
	case err := <-written:
		if err != nil {
			return protocol.Frame{}, err
		}
	case <-ctx.Done():
		c.Cancel(request.CorrelationID)
		return protocol.Frame{}, ctx.Err()
	case <-c.done:
		return protocol.Frame{}, ErrDisconnected
	}
	select {
	case frame := <-response:
		return frame, nil
	case <-ctx.Done():
		c.Cancel(request.CorrelationID)
		return protocol.Frame{}, ctx.Err()
	case <-c.done:
		return protocol.Frame{}, ErrDisconnected
	}
}

// Cancel queues a KindCancel for id without waiting. It never blocks a caller
// on a full queue: a cancel the peer never sees only costs one late response,
// which the read loop drops anyway.
func (c *Client) Cancel(id [16]byte) {
	frame := protocol.Frame{Kind: protocol.KindCancel, CorrelationID: id}
	select {
	case c.queue <- outbound{frame: frame}:
	case <-c.done:
	default:
		go func() {
			select {
			case c.queue <- outbound{frame: frame}:
			case <-c.done:
			}
		}()
	}
}

func (c *Client) writeLoop() {
	defer c.shutdown()
	for {
		select {
		case <-c.done:
			c.drainQueue()
			return
		case item := <-c.queue:
			err := protocol.WriteFrame(c.conn, item.frame)
			if item.done != nil {
				item.done <- err
			}
			if err != nil {
				c.drainQueue()
				return
			}
		}
	}
}

// drainQueue fails every queued sender once the writer has stopped.
func (c *Client) drainQueue() {
	for {
		select {
		case item := <-c.queue:
			if item.done != nil {
				item.done <- ErrDisconnected
			}
		default:
			return
		}
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
