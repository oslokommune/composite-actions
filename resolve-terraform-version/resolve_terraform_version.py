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
"""
import argparse
import json
import re
import sys
from pathlib import Path

VERSION = r"\d+(?:\.\d+){0,2}"
CONSTRAINT = re.compile(rf"(=|!=|>=|<=|>|<|~>)?\s*v?({VERSION})")
REQUIRED_VERSION = re.compile(r'^\s*required_version\s*=\s*"([^"]*)"', re.MULTILINE)


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


def read_required_versions(directory):
    """Merge required_version the way Terraform does: constraints in primary
    files add up, and each override file (override.tf or *_override.tf) that
    sets required_version replaces everything before it, in file name order."""
    constraints, override = [], None
    for path in sorted(Path(directory).glob("*.tf")):
        # Terraform ignores hidden files, such as editor lock files
        if path.name.startswith("."):
            continue
        found = REQUIRED_VERSION.findall(path.read_text())
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


def to_range(constraints, excluded):
    base = []
    excluded = {normalize(version) for version in excluded}
    for constraint in constraints:
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

    if not base and not excluded:
        return "latest"

    bounds = sorted(excluded, key=version_key)
    lowers = [None] + [f">{version}" for version in bounds]
    uppers = [f"<{version}" for version in bounds] + [None]
    intervals = [" ".join(base + [b for b in (lower, upper) if b]) for lower, upper in zip(lowers, uppers)]
    return " || ".join(intervals)


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

    try:
        print(to_range(read_required_versions(args.dir), denied))
    except ValueError as error:
        sys.exit(f"::error::{error}")


if __name__ == "__main__":
    main()
