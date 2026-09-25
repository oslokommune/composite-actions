package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const testIndex = `{
  "name": "terraform",
  "versions": {
    "1.5.7": {},
    "1.9.0": {},
    "1.9.2": {},
    "1.9.3": {},
    "1.10.0-beta1": {},
    "1.10.0": {},
    "1.10.5": {},
    "1.11.0-rc1": {}
  }
}`

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
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(testIndex))
	}))
	defer server.Close()

	tests := []struct {
		name    string
		files   map[string]string
		want    string
		wantErr bool
	}{
		{
			name:  "greater than or equal",
			files: map[string]string{"main.tf": `terraform { required_version = ">= 1.9.0" }`},
			want:  "1.10.5",
		},
		{
			name:  "exact version",
			files: map[string]string{"main.tf": `terraform { required_version = "= 1.9.2" }`},
			want:  "1.9.2",
		},
		{
			name:  "bare version means exact",
			files: map[string]string{"main.tf": `terraform { required_version = "1.9.0" }`},
			want:  "1.9.0",
		},
		{
			name:  "pessimistic patch constraint",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`},
			want:  "1.9.3",
		},
		{
			name:  "pessimistic minor constraint",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9" }`},
			want:  "1.10.5",
		},
		{
			name:  "not equals",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0, != 1.9.3" }`},
			want:  "1.9.2",
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
			want: "1.9.3",
		},
		{
			name: "override file replaces required_version",
			files: map[string]string{
				"versions.tf":          `terraform { required_version = "~> 1.9.0" }`,
				"versions_override.tf": `terraform { required_version = "~> 1.10.0" }`,
			},
			want: "1.10.5",
		},
		{
			name: "override.tf replaces required_version",
			files: map[string]string{
				"versions.tf": `terraform { required_version = "~> 1.9.0" }`,
				"override.tf": `terraform { required_version = "= 1.5.7" }`,
			},
			want: "1.5.7",
		},
		{
			name: "override file without required_version keeps the primary constraints",
			files: map[string]string{
				"versions.tf":         `terraform { required_version = "~> 1.9.0" }`,
				"backend_override.tf": "terraform {\n  backend \"s3\" {}\n}\n",
			},
			want: "1.9.3",
		},
		{
			name: "last override file wins",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = ">= 1.0.0" }`,
				"a_override.tf": `terraform { required_version = "= 1.5.7" }`,
				"b_override.tf": `terraform { required_version = "~> 1.9.0" }`,
			},
			want: "1.9.3",
		},
		{
			name: "file named like an override without the underscore is primary",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = "~> 1.9.0" }`,
				"nooverride.tf": `terraform { required_version = "!= 1.9.3" }`,
			},
			want: "1.9.2",
		},
		{
			name: "ignores hidden files",
			files: map[string]string{
				"versions.tf":   `terraform { required_version = "~> 1.9.0" }`,
				".#versions.tf": `terraform { required_version = "= 1.5.7" }`,
			},
			want: "1.9.3",
		},
		{
			name:  "ignores JSON configuration",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`, "main.tf.json": `{"terraform": {"required_version": "= 1.5.7"}}`},
			want:  "1.9.3",
		},
		{
			name:  "no constraints picks newest stable",
			files: map[string]string{"main.tf": `output "x" { value = 1 }`},
			want:  "1.10.5",
		},
		{
			name:  "ignores non-Terraform files",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`, "notes.txt": "not hcl {"},
			want:  "1.9.3",
		},
		{
			name:    "no matching release",
			files:   map[string]string{"main.tf": `terraform { required_version = ">= 2.0.0" }`},
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
			got, err := run(dir, server.URL, nil, io.Discard)
			if tt.wantErr {
				if err == nil {
					t.Fatalf("expected error, got %s", got)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if got.String() != tt.want {
				t.Errorf("got %s, want %s", got, tt.want)
			}
		})
	}
}

func TestRunFailsOnIndexError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer server.Close()

	dir := writeFiles(t, map[string]string{"main.tf": `terraform { required_version = ">= 1.9.0" }`})
	if _, err := run(dir, server.URL, nil, io.Discard); err == nil {
		t.Fatal("expected error")
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
	if !strings.Contains(log.String(), `resolve-terraform-version: warning: ignoring invalid deny list entry "banana"`) {
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

func TestDenylist(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(testIndex))
	}))
	defer server.Close()

	denied, err := parseDenylist(strings.NewReader(testDenylist), io.Discard)
	if err != nil {
		t.Fatal(err)
	}

	tests := []struct {
		name       string
		constraint string
		denied     map[string]string
		want       string
		wantLog    string
	}{
		{
			name:       "skips denied releases",
			constraint: "~> 1.9.0",
			denied:     denied,
			want:       "1.9.0",
			wantLog:    "resolve-terraform-version: skipping denied release 1.9.3 (breaks the S3 backend), using 1.9.0",
		},
		{
			name:       "entry without a reason",
			constraint: ">= 1.10.0",
			denied:     denied,
			want:       "1.10.0",
			wantLog:    "resolve-terraform-version: skipping denied release 1.10.5 (no reason given), using 1.10.0",
		},
		{
			name:       "no notice when the newest release is allowed",
			constraint: "< 1.9.2",
			denied:     denied,
			want:       "1.9.0",
		},
		{
			name:       "falls back when every allowed release is denied",
			constraint: "= 1.9.3",
			denied:     denied,
			want:       "1.9.3",
			wantLog:    `resolve-terraform-version: warning: every release allowed by "= 1.9.3" is denied, using 1.9.3 anyway (breaks the S3 backend)`,
		},
		{
			name:       "no deny list",
			constraint: "~> 1.9.0",
			want:       "1.9.3",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := writeFiles(t, map[string]string{"main.tf": `terraform { required_version = "` + tt.constraint + `" }`})
			var log strings.Builder
			got, err := run(dir, server.URL, tt.denied, &log)
			if err != nil {
				t.Fatal(err)
			}
			if got.String() != tt.want {
				t.Errorf("got %s, want %s", got, tt.want)
			}
			if !strings.Contains(log.String(), tt.wantLog) || (tt.wantLog == "" && log.Len() > 0) {
				t.Errorf("log %q, want %q", log.String(), tt.wantLog)
			}
		})
	}
}

func TestDenyConstraints(t *testing.T) {
	got := denyConstraints(map[string]string{"1.9.3": "a", "1.10.5": "b"}).String()
	if want := "!= 1.10.5,!= 1.9.3"; got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
