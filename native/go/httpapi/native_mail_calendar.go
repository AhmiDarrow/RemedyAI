package httpapi

import (
	"context"
	"encoding/json"
	"fmt"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// attachNativeMailCalendar serves the mail and calendar tools from Go: a
// connected Google account first, otherwise the IMAP/SMTP mailbox or CalDAV
// calendar the owner configured. With neither, the second provider reports
// that nothing is connected. The Python worker is never called.
func attachNativeMailCalendar(registry *tools.Registry, homeDir string) error {
	if registry == nil {
		return nil
	}
	type native func(context.Context, string, map[string]any) (map[string]any, error)
	rows := []struct {
		id, kind string
		run      native // Google
		other    native // IMAP/SMTP or CalDAV
	}{
		{"mail.list", "mail", toolMailListGoogle, toolMailListIMAP},
		{"mail.send", "mail", toolMailSendGoogle, toolMailSendSMTP},
		{"calendar.list_events", "calendar", toolCalendarListGoogle, toolCalendarListCalDAV},
		{"calendar.create_event", "calendar", toolCalendarCreateGoogle, toolCalendarCreateCalDAV},
	}
	for _, row := range rows {
		row := row
		if _, err := registry.Resolve(row.id, 1); err != nil {
			continue // not registered on this runtime (no worker attached)
		}
		err := registry.WrapExecutor(row.id, 1, func(tools.Executor) tools.Executor {
			return tools.ExecutorFunc(func(ctx context.Context, req tools.Request) (tools.Result, error) {
				run := row.run
				if !googleMailCalendarAvailable(homeDir, row.kind) {
					run = row.other
				}
				input := map[string]any{}
				if len(req.Input) > 0 {
					if err := json.Unmarshal(req.Input, &input); err != nil {
						return tools.Result{}, fmt.Errorf("%w: %v", tools.ErrInvalidInput, err)
					}
				}
				out, err := run(ctx, homeDir, input)
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
