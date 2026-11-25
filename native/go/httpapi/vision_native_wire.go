package httpapi

// NewNativeVisionWorker is the vision service without Python: it downloads,
// verifies and supervises the local image model server itself.
func NewNativeVisionWorker() VisionWorker { return newNativeVisionWorker() }

// noteVisionUse tells the vision worker its server was just chosen for a
// turn, which resets its idle stop. A no-op until a server installs a worker
// that keeps such a timer.
var noteVisionUse = func() {}

// bindVisionUse points noteVisionUse at w when w keeps an idle timer.
func bindVisionUse(w VisionWorker) {
	if n, ok := w.(interface{ NoteUse() }); ok {
		noteVisionUse = n.NoteUse
	}
}
