// Command resolve-terraform-version prints the newest stable Terraform version
// that satisfies the required_version constraints in a Terraform configuration
// directory, plus any extra constraints given on the command line.
//
// Constraints are evaluated with hashicorp/go-version, the same library
// Terraform uses, so operators like "~>" and "!=" behave exactly as they do in
// Terraform itself.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/hashicorp/go-version"
	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclparse"
	"github.com/zclconf/go-cty/cty"
)

const defaultIndexURL = "https://releases.hashicorp.com/terraform/index.json"

func main() {
	dir := flag.String("dir", ".", "Terraform configuration directory to read required_version from")
	extra := flag.String("constraint", "", `extra version constraint to apply, e.g. "!= 1.9.3"`)
	indexURL := flag.String("index-url", defaultIndexURL, "URL of the Terraform release index")
	flag.Parse()

	v, err := run(*dir, *extra, *indexURL)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	fmt.Println(v)
}

func run(dir, extra, indexURL string) (*version.Version, error) {
	constraints, err := requiredVersions(dir)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(extra) != "" {
		c, err := version.NewConstraint(extra)
		if err != nil {
			return nil, fmt.Errorf("parse extra constraint %q: %w", extra, err)
		}
		constraints = append(constraints, c...)
	}

	available, err := fetchVersions(indexURL)
	if err != nil {
		return nil, err
	}
	return newestMatching(available, constraints)
}

// requiredVersions collects the required_version constraints from all
// terraform blocks in the .tf and .tf.json files in dir.
func requiredVersions(dir string) (version.Constraints, error) {
	parser := hclparse.NewParser()
	var constraints version.Constraints

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		path := filepath.Join(dir, entry.Name())

		var file *hcl.File
		var diags hcl.Diagnostics
		switch {
		case strings.HasSuffix(entry.Name(), ".tf"):
			file, diags = parser.ParseHCLFile(path)
		case strings.HasSuffix(entry.Name(), ".tf.json"):
			file, diags = parser.ParseJSONFile(path)
		default:
			continue
		}
		if diags.HasErrors() {
			return nil, diags
		}

		c, err := fileRequiredVersions(file)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		constraints = append(constraints, c...)
	}
	return constraints, nil
}

func fileRequiredVersions(file *hcl.File) (version.Constraints, error) {
	content, _, diags := file.Body.PartialContent(&hcl.BodySchema{
		Blocks: []hcl.BlockHeaderSchema{{Type: "terraform"}},
	})
	if diags.HasErrors() {
		return nil, diags
	}

	var constraints version.Constraints
	for _, block := range content.Blocks {
		attrs, _, diags := block.Body.PartialContent(&hcl.BodySchema{
			Attributes: []hcl.AttributeSchema{{Name: "required_version"}},
		})
		if diags.HasErrors() {
			return nil, diags
		}
		attr, ok := attrs.Attributes["required_version"]
		if !ok {
			continue
		}

		value, diags := attr.Expr.Value(nil)
		if diags.HasErrors() {
			return nil, diags
		}
		if !value.Type().Equals(cty.String) || value.IsNull() {
			return nil, errors.New("required_version must be a string")
		}
		c, err := version.NewConstraint(value.AsString())
		if err != nil {
			return nil, fmt.Errorf("parse required_version: %w", err)
		}
		constraints = append(constraints, c...)
	}
	return constraints, nil
}

// fetchVersions returns every version listed in the Terraform release index.
func fetchVersions(url string) ([]*version.Version, error) {
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return nil, fmt.Errorf("fetch release index: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("fetch release index: %s", resp.Status)
	}

	var index struct {
		Versions map[string]json.RawMessage `json:"versions"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&index); err != nil {
		return nil, fmt.Errorf("decode release index: %w", err)
	}

	versions := make([]*version.Version, 0, len(index.Versions))
	for raw := range index.Versions {
		v, err := version.NewVersion(raw)
		if err != nil {
			continue
		}
		versions = append(versions, v)
	}
	return versions, nil
}

// newestMatching returns the newest stable version that satisfies all
// constraints. With no constraints, it returns the newest stable version.
func newestMatching(available []*version.Version, constraints version.Constraints) (*version.Version, error) {
	var best *version.Version
	for _, v := range available {
		if v.Prerelease() != "" || !constraints.Check(v) {
			continue
		}
		if best == nil || v.GreaterThan(best) {
			best = v
		}
	}
	if best == nil {
		return nil, fmt.Errorf("no Terraform release satisfies %q", constraints.String())
	}
	return best, nil
}
