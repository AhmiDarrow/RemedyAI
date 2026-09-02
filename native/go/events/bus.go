// Package events provides Remedy's typed durable event bus.
package events

import (
	"bufio"
	"encoding/binary"
	"encoding/json"
	"errors"
	"hash/crc32"
	"io"
	"os"
	"sync"
	"time"
)

const maxEventSize = 16 << 20

var ErrCorruptLog = errors.New("event log checksum mismatch")

type Event struct {
	Sequence uint64          `json:"sequence"`
	Type     string          `json:"type"`
	Source   string          `json:"source"`
	At       time.Time       `json:"at"`
	Data     json.RawMessage `json:"data,omitempty"`
}
type Filter struct {
	Types  map[string]bool
	Source string
}

func (f Filter) matches(event Event) bool {
	return (len(f.Types) == 0 || f.Types[event.Type]) && (f.Source == "" || f.Source == event.Source)
}

type Overflow uint8

const (
	DropNewest Overflow = iota
	DropOldest
	Disconnect
)

type Stats struct {
	Dropped      uint64
	Disconnected bool
}
type subscription struct {
	id       uint64
	filter   Filter
	overflow Overflow
	channel  chan Event
	stats    Stats
	closed   bool
}
type Subscription struct {
	bus *Bus
	id  uint64
	C   <-chan Event
}

func (s Subscription) Close() { s.bus.unsubscribe(s.id) }
func (s Subscription) Stats() Stats {
	s.bus.mu.RLock()
	defer s.bus.mu.RUnlock()
	sub := s.bus.subscribers[s.id]
	if sub == nil {
		return Stats{Disconnected: true}
	}
	return sub.stats
}

type Bus struct {
	mu          sync.RWMutex
	file        *os.File
	events      []Event
	next        uint64
	subscribers map[uint64]*subscription
	nextSub     uint64
}

func Open(path string) (*Bus, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	bus := &Bus{file: file, next: 1, subscribers: make(map[uint64]*subscription)}
	valid, err := bus.replayDisk()
	if err != nil {
		file.Close()
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, err
	}
	if info.Size() != valid {
		if err := file.Truncate(valid); err != nil {
			file.Close()
			return nil, err
		}
	}
	_, err = file.Seek(0, io.SeekEnd)
	if err != nil {
		file.Close()
		return nil, err
	}
	return bus, nil
}
func (b *Bus) Close() error {
	b.mu.Lock()
	defer b.mu.Unlock()
	for id, sub := range b.subscribers {
		close(sub.channel)
		delete(b.subscribers, id)
	}
	return b.file.Close()
}

func (b *Bus) Publish(event Event) (Event, error) {
	if event.Type == "" || event.Source == "" {
		return Event{}, errors.New("invalid event")
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	event.Sequence = b.next
	b.next++
	if event.At.IsZero() {
		event.At = time.Now().UTC()
	}
	payload, err := json.Marshal(event)
	if err != nil {
		return Event{}, err
	}
	if len(payload) > maxEventSize {
		return Event{}, errors.New("event too large")
	}
	var header [8]byte
	binary.LittleEndian.PutUint32(header[:4], uint32(len(payload)))
	binary.LittleEndian.PutUint32(header[4:], crc32.ChecksumIEEE(payload))
	if _, err = b.file.Write(header[:]); err != nil {
		return Event{}, err
	}
	if _, err = b.file.Write(payload); err != nil {
		return Event{}, err
	}
	if err = b.file.Sync(); err != nil {
		return Event{}, err
	}
	b.events = append(b.events, clone(event))
	for id, sub := range b.subscribers {
		if !sub.filter.matches(event) {
			continue
		}
		switch sub.overflow {
		case DropNewest:
			select {
			case sub.channel <- clone(event):
			default:
				sub.stats.Dropped++
			}
		case DropOldest:
			select {
			case sub.channel <- clone(event):
			default:
				select {
				case <-sub.channel:
					sub.stats.Dropped++
				default:
					{
					}
				}
				select {
				case sub.channel <- clone(event):
				default:
					sub.stats.Dropped++
				}
			}
		case Disconnect:
			select {
			case sub.channel <- clone(event):
			default:
				sub.stats.Disconnected = true
				sub.closed = true
				close(sub.channel)
				delete(b.subscribers, id)
			}
		}
	}
	return clone(event), nil
}

func (b *Bus) Subscribe(filter Filter, capacity int, overflow Overflow) Subscription {
	if capacity < 1 {
		capacity = 1
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	b.nextSub++
	sub := &subscription{id: b.nextSub, filter: filter, overflow: overflow, channel: make(chan Event, capacity)}
	b.subscribers[sub.id] = sub
	return Subscription{bus: b, id: sub.id, C: sub.channel}
}
func (b *Bus) Replay(from uint64, limit int) []Event {
	if limit <= 0 || limit > 10000 {
		limit = 1000
	}
	b.mu.RLock()
	defer b.mu.RUnlock()
	result := make([]Event, 0)
	for _, event := range b.events {
		if event.Sequence >= from {
			result = append(result, clone(event))
			if len(result) == limit {
				break
			}
		}
	}
	return result
}
func (b *Bus) unsubscribe(id uint64) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if sub := b.subscribers[id]; sub != nil && !sub.closed {
		sub.closed = true
		close(sub.channel)
		delete(b.subscribers, id)
	}
}
func (b *Bus) replayDisk() (int64, error) {
	reader := bufio.NewReader(b.file)
	var offset int64
	for {
		var header [8]byte
		_, err := io.ReadFull(reader, header[:])
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return offset, nil
		}
		if err != nil {
			return offset, err
		}
		length := binary.LittleEndian.Uint32(header[:4])
		if length > maxEventSize {
			return offset, ErrCorruptLog
		}
		payload := make([]byte, length)
		if _, err := io.ReadFull(reader, payload); errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return offset, nil
		} else if err != nil {
			return offset, err
		}
		if crc32.ChecksumIEEE(payload) != binary.LittleEndian.Uint32(header[4:]) {
			return offset, ErrCorruptLog
		}
		var event Event
		if json.Unmarshal(payload, &event) != nil || event.Sequence != b.next {
			return offset, ErrCorruptLog
		}
		b.events = append(b.events, event)
		b.next++
		offset += int64(8 + length)
	}
}
func clone(event Event) Event { event.Data = append(json.RawMessage(nil), event.Data...); return event }
