package tools

// GoBoundField marks a request whose path-binding inputs (home_dir,
// workspace_root, project_path) were set by the Go runtime rather than the
// model. httpapi's injection helper sets it inside the input object, after
// deleting any model-supplied copy.
const GoBoundField = "_go_bound"
