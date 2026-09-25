package main

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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
		extra   string
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
			name:  "extra constraint excludes a version",
			files: map[string]string{"main.tf": `terraform { required_version = "~> 1.9.0" }`},
			extra: "!= 1.9.3",
			want:  "1.9.2",
		},
		{
			name:  "extra constraint without required_version",
			files: map[string]string{"main.tf": `output "x" { value = 1 }`},
			extra: "< 1.10.0",
			want:  "1.9.3",
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
			name:  "json configuration",
			files: map[string]string{"main.tf.json": `{"terraform": {"required_version": "~> 1.9.0"}}`},
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
			name:    "invalid extra constraint",
			files:   map[string]string{"main.tf": `terraform { required_version = ">= 1.9.0" }`},
			extra:   "banana",
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
			got, err := run(dir, tt.extra, server.URL)
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
	if _, err := run(dir, "", server.URL); err == nil {
		t.Fatal("expected error")
	}
}
