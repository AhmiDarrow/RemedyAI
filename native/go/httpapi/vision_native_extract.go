package httpapi

// Archive extraction for llama.cpp runtimes (zip and tar.gz) with Zip-Slip /
// Tar-Slip protection. Mirrors install._extract_zip / _extract_tar.

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

const (
	nvMaxFiles       = 50_000
	nvMaxMemberBytes = int64(8_000_000_000)
	nvMaxTotalBytes  = nvMaxMemberBytes * 50
)

func nvExtractArchive(ctx context.Context, archive, dest string) error {
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return err
	}
	root, err := filepath.EvalSymlinks(dest)
	if err != nil {
		return err
	}
	ex := &nvExtractor{ctx: ctx, root: root}
	lower := strings.ToLower(filepath.Base(archive))
	if strings.HasSuffix(lower, ".tar.gz") || strings.HasSuffix(lower, ".tgz") {
		err = ex.tarGz(archive)
	} else {
		err = ex.zip(archive)
	}
	if err != nil {
		return err
	}
	nvEnsurePosixExecutables(dest)
	return nil
}

type nvExtractor struct {
	ctx   context.Context
	root  string // symlink-resolved destination
	files int
	total int64
}

func nvWithin(root, p string) bool {
	rel, err := filepath.Rel(root, p)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)) && !filepath.IsAbs(rel)
}

// target validates an archive member name and returns its path under root.
func (e *nvExtractor) target(name, kind string) (string, error) {
	n := strings.ReplaceAll(name, "\\", "/")
	if n == "" || strings.HasPrefix(n, "/") || (len(n) > 1 && n[1] == ':') {
		return "", fmt.Errorf("%s Slip blocked (absolute): %s", kind, name)
	}
	for _, part := range strings.Split(n, "/") {
		if part == ".." {
			return "", fmt.Errorf("%s Slip blocked: %s", kind, name)
		}
	}
	t := filepath.Join(e.root, filepath.FromSlash(n))
	if !nvWithin(e.root, t) || t == e.root {
		return "", fmt.Errorf("%s Slip blocked: %s", kind, name)
	}
	return t, nil
}

// prepare creates parents and makes sure nothing on the way (or the file
// itself) is a symlink that leads out of root.
func (e *nvExtractor) prepare(t, name, kind string) error {
	parent := filepath.Dir(t)
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	real, err := filepath.EvalSymlinks(parent)
	if err != nil || !nvWithin(e.root, real) {
		return fmt.Errorf("%s Slip blocked: %s", kind, name)
	}
	if st, err := os.Lstat(t); err == nil && st.Mode()&fs.ModeSymlink != 0 {
		if err := os.Remove(t); err != nil {
			return err
		}
	}
	return nil
}

