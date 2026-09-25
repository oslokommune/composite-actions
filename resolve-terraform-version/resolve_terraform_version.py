#!/usr/bin/env python3
"""Print an npm semver range for the terraform_version input of
hashicorp/setup-terraform.

The range allows what the required_version constraints in a Terraform
configuration directory allow, minus the releases in an optional deny list.
setup-terraform then installs the newest stable release in the range.

npm semver reads Terraform's syntax almost as Terraform does, with three
differences that this script removes:

- npm semver doesn't know commas. A space means AND, so commas become spaces.
- npm semver reads "~> 1.9" as "< 1.10.0", but Terraform reads it as
  "< 2.0.0". Each "~>" becomes an explicit ">=" and "<" pair.
- npm semver has no "!=" and no parentheses. Each excluded version cuts the
  range into intervals: "<A || >A <B || >B", with the other constraints
  repeated in each interval.

If the deny list leaves no version the configuration allows, the script
ignores the deny list, so that a denied release never stops a pipeline.
"""
import argparse
import json
import re
import sys
from pathlib import Path

VERSION = r"\d+(?:\.\d+){0,2}"
CONSTRAINT = re.compile(rf"(=|!=|>=|<=|>|<|~>)?\s*v?({VERSION})")
# Tokens needed to find required_version in top-level terraform blocks.
# Comments and strings are matched so that their contents are skipped.
HCL_TOKEN = re.compile(
    r"""
      (?P<comment>\#[^\n]*|//[^\n]*|/\*.*?\*/)
    | (?P<required_version>\brequired_version\s*=\s*"(?P<value>[^"]*)")
    | (?P<string>"(?:\\.|[^"\\\n])*")
    | (?P<terraform>\bterraform\s*\{)
    | (?P<open>\{)
    | (?P<close>\})
    """,
    re.VERBOSE | re.DOTALL,
)


def version_key(version):
    return tuple(int(part) for part in version.split("."))


def normalize(version):
    """Pad to three parts. npm semver reads "= 1.9" as any 1.9.x release,
    Terraform reads it as 1.9.0."""
    parts = version.split(".")
    return ".".join(parts + ["0"] * (3 - len(parts)))


def pessimistic(version):
    """Terraform's "~> version" as npm semver constraints:
    ~> 1.9 -> >=1.9.0 <2.0.0, ~> 1.9.3 -> >=1.9.3 <1.10.0.
    Like hashicorp/go-version, "~> 1" has no upper limit."""
    parts = [int(part) for part in version.split(".")]
    if len(parts) == 1:
        return [f">={normalize(version)}"]
    upper = parts[:-2] + [parts[-2] + 1]
    return [f">={normalize(version)}", f"<{normalize('.'.join(str(part) for part in upper))}"]


def find_required_versions(text):
    """Return the required_version values set directly in terraform blocks."""
    found, depth, in_terraform = [], 0, False
    for token in HCL_TOKEN.finditer(text):
        kind = token.lastgroup
        if kind == "required_version" and in_terraform and depth == 1:
            found.append(token.group("value"))
        elif kind == "terraform":
            in_terraform = in_terraform or depth == 0
            depth += 1
        elif kind == "open":
            depth += 1
        elif kind == "close":
            depth = max(depth - 1, 0)
            in_terraform = in_terraform and depth > 0
    return found


def read_required_versions(directory):
    """Return (file name, constraint) pairs, merged the way Terraform does:
    constraints in primary files add up, and each override file (override.tf
    or *_override.tf) that sets required_version replaces everything before
    it, in file name order."""
    constraints, override = [], None
    for path in sorted(Path(directory).glob("*.tf")):
        # Terraform ignores hidden files, such as editor lock files
        if path.name.startswith(".") or not path.is_file():
            continue
        found = [(path.name, value) for value in find_required_versions(path.read_text())]
        if path.stem == "override" or path.stem.endswith("_override"):
            if found:
                override = found
        else:
            constraints += found
    return constraints if override is None else override


def read_denylist(path):
    """Return the denied versions mapped to the reason they are denied."""
    denylist = json.loads(Path(path).read_text())
    if not isinstance(denylist, dict) or not isinstance(denylist.get("denied"), list):
        raise ValueError('deny list has no "denied" list')

    denied = {}
    for entry in denylist["denied"]:
        version = str(entry.get("version", "")) if isinstance(entry, dict) else ""
        if not re.fullmatch(VERSION, version):
            print(f"::warning::Ignoring invalid deny list entry {entry!r}", file=sys.stderr)
            continue
        denied[normalize(version)] = str(entry.get("reason") or "").strip() or "no reason given"
    return denied


def parse(constraint):
    """Return a required_version constraint as npm semver constraints and the
    versions it excludes with "!="."""
    base, excluded = [], set()
    for part in constraint.split(","):
        match = CONSTRAINT.fullmatch(part.strip())
        if not match:
            raise ValueError(f"unsupported required_version constraint {part.strip()!r}")
        operator, version = match.groups()
        if operator == "!=":
            excluded.add(normalize(version))
        elif operator == "~>":
            base += pessimistic(version)
        else:
            base.append(f"{operator or '='}{normalize(version)}")
    return base, excluded


def is_empty(interval):
    """Whether no version satisfies all npm semver constraints in interval."""
    constraints = [CONSTRAINT.fullmatch(constraint).groups() for constraint in interval]
    # Tuples compare so that ">" beats ">=" on the same version, and "<" beats "<="
    lows = [(version_key(version), operator == ">") for operator, version in constraints if operator in ("=", ">=", ">")]
    highs = [(version_key(version), operator != "<") for operator, version in constraints if operator in ("=", "<=", "<")]
    if not lows or not highs:
        return False
    (low, low_exclusive), (high, high_inclusive) = max(lows), min(highs)
    return low > high or (low == high and (low_exclusive or not high_inclusive))


def intervals(base, excluded):
    bounds = sorted(excluded, key=version_key)
    lowers = [None] + [f">{version}" for version in bounds]
    uppers = [f"<{version}" for version in bounds] + [None]
    return [base + [b for b in (lower, upper) if b] for lower, upper in zip(lowers, uppers)]


def to_range(constraints, denied):
    base, excluded = [], set()
    for constraint in constraints:
        constraint_base, constraint_excluded = parse(constraint)
        base += constraint_base
        excluded |= constraint_excluded
    denied = {normalize(version) for version in denied} - excluded

    if not base and not excluded and not denied:
        return "latest"

    result = intervals(base, excluded | denied)
    if denied and all(is_empty(interval) for interval in result):
        print("::warning::Ignoring deny list, because it excludes every release the configuration allows", file=sys.stderr)
        result = intervals(base, excluded)
    return " || ".join(" ".join(interval) for interval in result)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=".", help="Terraform configuration directory")
    parser.add_argument("--denylist", default="", help="JSON file with Terraform releases to exclude")
    args = parser.parse_args()

    denied = {}
    if args.denylist:
        try:
            denied = read_denylist(args.denylist)
        except (OSError, ValueError) as error:
            print(f"::warning::Ignoring deny list: {error}", file=sys.stderr)
    for version, reason in sorted(denied.items(), key=lambda item: version_key(item[0])):
        print(f"Excluding denied release {version} ({reason})", file=sys.stderr)

    required = read_required_versions(args.dir)
    for name, constraint in required:
        try:
            parse(constraint)
        except ValueError as error:
            sys.exit(f"::error::{name}: {error}")
    print(to_range([constraint for _, constraint in required], denied))


if __name__ == "__main__":
    main()
