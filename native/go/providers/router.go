// Package providers binds sessions to model providers without global-state leakage.
package providers

import (
	"errors"
	"sync"
)

var (
	ErrProviderNotFound = errors.New("provider is not registered")
	ErrSessionNotBound  = errors.New("session has no provider binding")
)

type Provider interface{ ID() string }

type Router struct {
	mu        sync.RWMutex
	providers map[string]Provider
	sessions  map[string]string
}

func NewRouter() *Router {
	return &Router{providers: make(map[string]Provider), sessions: make(map[string]string)}
}

func (r *Router) Register(provider Provider) error {
	if provider == nil || provider.ID() == "" {
		return ErrProviderNotFound
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.providers[provider.ID()] = provider
	return nil
}

func (r *Router) Bind(sessionID, providerID string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, ok := r.providers[providerID]; !ok {
		return ErrProviderNotFound
	}
	r.sessions[sessionID] = providerID
	return nil
}

func (r *Router) Resolve(sessionID string) (Provider, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	providerID, ok := r.sessions[sessionID]
	if !ok {
		return nil, ErrSessionNotBound
	}
	provider, ok := r.providers[providerID]
	if !ok {
		return nil, ErrProviderNotFound
	}
	return provider, nil
}

func (r *Router) Unbind(sessionID string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.sessions, sessionID)
}
