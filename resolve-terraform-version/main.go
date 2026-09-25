// Command resolve-terraform-version prints an npm semver range that matches
// the Terraform versions allowed by the required_version constraints in a
// Terraform configuration directory.
//
// hashicorp/setup-terraform picks the newest release that satisfies an npm
// semver range, but npm semver doesn't understand Terraform's "~>" and "!="
// operators. This command translates the constraints into a range with the
// same meaning, so it doesn't need to look up which releases exist.
//
// The range is printed on standard output. Warnings go to standard error,
// and the exit status is non-zero if the constraints can't be translated.
//
// Constraints are parsed with hashicorp/go-version, the same library
// Terraform uses. Each release in an optional deny list file is excluded from
// the range like a "!=" constraint.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/hashicorp/go-version"
	"github.com/hashicorp/hcl/v2"
	"github.com/hashicorp/hcl/v2/hclparse"
	"github.com/zclconf/go-cty/cty"
)

const progName = "resolve-terraform-version"

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

	r, err := run(*dir, denied, os.Stderr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %s\n", progName, err)
		os.Exit(1)
	}
	fmt.Println(r)
}

// run returns the npm semver range for the configuration in dir, excluding
// the releases in denied (a map from version to the reason it is denied).
// Warnings are written to log.
func run(dir string, denied map[string]string, log io.Writer) (string, error) {
	constraints, err := requiredVersions(dir)
	if err != nil {
		return "", err
	}
	r, err := semverRange(constraints, denied)
	if err != nil {
		return "", err
	}
	if r.ignoredDenied != "" {
		fmt.Fprintf(log, "%s: warning: %q pins denied release %s, using it anyway (%s)\n", progName, constraints.String(), r.ignoredDenied, denied[r.ignoredDenied])
	}
	for _, s := range r.skipped {
		fmt.Fprintf(log, "%s: skipping denied release %s (%s)\n", progName, s, denied[s])
	}
	return r.semver, nil
}

type rangeResult struct {
	semver string
	// skipped holds the denied releases the constraints would otherwise
	// allow, oldest first
	skipped []string
	// ignoredDenied is set to the pinned release when the constraints pin a
	// denied release, so it isn't excluded
	ignoredDenied string
}

