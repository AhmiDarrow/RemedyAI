package httpapi

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// attachNativeMailCalendar puts the Go Google implementations in front of the
// mail and calendar tools. A home whose mail or calendar is a connected Google
// account is served natively and never starts the Python worker. Any other
// provider (IMAP/SMTP, CalDAV) falls through to the worker as before.
func attachNativeMailCalendar(registry *tools.Registry, homeDir string) error {
	if registry == nil {
		return nil
	}
	type native func(context.Context, string, map[string]any) (map[string]any, error)
	rows := []struct {
		id, kind string
		run      native
	}{
		{"mail.list", "mail", toolMailListGoogle},
		{"mail.send", "mail", toolMailSendGoogle},
		{"calendar.list_events", "calendar", toolCalendarListGoogle},
		{"calendar.create_event", "calendar", toolCalendarCreateGoogle},
	}
	for _, row := range rows {
		row := row
		if _, err := registry.Resolve(row.id, 1); err != nil {
			continue // not registered on this runtime (no worker attached)
		}
		err := registry.WrapExecutor(row.id, 1, func(next tools.Executor) tools.Executor {
			return tools.ExecutorFunc(func(ctx context.Context, req tools.Request) (tools.Result, error) {
				if !googleMailCalendarAvailable(homeDir, row.kind) {
					return next.Execute(ctx, req)
				}
				input := map[string]any{}
				if len(req.Input) > 0 {
					if err := json.Unmarshal(req.Input, &input); err != nil {
						return tools.Result{}, fmt.Errorf("%w: %v", tools.ErrInvalidInput, err)
					}
				}
				out, err := row.run(ctx, homeDir, input)
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
