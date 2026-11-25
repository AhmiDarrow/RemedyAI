package httpapi

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// attachNativePeerTools serves the peer.* tools (the other bots the owner
// connected) from Go, so asking a peer never starts the Python worker.
func attachNativePeerTools(registry *tools.Registry, s *Server) error {
	if registry == nil || s == nil {
		return nil
	}
	type native func(context.Context, *Server, map[string]any) (map[string]any, error)
	for id, run := range map[string]native{
		"peer.list": toolPeerList, "peer.ask": toolPeerAsk,
		"peer.delegate": toolPeerDelegate, "peer.status": toolPeerStatus,
	} {
		run := run
		if _, err := registry.Resolve(id, 1); err != nil {
			continue
		}
		err := registry.WrapExecutor(id, 1, func(tools.Executor) tools.Executor {
			return tools.ExecutorFunc(func(ctx context.Context, req tools.Request) (tools.Result, error) {
				input := map[string]any{}
				if len(req.Input) > 0 {
					if err := json.Unmarshal(req.Input, &input); err != nil {
						return tools.Result{}, fmt.Errorf("%w: %v", tools.ErrInvalidInput, err)
					}
				}
				out, err := run(ctx, s, input)
				if err != nil {
					return tools.Result{}, err
				}
				raw, err := json.Marshal(out)
				if err != nil {
					return tools.Result{}, err
				}
				return tools.Result{Output: raw}, nil
			})
		})
		if err != nil {
			return err
		}
	}
	return nil
}
