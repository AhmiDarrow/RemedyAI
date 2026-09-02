package providers

import (
	"errors"
	"fmt"
	"sync"
	"testing"
)

type fakeProvider string

func (p fakeProvider) ID() string { return string(p) }

func TestRouterKeepsSessionBindingsIsolated(t *testing.T) {
	router := NewRouter()
	for _, id := range []string{"local", "remote"} {
		if err := router.Register(fakeProvider(id)); err != nil {
			t.Fatal(err)
		}
	}
	if err := router.Bind("desktop", "local"); err != nil {
		t.Fatal(err)
	}
	if err := router.Bind("messenger", "remote"); err != nil {
		t.Fatal(err)
	}
	got, _ := router.Resolve("desktop")
	if got.ID() != "local" {
		t.Fatalf("desktop provider = %q", got.ID())
	}
	got, _ = router.Resolve("messenger")
	if got.ID() != "remote" {
		t.Fatalf("messenger provider = %q", got.ID())
	}
}

func TestRouterConcurrentBindings(t *testing.T) {
	router := NewRouter()
	if err := router.Register(fakeProvider("provider")); err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			session := fmt.Sprintf("session-%d", i)
			if err := router.Bind(session, "provider"); err != nil {
				t.Error(err)
				return
			}
			if _, err := router.Resolve(session); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
}

func TestRouterRejectsUnknownBindings(t *testing.T) {
	router := NewRouter()
	if err := router.Bind("session", "missing"); !errors.Is(err, ErrProviderNotFound) {
		t.Fatalf("Bind unknown: %v", err)
	}
	if _, err := router.Resolve("missing"); !errors.Is(err, ErrSessionNotBound) {
		t.Fatalf("Resolve unbound: %v", err)
	}
}