// requiredVersions collects the required_version constraints from all
// terraform blocks in the .tf files in dir, merged the way Terraform merges
// them: constraints from primary files add up, and each override file that
// sets required_version replaces everything before it.
func requiredVersions(dir string) (version.Constraints, error) {
	parser := hclparse.NewParser()
	var constraints, overrides version.Constraints

	// ReadDir sorts by file name, which is the order Terraform applies
	// override files in.
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	for _, entry := range entries {
		name := entry.Name()
		// Terraform ignores hidden files, such as editor lock files
		if entry.IsDir() || !strings.HasSuffix(name, ".tf") || strings.HasPrefix(name, ".") {
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

		base := strings.TrimSuffix(name, ".tf")
		if base == "override" || strings.HasSuffix(base, "_override") {
			if len(c) > 0 {
				overrides = c
			}
		} else {
			constraints = append(constraints, c...)
		}
	}
	if overrides != nil {
		return overrides, nil
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

// constraintRegexp splits a go-version constraint into its operator and
// version. go-version has already validated it.
var constraintRegexp = regexp.MustCompile(`^\s*(<=|>=|!=|~>|<|>|=)?\s*(.*?)\s*$`)

// segmentsRegexp matches the numeric segments of a version as written.
var segmentsRegexp = regexp.MustCompile(`^v?[0-9]+(\.[0-9]+)*`)

// semverRange translates constraints into an npm semver range that allows the
// same versions, minus the versions in denied. If the constraints pin a denied
// version, it is kept, since excluding it would leave nothing to install.
//
// npm semver has no "!=", so the range is split around each excluded
// version. Excluding 1.15.9 and 1.16.3 from ">= 1.10.0" gives
//
//	>=1.10.0 <1.15.9 || >=1.10.0 >1.15.9 <1.16.3 || >=1.10.0 >1.16.3
func semverRange(constraints version.Constraints, denied map[string]string) (rangeResult, error) {
	var bounds []string
	var allowed version.Constraints
	var excluded []*version.Version
	var pinned *version.Version
	var result rangeResult
	for _, c := range constraints {
		m := constraintRegexp.FindStringSubmatch(c.String())
		op, raw := m[1], m[2]
		v, err := version.NewVersion(raw)
		if err != nil {
			return rangeResult{}, err
		}
		if op == "!=" {
			excluded = append(excluded, v)
			continue
		}
		b, err := comparators(op, v, len(strings.Split(segmentsRegexp.FindString(raw), ".")))
		if err != nil {
			return rangeResult{}, fmt.Errorf("translate %q: %w", c.String(), err)
		}
		bounds = append(bounds, b...)
		allowed = append(allowed, c)
		if op == "" || op == "=" {
			pinned = v
		}
	}

	for d := range denied {
		v := version.Must(version.NewVersion(d))
		if pinned != nil && v.Equal(pinned) && allowed.Check(v) {
			result.ignoredDenied = d
			continue
		}
		excluded = append(excluded, v)
	}
	// Versions outside the bounds are excluded already. Leaving them out keeps
	// the range short.
	excluded = slices.DeleteFunc(excluded, func(v *version.Version) bool { return !allowed.Check(v) })
	slices.SortFunc(excluded, (*version.Version).Compare)
	excluded = slices.CompactFunc(excluded, (*version.Version).Equal)

	sets := make([]string, 0, len(excluded)+1)
	lower := bounds
	for _, v := range excluded {
		s, err := semver(v)
		if err != nil {
			return rangeResult{}, err
		}
		if _, ok := denied[v.String()]; ok {
			result.skipped = append(result.skipped, v.String())
		}
		sets = append(sets, strings.Join(append(slices.Clone(lower), "<"+s), " "))
		lower = append(slices.Clone(bounds), ">"+s)
	}
	last := strings.Join(lower, " ")
	if last == "" {
		last = "*"
	}
	sets = append(sets, last)
	result.semver = strings.Join(sets, " || ")
	return result, nil
}

// comparators translates a constraint other than "!=" into npm semver
// comparators. n is the number of segments the version was written with,
// which decides what "~>" allows.
func comparators(op string, v *version.Version, n int) ([]string, error) {
	s, err := semver(v)
	if err != nil {
		return nil, err
	}
	switch op {
	case "", "=":
		return []string{"=" + s}, nil
	case "~>":
		if v.Prerelease() != "" {
			return nil, errors.New("~> with a pre-release version isn't supported")
		}
		// "~> 1" only sets a lower bound
		if n < 2 {
			return []string{">=" + s}, nil
		}
		// "~> 1.2" allows 1.x from 1.2, and "~> 1.2.3" allows 1.2.x from 1.2.3
		upper := slices.Clone(v.Segments()[:n-1])
		upper[n-2]++
		for len(upper) < 3 {
			upper = append(upper, 0)
		}
		return []string{">=" + s, "<" + joinSegments(upper)}, nil
	default:
		return []string{op + s}, nil
	}
}

// semver formats v as an npm semver version, without build metadata.
func semver(v *version.Version) (string, error) {
	if len(v.Segments()) > 3 {
		return "", fmt.Errorf("version %s has more than three segments", v)
	}
	s := joinSegments(v.Segments())
	if p := v.Prerelease(); p != "" {
		s += "-" + p
	}
	return s, nil
}

func joinSegments(segments []int) string {
	parts := make([]string, len(segments))
	for i, s := range segments {
		parts[i] = strconv.Itoa(s)
	}
	return strings.Join(parts, ".")
}
