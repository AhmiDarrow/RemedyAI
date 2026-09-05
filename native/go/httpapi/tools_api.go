package httpapi

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// ToolRegistryProvider is implemented by CognitionTurnRunner so CLI / debug
// surfaces can list and invoke the live Tool ABI without Python BasicRuntime.
type ToolRegistryProvider interface {
	ToolRegistry() *tools.Registry
}

// ToolRegistry exposes the turn-time Tool ABI registry.
func (r *CognitionTurnRunner) ToolRegistry() *tools.Registry {
	if r == nil {
		return nil
	}
	return r.Registry
}

func (s *Server) toolRegistry() *tools.Registry {
	if s == nil || s.runner == nil {
		return nil
	}
	if p, ok := s.runner.(ToolRegistryProvider); ok {
		return p.ToolRegistry()
	}
	return nil
}

func riskName(r tools.Risk) string {
	switch r {
	case tools.RiskReadOnly:
		return "read_only"
	case tools.RiskMutation:
		return "mutation"
	case tools.RiskCheckpoint:
		return "checkpoint"
	default:
		return "unknown"
	}
}

func toolPublic(d tools.Descriptor) map[string]any {
	caps := d.Capabilities
	if caps == nil {
		caps = []string{}
	}
	perms := d.Permissions
	if perms == nil {
		perms = []string{}
	}
	return map[string]any{
		"id":           d.ID,
		"version":      d.Version,
		"description":  d.Description,
		"runtime":      string(d.Runtime),
		"risk":         riskName(d.Risk),
		"capabilities": caps,
		"permissions":  perms,
		"input_schema": json.RawMessage(append(json.RawMessage(nil), d.InputSchema...)),
	}
}

func (s *Server) handleListTools(w http.ResponseWriter, r *http.Request) {
	reg := s.toolRegistry()
	if reg == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"detail": "Tool ABI registry unavailable (start remedy-runtime with CognitionTurnRunner)",
		})
		return
	}
	q := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("q")))
	listed := reg.List()
	out := make([]map[string]any, 0, len(listed))
	for _, d := range listed {
		if q != "" {
			hay := strings.ToLower(d.ID + " " + d.Description + " " + string(d.Runtime))
			if !strings.Contains(hay, q) {
				continue
			}
		}
		out = append(out, toolPublic(d))
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"tools": out,
		"count": len(out),
	})
}

type toolInvokeBody struct {
	ID      string          `json:"id"`
	Version uint32          `json:"version"`
	Input   json.RawMessage `json:"input"`
	Args    json.RawMessage `json:"args"` // alias for input (CLI familiarity)
}

func (s *Server) handleInvokeTool(w http.ResponseWriter, r *http.Request) {
	reg := s.toolRegistry()
	if reg == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{
			"detail": "Tool ABI registry unavailable (start remedy-runtime with CognitionTurnRunner)",
		})
		return
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "unable to read body"})
		return
	}
	var body toolInvokeBody
	if err := json.Unmarshal(raw, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	id := strings.TrimSpace(body.ID)
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "id is required"})
		return
	}
	input := body.Input
	if len(input) == 0 {
		input = body.Args
	}
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}

	desc, err := reg.Latest(id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "tool not found: " + id})
		return
	}
	version := body.Version
	if version == 0 {
		version = desc.Version
	} else {
		resolved, rerr := reg.Resolve(id, version)
		if rerr != nil {
			writeJSON(w, http.StatusNotFound, map[string]string{
				"detail": "tool version not found",
			})
			return
		}
		desc = resolved
	}

	req := tools.Request{
		ToolID:  desc.ID,
		Version: desc.Version,
		Input:   append(json.RawMessage(nil), input...),
	}
	req.CapabilityToken = RuntimeCapabilityToken(desc)

	result, execErr := reg.Execute(r.Context(), req)
	if execErr != nil {
		status := http.StatusBadRequest
		switch {
		case errors.Is(execErr, tools.ErrToolNotFound):
			status = http.StatusNotFound
		case errors.Is(execErr, tools.ErrAuthorizationRequired), errors.Is(execErr, tools.ErrUnauthorized):
			status = http.StatusForbidden
		}
		writeJSON(w, status, map[string]any{
			"ok":      false,
			"id":      id,
			"version": desc.Version,
			"error":   execErr.Error(),
		})
		return
	}
	var output any
	if len(result.Output) > 0 {
		_ = json.Unmarshal(result.Output, &output)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"id":      id,
		"version": desc.Version,
		"runtime": string(desc.Runtime),
		"risk":    riskName(desc.Risk),
		"output":  output,
	})
}
