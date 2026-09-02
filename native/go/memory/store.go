// Package memory provides durable typed memory records and bounded retrieval.
package memory

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"hash/crc32"
	"io"
	"os"
	"strings"
	"sync"
	"time"
)

const MaxRecordSize = 16 << 20

var ErrCorruptLog = errors.New("memory log checksum mismatch")

type Kind string

const (
	Episodic   Kind = "episodic"
	Semantic   Kind = "semantic"
	Procedural Kind = "procedural"
	Working    Kind = "working"
	Relational Kind = "relational"
)

type Record struct {
	ID        string    `json:"id"`
	Namespace string    `json:"namespace"`
	Kind      Kind      `json:"kind"`
	Key       string    `json:"key"`
	Content   string    `json:"content"`
	Related   []string  `json:"related,omitempty"`
	CreatedAt time.Time `json:"created_at"`
}
type Query struct {
	Namespace string
	Kinds     []Kind
	Text      string
	Limit     int
}
type Match struct {
	Record Record
	Score  int
}

type Store struct {
	mu         sync.RWMutex
	file       *os.File
	records    []Record
	searchText []string
	latest     map[string]int
}

func Open(path string) (*Store, error) {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	store := &Store{file: file, latest: make(map[string]int)}
	valid, err := store.replay()
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
	return store, nil
}

func (s *Store) Close() error { s.mu.Lock(); defer s.mu.Unlock(); return s.file.Close() }

func (s *Store) Append(record Record) error {
	if record.ID == "" || record.Namespace == "" || record.Kind == "" || record.Key == "" {
		return errors.New("invalid memory record")
	}
	payload, err := json.Marshal(record)
	if err != nil {
		return err
	}
	if len(payload) > MaxRecordSize {
		return errors.New("memory record too large")
	}
	var header [8]byte
	binary.LittleEndian.PutUint32(header[0:4], uint32(len(payload)))
	binary.LittleEndian.PutUint32(header[4:8], crc32.ChecksumIEEE(payload))
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, err = s.file.Write(header[:]); err != nil {
		return err
	}
	if _, err = s.file.Write(payload); err != nil {
		return err
	}
	if err = s.file.Sync(); err != nil {
		return err
	}
	s.index(record)
	return nil
}

func (s *Store) GetLatest(namespace string, kind Kind, key string) (Record, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	index, ok := s.latest[indexKey(namespace, kind, key)]
	if !ok {
		return Record{}, false
	}
	return clone(s.records[index]), true
}

func (s *Store) Search(query Query) []Match {
	limit := query.Limit
	if limit <= 0 || limit > 100 {
		limit = 20
	}
	terms := strings.Fields(strings.ToLower(query.Text))
	allowed := make(map[Kind]bool)
	for _, kind := range query.Kinds {
		allowed[kind] = true
	}
	s.mu.RLock()
	defer s.mu.RUnlock()
	type candidate struct{ index, score int }
	candidates := make([]candidate, 0, limit)
	for index, record := range s.records {
		if record.Namespace != query.Namespace {
			continue
		}
		if len(allowed) > 0 && !allowed[record.Kind] {
			continue
		}
		haystack := s.searchText[index]
		score := 0
		for _, term := range terms {
			score += strings.Count(haystack, term)
		}
		if len(terms) == 0 || score > 0 {
			value := candidate{index: index, score: score}
			position := len(candidates)
			for position > 0 {
				prior := candidates[position-1]
				if prior.score > value.score || (prior.score == value.score && !s.records[value.index].CreatedAt.After(s.records[prior.index].CreatedAt)) {
					break
				}
				position--
			}
			if position < limit {
				if len(candidates) < limit {
					candidates = append(candidates, candidate{})
				}
				copy(candidates[position+1:], candidates[position:len(candidates)-1])
				candidates[position] = value
			}
		}
	}
	matches := make([]Match, len(candidates))
	for index, candidate := range candidates {
		matches[index] = Match{Record: clone(s.records[candidate.index]), Score: candidate.score}
	}
	return matches
}

func (s *Store) replay() (int64, error) {
	reader := bufio.NewReader(s.file)
	var offset int64
	for {
		var header [8]byte
		n, err := io.ReadFull(reader, header[:])
		if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return offset, nil
		}
		if err != nil {
			return offset, err
		}
		if n != 8 {
			return offset, nil
		}
		length := binary.LittleEndian.Uint32(header[0:4])
		if length > MaxRecordSize {
			return offset, ErrCorruptLog
		}
		payload := make([]byte, length)
		if _, err := io.ReadFull(reader, payload); errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
			return offset, nil
		} else if err != nil {
			return offset, err
		}
		if crc32.ChecksumIEEE(payload) != binary.LittleEndian.Uint32(header[4:8]) {
			return offset, ErrCorruptLog
		}
		var record Record
		if err := json.NewDecoder(bytes.NewReader(payload)).Decode(&record); err != nil {
			return offset, ErrCorruptLog
		}
		s.index(record)
		offset += int64(8 + length)
	}
}

func (s *Store) index(record Record) {
	s.records = append(s.records, clone(record))
	s.searchText = append(s.searchText, strings.ToLower(record.Key+" "+record.Content))
	s.latest[indexKey(record.Namespace, record.Kind, record.Key)] = len(s.records) - 1
}
func indexKey(namespace string, kind Kind, key string) string {
	return namespace + "\x00" + string(kind) + "\x00" + key
}
func clone(record Record) Record {
	record.Related = append([]string(nil), record.Related...)
	return record
}
