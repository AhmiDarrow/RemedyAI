package cli

import (
	"os"
	"testing"
)

// Run with SK_YAML_CASES=<json from a PyYAML generator> to compare the dumper
// against PyYAML on many random documents. Skipped in normal runs; the fixed
// goldens in cli_skills_export_test.go always run.
func TestSkillsYAMLDumpMatchesPyYAMLCases(t *testing.T) {
	path := os.Getenv("SK_YAML_CASES")
	if path == "" {
		t.Skip("SK_YAML_CASES not set")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	all, err := skJSONDecode(raw)
	if err != nil {
		t.Fatal(err)
	}
	bad := 0
	for i, c := range all.([]any) {
		cm := c.(skMap)
		ds, _ := cm.get("data")
		data, err := skJSONDecode([]byte(ds.(string)))
		if err != nil {
			t.Fatal(err)
		}
		want, _ := cm.get("sorted")
		if got := skYAMLDump(data, skDumpOpts{sortKeys: true}); got != want.(string) {
			bad++
			if bad < 6 {
				t.Errorf("case %d sorted:\n--- got\n%q\n--- want\n%q", i, got, want)
			}
		}
		want2, _ := cm.get("unsorted_unicode")
		if got := skYAMLDump(data, skDumpOpts{allowUnicode: true}); got != want2.(string) {
			bad++
			if bad < 6 {
				t.Errorf("case %d unicode:\n--- got\n%q\n--- want\n%q", i, got, want2)
			}
		}
	}
	if bad > 0 {
		t.Errorf("%d mismatches", bad)
	}
}

// SK_YAML_LOADS: [{doc, dump|error}] where dump is
// yaml.dump(yaml.safe_load(doc), sort_keys=False).
func TestSkillsYAMLLoadMatchesPyYAMLCases(t *testing.T) {
	path := os.Getenv("SK_YAML_LOADS")
	if path == "" {
		t.Skip("SK_YAML_LOADS not set")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	all, err := skJSONDecode(raw)
	if err != nil {
		t.Fatal(err)
	}
	for i, c := range all.([]any) {
		cm := c.(skMap)
		doc, _ := cm.get("doc")
		v, lerr := skYAMLLoad(doc.(string))
		if e, ok := cm.get("error"); ok {
			if lerr == nil {
				t.Errorf("case %d: want %v, loaded %#v", i, e, v)
			}
			continue
		}
		if lerr != nil {
			t.Errorf("case %d: %v", i, lerr)
			continue
		}
		want, _ := cm.get("dump")
		if got := skYAMLDump(v, skDumpOpts{}); got != want.(string) {
			t.Errorf("case %d:\n--- got\n%q\n--- want\n%q", i, got, want)
		}
	}
}
