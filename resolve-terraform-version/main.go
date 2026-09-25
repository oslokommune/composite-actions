// Command resolve-terraform-version prints the newest stable Terraform version
// that satisfies the required_version constraints in a Terraform configuration
// directory.
//
// The version is printed on standard output. Warnings go to standard error,
// and the exit status is non-zero if no release satisfies the constraints.
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

const (
	progName        = "resolve-terraform-version"
	defaultIndexURL = "https://releases.hashicorp.com/terraform/index.json"
)

func main() {
	dir := flag.String("dir", ".", "Terraform configuration directory to read required_version from")
	denylistPath := flag.String("denylist", "", "path to a JSON file listing Terraform releases to skip")
	flag.Parse()

	var denied map[string]string
	if *denylistPath != "" {
		var err error
		denied, err = readDenylist(*denylistPath, os.Stderr)
		if err != nil {
			fmt.Fprintf(os.Stderr, "%s: warning: ignoring deny list: %s\n", progName, err)
		}
	}

	v, err := run(*dir, defaultIndexURL, denied, os.Stderr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %s\n", progName, err)
		os.Exit(1)
	}
	fmt.Println(v)
}

// run resolves the Terraform version, skipping the releases in denied (a map
// from version to the reason it is denied). Warnings and notices are written
// to log.
func run(dir, indexURL string, denied map[string]string, log io.Writer) (*version.Version, error) {
	constraints, err := requiredVersions(dir)
	if err != nil {
		return nil, err
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
		fmt.Fprintf(log, "%s: warning: every release allowed by %q is denied, using %s anyway (%s)\n", progName, constraints.String(), best, denied[best.String()])
		return best, nil
	}
	if !v.Equal(best) {
		fmt.Fprintf(log, "%s: skipping denied release %s (%s), using %s\n", progName, best, denied[best.String()], v)
	}
	return v, nil
}

// requiredVersions collects the required_version constraints from all
// terraform blocks in the .tf files in dir.
func requiredVersions(dir string) (version.Constraints, error) {
	parser := hclparse.NewParser()
	var constraints version.Constraints

	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasSuffix(name, ".tf") {
			continue
		}
		path := filepath.Join(dir, name)

		file, diags := parser.ParseHCLFile(path)
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
	if list.Denied == nil {
		return nil, errors.New(`deny list has no "denied" list`)
	}

	denied := map[string]string{}
	for _, entry := range list.Denied {
		v, err := version.NewVersion(entry.Version)
		if err != nil {
			fmt.Fprintf(log, "%s: warning: ignoring invalid deny list entry %q\n", progName, entry.Version)
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
