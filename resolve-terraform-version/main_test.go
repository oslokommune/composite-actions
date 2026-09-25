package main

import (
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"github.com/hashicorp/go-version"
)

func writeFiles(t *testing.T, files map[string]string) string {
	t.Helper()
	dir := t.TempDir()
	for name, content := range files {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return dir
}

func TestRun(t *testing.T) {
	tests := []struct {
		name    string
		files   map[string]string
		want    string
		wantErr bool
	}{
		{
			name:  "greater than or equal",
			files: map[string]string{"main.tf": `terraform { required_version = ">= 1.9.0" }`},
			want:  ">=1.9.0",
		},
		{
			name:  "exact version",
			files: map[string]string{"main.tf": `terraform { required_version = "= 1.9.2" }`},
			want:  "=1.9.2",
		},
		{
			name:  "bare version means exact",
			files: map[string]string{"main.tf": `terraform { required_version = "1.9.0" }`},
			want:  "=1.9.0",
		},
		{
			name:  "pessimistic patch constraint",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`},
			want:  ">=1.9.0 <1.10.0",
		},
		{
			name:  "pessimistic minor constraint",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9" }`},
			want:  ">=1.9.0 <2.0.0",
		},
		{
			name:  "not equals",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0, != 1.9.3" }`},
			want:  ">=1.9.0 <1.10.0 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3",
		},
		{
			name:  "two not equals",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0, != 1.9.3, != 1.9.2" }`},
			want:  ">=1.9.0 <1.10.0 <1.9.2 || >=1.9.0 <1.10.0 >1.9.2 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3",
		},
		{
			name: "constraints across multiple files and blocks",
			files: map[string]string{
				"versions.tf": `terraform { required_version = ">= 1.9.0" }`,
				"main.tf": `
terraform { required_version = "< 1.10.0" }
terraform {
  backend "s3" {}
}
`,
			},
			want: "<1.10.0 >=1.9.0",
		},
		{
			name: "override file replaces required_version",
			files: map[string]string{
				"versions.tf":          `terraform { required_version = "~> 1.9.0" }`,
				"versions_override.tf": `terraform { required_version = "~> 1.10.0" }`,
			},
			want: ">=1.10.0 <1.11.0",
		},
		{
			name: "override.tf replaces required_version",
			files: map[string]string{
				"versions.tf": `terraform { required_version = "~> 1.9.0" }`,
				"override.tf": `terraform { required_version = "= 1.5.7" }`,
			},
			want: "=1.5.7",
		},
		{
			name: "override file without required_version keeps the primary constraints",
			files: map[string]string{
				"versions.tf":         `terraform { required_version = "~> 1.9.0" }`,
				"backend_override.tf": "terraform {\n  backend \"s3\" {}\n}\n",
			},
			want: ">=1.9.0 <1.10.0",
		},
		{
			name: "last override file wins",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = ">= 1.0.0" }`,
				"a_override.tf": `terraform { required_version = "= 1.5.7" }`,
				"b_override.tf": `terraform { required_version = "~> 1.9.0" }`,
			},
			want: ">=1.9.0 <1.10.0",
		},
		{
			name: "file named like an override without the underscore is primary",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = "~> 1.9.0" }`,
				"nooverride.tf": `terraform { required_version = "!= 1.9.3" }`,
			},
			want: ">=1.9.0 <1.10.0 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3",
		},
		{
			name: "reads files starting with underscores",
			files: map[string]string{
				"__gp_versions.tf": `terraform { required_version = "~> 1.9.0" }`,
				"main.tf":          `output "x" { value = 1 }`,
			},
			want: ">=1.9.0 <1.10.0",
		},
		{
			name: "ignores hidden files",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = "~> 1.9.0" }`,
				".#versions.tf": `terraform { required_version = "= 1.5.7" }`,
			},
			want: ">=1.9.0 <1.10.0",
		},
		{
			name:  "ignores JSON configuration",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`, "main.tf.json": `{"terraform": {"required_version": "= 1.5.7"}}`},
			want:  ">=1.9.0 <1.10.0",
		},
		{
			name:  "no constraints allows every release",
			files: map[string]string{"main.tf": `output "x" { value = 1 }`},
			want:  "*",
		},
		{
			name:  "ignores non-Terraform files",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`, "notes.txt": "not hcl {"},
			want:  ">=1.9.0 <1.10.0",
		},
		{
			name:    "pessimistic constraint with a pre-release",
			files:   map[string]string{"main.tf": `terraform { required_version = "~> 1.10.0-beta1" }`},
			wantErr: true,
		},
		{
			name:    "more than three segments",
			files:   map[string]string{"main.tf": `terraform { required_version = ">= 1.9.0.1" }`},
			wantErr: true,
		},
		{
			name:    "invalid required_version",
			files:   map[string]string{"main.tf": `terraform { required_version = "banana" }`},
			wantErr: true,
		},
		{
			name:    "invalid HCL",
			files:   map[string]string{"main.tf": `terraform {`},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := writeFiles(t, tt.files)
			got, err := run(dir, nil, io.Discard)
			if tt.wantErr {
				if err == nil {
					t.Fatalf("expected error, got %s", got)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got != tt.want {
				t.Errorf("got %q, want %q", got, tt.want)
			}
		})
	}
}

const testDenylist = `{
  "denied": [
    {"version": "1.9.3", "reason": "breaks the S3 backend"},
    {"version": "1.10.5"},
    {"version": "banana", "reason": "not a version"},
    {"version": "1.9.2", "reason": "also broken"}
  ]
}`

func TestParseDenylist(t *testing.T) {
	var log strings.Builder
	got, err := parseDenylist(strings.NewReader(testDenylist), &log)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"1.9.3":  "breaks the S3 backend",
		"1.10.5": "no reason given",
		"1.9.2":  "also broken",
	}
	if len(got) != len(want) {
		t.Errorf("got %v, want %v", got, want)
	}
	for v, reason := range want {
		if got[v] != reason {
			t.Errorf("reason for %s: got %q, want %q", v, got[v], reason)
		}
	}
	if !strings.Contains(log.String(), "banana") {
		t.Errorf("log %q does not warn about the invalid entry", log.String())
	}
}

func TestParseDenylistInvalidJSON(t *testing.T) {
	if _, err := parseDenylist(strings.NewReader("1.9.3 # not JSON"), io.Discard); err == nil {
		t.Fatal("expected error")
	}
}

func TestParseDenylistWithoutDeniedList(t *testing.T) {
	// gh api writes the error response to stdout when the download fails
	body := `{"message":"Not Found","status":"404"}`
	if _, err := parseDenylist(strings.NewReader(body), io.Discard); err == nil {
		t.Fatal("expected error")
	}
}

func TestReadDenylistMissingFile(t *testing.T) {
	if _, err := readDenylist(filepath.Join(t.TempDir(), "missing.json"), io.Discard); err == nil {
		t.Fatal("expected error")
	}
}

func TestSemverRange(t *testing.T) {
	denied, err := parseDenylist(strings.NewReader(testDenylist), io.Discard)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name        string
		constraint  string
		denied      map[string]string
		want        string
		wantSkipped []string
		wantIgnored string
	}{
		{
			name:       "no deny list",
			constraint: ">= 1.10.0",
			want:       ">=1.10.0",
		},
		{
			name:        "excludes two releases",
			constraint:  ">= 1.10.0",
			denied:      map[string]string{"1.16.3": "b", "1.15.9": "a"},
			want:        ">=1.10.0 <1.15.9 || >=1.10.0 >1.15.9 <1.16.3 || >=1.10.0 >1.16.3",
			wantSkipped: []string{"1.15.9", "1.16.3"},
		},
		{
			name:        "leaves out denied releases the constraints don't allow",
			constraint:  "~> 1.9.0",
			denied:      denied,
			want:        ">=1.9.0 <1.10.0 <1.9.2 || >=1.9.0 <1.10.0 >1.9.2 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3",
			wantSkipped: []string{"1.9.2", "1.9.3"},
		},
		{
			name:        "combines exclusions in required_version with the deny list",
			constraint:  "~> 1.9.0, != 1.9.3",
			denied:      map[string]string{"1.9.3": "a", "1.9.2": "b"},
			want:        ">=1.9.0 <1.10.0 <1.9.2 || >=1.9.0 <1.10.0 >1.9.2 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3",
			wantSkipped: []string{"1.9.2", "1.9.3"},
		},
		{
			name:       "nothing to exclude",
			constraint: "< 1.9.2",
			denied:     denied,
			want:       "<1.9.2",
		},
		{
			name:        "no constraints",
			denied:      map[string]string{"1.9.3": "a"},
			want:        "<1.9.3 || >1.9.3",
			wantSkipped: []string{"1.9.3"},
		},
		{
			name:        "keeps a pinned denied release",
			constraint:  "= 1.9.3",
			denied:      denied,
			want:        "=1.9.3",
			wantIgnored: "1.9.3",
		},
		{
			name:       "pessimistic major constraint only sets a lower bound",
			constraint: "~> 1",
			want:       ">=1.0.0",
		},
		{
			name:       "pads partial versions",
			constraint: ">= 1.10, < 2",
			want:       ">=1.10.0 <2.0.0",
		},
		{
			name:       "keeps pre-releases",
			constraint: ">= 1.10.0-beta1",
			want:       ">=1.10.0-beta1",
		},
		{
			name:       "drops build metadata",
			constraint: "= 1.10.0+ent",
			want:       "=1.10.0",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var constraints version.Constraints
			if tt.constraint != "" {
				constraints = version.MustConstraints(version.NewConstraint(tt.constraint))
			}
			got, err := semverRange(constraints, tt.denied)
			if err != nil {
				t.Fatal(err)
			}
			if got.semver != tt.want {
				t.Errorf("range: got %q, want %q", got.semver, tt.want)
			}
			if !slices.Equal(got.skipped, tt.wantSkipped) {
				t.Errorf("skipped: got %v, want %v", got.skipped, tt.wantSkipped)
			}
			if got.ignoredDenied != tt.wantIgnored {
				t.Errorf("ignoredDenied: got %q, want %q", got.ignoredDenied, tt.wantIgnored)
			}
		})
	}
}

func TestDenylistFileWithTwoEntries(t *testing.T) {
	dir := writeFiles(t, map[string]string{
		"versions.tf": `terraform { required_version = "~> 1.9.0" }`,
		"denylist.json": `{
  "denied": [
    {"version": "1.9.3", "reason": "first broken release"},
    {"version": "1.9.2", "reason": "second broken release"}
  ]
}`,
	})

	denied, err := readDenylist(filepath.Join(dir, "denylist.json"), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	got, err := run(dir, denied, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if want := ">=1.9.0 <1.10.0 <1.9.2 || >=1.9.0 <1.10.0 >1.9.2 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3"; got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestRunLogsSkippedReleases(t *testing.T) {
	denied, err := parseDenylist(strings.NewReader(testDenylist), io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	dir := writeFiles(t, map[string]string{"main.tf": `terraform { required_version = "< 1.9.3" }`})
	var log strings.Builder
	if _, err := run(dir, denied, &log); err != nil {
		t.Fatal(err)
	}
	// The reason must reach the user; the wording is free to change
	for _, want := range []string{"1.9.2", "also broken"} {
		if !strings.Contains(log.String(), want) {
			t.Errorf("log %q does not mention %q", log.String(), want)
		}
	}
}
