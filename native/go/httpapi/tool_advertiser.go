package httpapi

import (
	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// advertiseToolSurface pushes the Tool ABI surface onto any adapter that
// implements providers.ToolAdvertiser — one seam for every provider instead of
// a type assertion per adapter. coding selects the mid-build coding pack;
// loopback runtimes always get it, along with the local request fitter.
func (r *CognitionTurnRunner) advertiseToolSurface(model cognition.Model, coding bool) {
	if r == nil || r.Registry == nil || model == nil {
		return
	}
	adv, ok := model.(providers.ToolAdvertiser)
	if !ok {
		return
	}
	useCoding := coding
	if oc, isCompat := model.(*providers.OpenAICompat); isCompat {
		// The local vision helper is a 2B VLM for basic chat — never show it
		// the tool surface.
		if isVisionHelperCompat(oc) {
			adv.SetTools(nil)
			return
		}
		// Only a loopback runtime gets the fitter and its small n_ctx: a cloud
		// model switching to the coding pack mid-build must keep its real
		// context window, or the fitter would shred a long transcript.
		if providers.IsLocalBaseURL(oc.BaseURL) {
			oc.LocalFit = true
			if oc.NCtx <= 0 {
				oc.NCtx = providers.LocalContextWindow
			}
			useCoding = true
		}
	}
	adv.SetTools(r.registryToolSurface(useCoding))
}

// registryToolSurface is the model-visible Tool ABI surface in registry order.
// A coding turn narrows it to the coding pack, but never to nothing.
func (r *CognitionTurnRunner) registryToolSurface(coding bool) []providers.RegistryTool {
	if r == nil || r.Registry == nil {
		return nil
	}
	list := r.Registry.List()
	meta := make([]providers.RegistryTool, 0, len(list))
	for _, d := range list {
		if !modelVisibleTool(d.ID) {
			continue
		}
		if coding && !isCodingPackTool(d.ID) {
			continue
		}
		meta = append(meta, providers.RegistryTool{
			ID:          d.ID,
			Description: d.Description,
			// Fields the runtime binds (workspace_root, home_dir, session_id)
			// are stripped before the model sees them: advertising a field
			// whose value is overwritten invites the model to set it, wonder
			// why it had no effect, and spend a round finding out.
			InputSchema: tools.ModelInputSchema(d.InputSchema),
		})
	}
	if coding && len(meta) == 0 {
		return r.registryToolSurface(false)
	}
	return meta
}
