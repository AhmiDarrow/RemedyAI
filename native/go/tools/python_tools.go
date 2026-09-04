package tools

import (
	"encoding/json"
	"errors"
	"fmt"
)

// RegisterPythonWorkerTools installs Tool ABI descriptors that execute over
// RMDY KindToolRequest frames on the supervised Python worker. caller must be
// live; there is no in-process Python fallback.
func RegisterPythonWorkerTools(registry *Registry, caller FrameCaller) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	if caller == nil {
		return errors.New("python worker frame caller is required")
	}
	exec := NewRMDYExecutor(caller)

	if err := registry.Register(Descriptor{
		ID:          "text.slugify",
		Version:     1,
		Description: "Slugify text in the Python worker",
		Runtime:     RuntimePython,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string"}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["slug"],
			"properties":{"slug":{"type":"string"}},
			"additionalProperties":false
		}`),
	}, exec); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "text.word_count",
		Version:     1,
		Description: "Count whitespace-separated words in the Python worker",
		Runtime:     RuntimePython,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string"}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["words"],
			"properties":{"words":{"type":"integer","minimum":0}},
			"additionalProperties":false
		}`),
	}, exec); err != nil {
		return err
	}

	return nil
}
