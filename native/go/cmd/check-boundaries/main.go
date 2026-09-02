package main

import (
	"flag"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

var alwaysForbidden = map[string]bool{"os/exec": true, "syscall": true, "unsafe": true, "plugin": true}
var externalOwners = map[string]string{"github.com/Microsoft/go-winio": "ipc", "golang.org/x/sys": "ipc,state", "github.com/santhosh-tekuri/jsonschema/v6": "tools"}

func main() {
	root := flag.String("root", "..", "native directory")
	flag.Parse()
	violations, err := check(filepath.Join(*root, "go"))
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	for _, violation := range violations {
		fmt.Fprintln(os.Stderr, violation)
	}
	if len(violations) > 0 {
		os.Exit(1)
	}
}

func check(goRoot string) ([]string, error) {
	var violations []string
	err := filepath.WalkDir(goRoot, func(path string, entry os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if entry.IsDir() {
			if entry.Name() == ".cache" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return nil
		}
		relative, _ := filepath.Rel(goRoot, path)
		pkg := strings.Split(filepath.ToSlash(relative), "/")[0]
		if pkg == "cmd" {
			return nil
		}
		file, err := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if err != nil {
			return err
		}
		for _, spec := range file.Imports {
			value, err := strconv.Unquote(spec.Path.Value)
			if err != nil {
				return err
			}
			if alwaysForbidden[value] {
				violations = append(violations, fmt.Sprintf("%s imports forbidden %s", filepath.ToSlash(relative), value))
			}
			for dependency, owners := range externalOwners {
				if value == dependency || strings.HasPrefix(value, dependency+"/") {
					if !ownerAllowed(pkg, owners) {
						violations = append(violations, fmt.Sprintf("%s imports %s owned by %s", filepath.ToSlash(relative), value, owners))
					}
				}
			}
			if strings.Contains(strings.ToLower(value), "python") {
				violations = append(violations, fmt.Sprintf("%s directly couples to Python package %s", filepath.ToSlash(relative), value))
			}
		}
		_ = ast.FileExports(file)
		return nil
	})
	return violations, err
}
func ownerAllowed(pkg, owners string) bool {
	for _, owner := range strings.Split(owners, ",") {
		if pkg == owner {
			return true
		}
	}
	return false
}
