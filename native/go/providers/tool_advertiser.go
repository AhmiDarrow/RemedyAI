package providers

// ToolAdvertiser is a model adapter that can be told which Tool ABI
// descriptors to advertise. Every adapter renders the surface in its own
// wire shape — OpenAI function schemas, Anthropic strict tools — so the turn
// runner has one seam instead of a type assertion per provider.
type ToolAdvertiser interface {
	SetTools(tools []RegistryTool)
}

var (
	_ ToolAdvertiser = (*OpenAICompat)(nil)
	_ ToolAdvertiser = (*Anthropic)(nil)
)

// SetTools advertises the Tool ABI surface as OpenAI function schemas and
// keeps the advertised→ABI map the turn runner resolves tool calls through.
func (c *OpenAICompat) SetTools(list []RegistryTool) {
	if c == nil {
		return
	}
	if len(list) == 0 {
		c.Tools, c.ToolNameMap = nil, nil
		return
	}
	c.Tools, c.ToolNameMap = ToolSchemasFromRegistryMapped(list)
}