func (e *nvExtractor) writeFile(t string, src io.Reader, mode fs.FileMode, name, kind string, limit int64) error {
	if limit > nvMaxMemberBytes {
		return fmt.Errorf("%s member too large: %s", kind, name)
	}
	e.files++
	if e.files > nvMaxFiles {
		return fmt.Errorf("%s has too many files (>%d)", kind, nvMaxFiles)
	}
	out, err := os.OpenFile(t, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
	if err != nil {
		return err
	}
	buf := make([]byte, 256*1024)
	for {
		if e.ctx.Err() != nil {
			out.Close()
			return errNativeVisionCancelled
		}
		n, rerr := src.Read(buf)
		if n > 0 {
			e.total += int64(n)
			if e.total > nvMaxTotalBytes {
				out.Close()
				return fmt.Errorf("%s too large after %s", kind, name)
			}
			if _, err := out.Write(buf[:n]); err != nil {
				out.Close()
				return err
			}
		}
		if rerr == io.EOF {
			break
		}
		if rerr != nil {
			out.Close()
			return rerr
		}
	}
	if err := out.Close(); err != nil {
		return err
	}
	if mode&0o111 != 0 && runtime.GOOS != "windows" {
		_ = os.Chmod(t, 0o755)
	}
	return nil
}

// linkTarget checks a symlink member: relative, and resolving inside root.
func (e *nvExtractor) linkTarget(member, link, kind string) error {
	l := strings.TrimSpace(link)
	if l == "" {
		return fmt.Errorf("%s empty link blocked: %s", kind, member)
	}
	if strings.HasPrefix(l, "/") || strings.HasPrefix(l, "\\") || (len(l) >= 2 && l[1] == ':') {
		return fmt.Errorf("%s absolute link blocked: %s -> %s", kind, member, link)
	}
	t, err := e.target(member, kind)
	if err != nil {
		return err
	}
	if !nvWithin(e.root, filepath.Join(filepath.Dir(t), filepath.FromSlash(strings.ReplaceAll(l, "\\", "/")))) {
		return fmt.Errorf("%s link escape blocked: %s -> %s", kind, member, link)
	}
	return nil
}

func (e *nvExtractor) makeSymlink(name, link, kind string) error {
	if err := e.linkTarget(name, link, kind); err != nil {
		return err
	}
	t, _ := e.target(name, kind)
	if err := e.prepare(t, name, kind); err != nil {
		return err
	}
	_ = os.Remove(t)
	if err := os.Symlink(link, t); err != nil {
		return err
	}
	// Chains of in-archive links can still lead out: verify the real landing.
	if real, err := filepath.EvalSymlinks(t); err == nil && !nvWithin(e.root, real) {
		_ = os.Remove(t)
		return fmt.Errorf("%s link escape blocked: %s -> %s", kind, name, link)
	}
	return nil
}

func (e *nvExtractor) zip(path string) error {
	zr, err := zip.OpenReader(path)
	if err != nil {
		return err
	}
	defer zr.Close()
	for _, f := range zr.File {
		if e.ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		mode := f.Mode()
		if mode&fs.ModeSymlink != 0 {
			rc, err := f.Open()
			if err != nil {
				return err
			}
			b, err := io.ReadAll(io.LimitReader(rc, 4096))
			rc.Close()
			if err != nil {
				return err
			}
			if err := e.makeSymlink(f.Name, string(b), "Zip"); err != nil {
				return err
			}
			continue
		}
		if f.Name == "" || strings.HasSuffix(f.Name, "/") || mode.IsDir() {
			// Directory entries are not trusted to create anything, but a bad
			// name is still refused.
			if f.Name != "" {
				if _, err := e.target(strings.TrimRight(f.Name, "/"), "Zip"); err != nil {
					return err
				}
			}
			continue
		}
		t, err := e.target(f.Name, "Zip")
		if err != nil {
			return err
		}
		if err := e.prepare(t, f.Name, "Zip"); err != nil {
			return err
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		err = e.writeFile(t, rc, mode, f.Name, "Zip", int64(f.UncompressedSize64))
		rc.Close()
		if err != nil {
			return err
		}
	}
	return nil
}

func (e *nvExtractor) tarGz(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	gz, err := gzip.NewReader(f)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	var links []*tar.Header
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if e.ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		switch h.Typeflag {
		case tar.TypeSymlink, tar.TypeLink:
			// Validate now, create after the files so hard-link targets exist.
			if h.Typeflag == tar.TypeSymlink {
				err = e.linkTarget(h.Name, h.Linkname, "Tar")
			} else {
				_, err = e.target(h.Name, "Tar")
				if err == nil {
					_, err = e.target(h.Linkname, "Tar")
				}
			}
			if err != nil {
				return err
			}
			links = append(links, h)
		case tar.TypeReg:
			t, err := e.target(h.Name, "Tar")
			if err != nil {
				return err
			}
			if err := e.prepare(t, h.Name, "Tar"); err != nil {
				return err
			}
			if err := e.writeFile(t, tr, fs.FileMode(h.Mode), h.Name, "Tar", h.Size); err != nil {
				return err
			}
		}
	}
	for _, h := range links {
		if e.ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		if h.Typeflag == tar.TypeSymlink {
			if err := e.makeSymlink(h.Name, h.Linkname, "Tar"); err != nil {
				return err
			}
			continue
		}
		t, _ := e.target(h.Name, "Tar")
		src, _ := e.target(h.Linkname, "Tar") // hard links name an archive-root path
		if err := e.prepare(t, h.Name, "Tar"); err != nil {
			return err
		}
		if st, err := os.Lstat(src); err != nil || !st.Mode().IsRegular() {
			return fmt.Errorf("Tar hardlink target missing: %s -> %s", h.Name, h.Linkname)
		}
		_ = os.Remove(t)
		if err := os.Link(src, t); err != nil {
			return err
		}
	}
	return nil
}

// nvEnsurePosixExecutables: llama-server and .so files must be executable on
// Linux (official tarballs).
func nvEnsurePosixExecutables(root string) {
	if runtime.GOOS == "windows" {
		return
	}
	names := map[string]bool{"llama-server": true, "llama-cli": true, "llama-quantize": true, "llama-bench": true}
	_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() || d.Type()&fs.ModeSymlink != 0 {
			return nil
		}
		n := d.Name()
		if names[n] || strings.HasSuffix(n, ".so") || strings.Contains(n, ".so.") {
			if st, err := os.Stat(p); err == nil {
				_ = os.Chmod(p, st.Mode()|0o755)
			}
		}
		return nil
	})
}
