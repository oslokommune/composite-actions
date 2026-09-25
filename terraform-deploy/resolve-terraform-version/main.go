// Command resolve-terraform-version prints the newest stable Terraform version
// that satisfies the required_version constraints in a Terraform configuration
// directory, plus any extra constraints given on the command line.
//
// Constraints are evaluated with hashicorp/go-version, the same library
// Terraform uses, so operators like "~>" and "!=" behave exactly as they do in
// Terraform itself.
//
// Each release in an optional deny list file is added as a "!=" constraint.
// The deny list is best-effort: if the file can't be read, or it would rule
// out every release the constraints allow, a warning is printed and the deny
// list is ignored.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"maps"
	"net/http"
	"os"
	"path/filepath"
	"slices"
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
	denylistPath := flag.String("denylist", "", "path to a JSON file listing Terraform releases to skip")
	flag.Parse()

	var denied map[string]string
	if *denylistPath != "" {
		var err error
		denied, err = readDenylist(*denylistPath, os.Stderr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "::warning::Could not read the Terraform version deny list, resolving without it: %s\n", err)
		}
	}

	v, err := run(*dir, *extra, *indexURL, denied, os.Stderr)
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
	fmt.Println(v)
}

// run resolves the Terraform version, skipping the releases in denied (a map
// from version to the reason it is denied). Warnings and notices are written
// to log as GitHub Actions workflow commands.
func run(dir, extra, indexURL string, denied map[string]string, log io.Writer) (*version.Version, error) {
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
	best, err := newestMatching(available, constraints)
	if err != nil {
		return nil, err
	}
	if len(denied) == 0 {
		return best, nil
	}

	v, err := newestMatching(available, append(slices.Clone(constraints), denyConstraints(denied)...))
	if err != nil {
		fmt.Fprintf(log, "::warning::Every Terraform release allowed by %q is on the deny list, using %s anyway (%s)\n", constraints.String(), best, denied[best.String()])
		return best, nil
	}
	if !v.Equal(best) {
		fmt.Fprintf(log, "::notice::Skipping Terraform %s because it is on the deny list (%s), using %s\n", best, denied[best.String()], v)
	}
	return v, nil
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

type denylist struct {
	Denied []denylistEntry `json:"denied"`
}

type denylistEntry struct {
	Version string `json:"version"`
	Reason  string `json:"reason"`
}

func readDenylist(path string, log io.Writer) (map[string]string, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return parseDenylist(f, log)
}

// parseDenylist returns the denied versions mapped to the reason they are
// denied. The input is JSON of the form
//
//	{"denied": [{"version": "1.9.3", "reason": "breaks the S3 backend"}]}
//
// Entries with an invalid version are skipped with a warning.
func parseDenylist(r io.Reader, log io.Writer) (map[string]string, error) {
	var list denylist
	if err := json.NewDecoder(r).Decode(&list); err != nil {
		return nil, fmt.Errorf("decode deny list: %w", err)
	}

	denied := map[string]string{}
	for _, entry := range list.Denied {
		v, err := version.NewVersion(entry.Version)
		if err != nil {
			fmt.Fprintf(log, "::warning::Skipping invalid entry %q in the Terraform version deny list\n", entry.Version)
			continue
		}
		reason := strings.TrimSpace(entry.Reason)
		if reason == "" {
			reason = "no reason given"
		}
		denied[v.String()] = reason
	}
	return denied, nil
}

// denyConstraints turns each denied version into a "!=" constraint, in a
// stable order.
func denyConstraints(denied map[string]string) version.Constraints {
	var constraints version.Constraints
	for _, v := range slices.Sorted(maps.Keys(denied)) {
		c, err := version.NewConstraint("!= " + v)
		if err != nil {
			// Unreachable: the keys are normalized versions from parseDenylist
			panic(err)
		}
		constraints = append(constraints, c...)
	}
	return constraints
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
