package cli

// notYet answers a command whose Go port has not landed. Each group's
// stub lives in its own stub_<group>.go so a port deletes just that file.
func notYet(c *Ctx) int {
	c.Errorf("remedy %v: not in the Go CLI yet\n", c.P.Path)
	return 2
}
