#!/usr/bin/env python3
"""
Resolve an exact Terraform CLI version from a stack's `required_version` constraint,
skipping releases newer than COOLDOWN_DAYS.

Input (environment variables):
  STACK_DIR      directory to scan for `*.tf` (default `.`)
  COOLDOWN_DAYS  minimum release age in days (default 7)

Output (two values):
  terraform-version  the resolved exact version, e.g. `1.15.8`
  constraint         the `required_version` that was found, e.g. `>= 1.10.0`

Both are written to the file named by GITHUB_OUTPUT, or to stdout when that is unset.
Progress goes to stdout either way.

Example
-------
Given `stacks/dev/dns/__gp_versions.tf` containing:

    terraform {
      required_version = ">= 1.10.0"
    }

then on 2026-08-21, with 1.15.9 released a day earlier and 1.15.8 released 43 days
earlier:

    $ STACK_DIR=stacks/dev/dns COOLDOWN_DAYS=7 python3 resolve_terraform_version.py
    Found required_version '>= 1.10.0' in __gp_versions.tf
    Newest version satisfying the constraint is 1.15.9 (from the recent releases)
    Skipping 1.15.9, released 2026-08-19 (1 days ago), inside cooldown
    Selected 1.15.8, released 2026-07-08 (43 days ago)
    terraform-version=1.15.8
    constraint=>= 1.10.0
"""

import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

# Newest releases with their dates inline. One request answers everything: a constraint
# that none of these satisfy can only match old versions, where the cooldown is moot.
RECENT_URL = "https://api.releases.hashicorp.com/v1/releases/terraform?limit={limit}"
RECENT_WINDOW = 20

HTTP_TIMEOUT_SECONDS = 15

# Longest operators first: `>` would otherwise shadow `>=`.
OPERATORS = ("~>", "!=", ">=", "<=", "=", ">", "<")

VERSION_RE = re.compile(
    r"""
    ^v?
    (?P<segments>[0-9]+(?:\.[0-9]+)*)
    (?:-(?P<pre>[0-9A-Za-z\-.]+))?
    (?:\+(?P<meta>[0-9A-Za-z\-.]+))?
    $
    """,
    re.VERBOSE,
)

# The lookbehind rejects attributes such as `min_required_version`. HCL identifiers allow dashes.
REQUIRED_VERSION_RE = re.compile(r"""(?<![\w.-])required_version\s*=\s*"([^"]*)\"""")


class ResolveError(Exception):
    """Raised when no exact version could be determined. Callers fail open."""


def log(message: str) -> None:
    print(message)


def warn(title: str, message: str) -> None:
    print(f"::warning title={title}::{message}")


class Version:
    """A version ordered by hashicorp/go-version rules.

    `written` is how many segments the string actually spelled out, which `~>` depends on:
    `~> 1.10` and `~> 1.10.0` pad to the same segments but mean different things.
    """

    __slots__ = ("raw", "segments", "written", "pre", "meta")

    def __init__(self, raw: str, segments: tuple[int, ...], written: int, pre: str, meta: str):
        self.raw = raw
        self.segments = segments
        self.written = written
        self.pre = pre
        self.meta = meta

    @classmethod
    def parse(cls, text: str) -> "Version":
        match = VERSION_RE.match(text.strip())
        if not match:
            raise ValueError(f"Malformed version: {text!r}")

        parts = [int(p) for p in match.group("segments").split(".")]
        written = len(parts)
        # go-version pads to three segments so that 1.2 and 1.2.0 compare equal.
        segments = tuple(parts + [0] * (3 - written)) if written < 3 else tuple(parts)

        return cls(text.strip(), segments, written, match.group("pre") or "", match.group("meta") or "")

    def compare(self, other: "Version") -> int:
        longest = max(len(self.segments), len(other.segments))
        for i in range(longest):
            # A shorter version only stays equal while the other's extra segments are zero.
            if i >= len(self.segments):
                return 0 if all(s == 0 for s in other.segments[i:]) else -1
            if i >= len(other.segments):
                return 0 if all(s == 0 for s in self.segments[i:]) else 1
            if self.segments[i] != other.segments[i]:
                return 1 if self.segments[i] > other.segments[i] else -1

        if self.pre == other.pre:
            return 0
        # A release outranks any prerelease of the same segments.
        if not self.pre:
            return 1
        if not other.pre:
            return -1
        return _compare_prereleases(self.pre, other.pre)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and self.compare(other) == 0

    def __lt__(self, other: "Version") -> bool:
        return self.compare(other) < 0

    def __hash__(self) -> int:
        return hash((self.segments, self.pre))

    def __repr__(self) -> str:
        return f"Version({self.raw!r})"

    def __str__(self) -> str:
        return self.raw

    def padded(self) -> str:
        """Spell out all three segments, so `1.10` becomes `1.10.0`.

        Terraform reads a two-segment version the same way, because go-version pads it
        before comparing. npm semver does not, and setup-terraform runs on npm semver,
        where a bare `1.10` is the range `1.10.x`. Writing the padded form keeps both
        readings identical.
        """
        text = ".".join(str(segment) for segment in self.segments)
        return f"{text}-{self.pre}" if self.pre else text


def _compare_prerelease_part(left: str, right: str) -> int:
    """Compare one dot-separated prerelease identifier, per go-version's comparePart."""
    if left == right:
        return 0

    left_int = int(left) if left.isdigit() else None
    right_int = int(right) if right.isdigit() else None

    # An absent identifier loses to a numeric one but beats an alphanumeric one.
    if left == "":
        return -1 if right_int is not None else 1
    if right == "":
        return 1 if left_int is not None else -1

    if left_int is not None and right_int is None:
        return -1
    if left_int is None and right_int is not None:
        return 1
    if left_int is None and right_int is None:
        return -1 if left < right else 1

    assert left_int is not None and right_int is not None
    if left_int == right_int:
        return 0
    return -1 if left_int < right_int else 1


def _compare_prereleases(left: str, right: str) -> int:
    if left == right:
        return 0

    left_parts = left.split(".")
    right_parts = right.split(".")

    for i in range(max(len(left_parts), len(right_parts))):
        result = _compare_prerelease_part(
            left_parts[i] if i < len(left_parts) else "",
            right_parts[i] if i < len(right_parts) else "",
        )
        if result != 0:
            return result
    return 0


class Term(NamedTuple):
    operator: str
    version: Version


class Release(NamedTuple):
    version: Version
    released: datetime


def parse_constraint(constraint: str) -> list[Term]:
    """Parse a comma-separated Terraform version constraint into ANDed terms."""
    terms = []
    for part in constraint.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Empty term in constraint: {constraint!r}")

        operator = next((op for op in OPERATORS if part.startswith(op)), "")
        version = Version.parse(part[len(operator) :])
        terms.append(Term(operator, version))

    if not terms:
        raise ValueError(f"Empty constraint: {constraint!r}")
    return terms


def _satisfies_pessimistic(version: Version, constraint: Version) -> bool:
    """Terraform's `~>`: pin every segment before the last one written, then allow growth.

    So `~> 1.10` fixes the major and allows any minor from 10 up (1.10 .. 1.99), while
    `~> 1.10.5` fixes major and minor and allows any patch from 5 up. This is where npm
    semver disagrees: it reads the two-segment form as patch-only.
    """
    if version.compare(constraint) != 0 and (version.pre or constraint.pre):
        return False
    if version.compare(constraint) < 0:
        return False
    if len(constraint.segments) > len(version.segments):
        return False

    for i in range(constraint.written - 1):
        if version.segments[i] != constraint.segments[i]:
            return False

    return constraint.segments[constraint.written - 1] <= version.segments[constraint.written - 1]


def _satisfies_term(version: Version, term: Term) -> bool:
    operator, constraint = term
    result = version.compare(constraint)

    if operator in ("", "="):
        return result == 0
    if operator == "!=":
        return result != 0
    if operator == ">":
        return result > 0
    if operator == ">=":
        return result >= 0
    if operator == "<":
        return result < 0
    if operator == "<=":
        return result <= 0
    if operator == "~>":
        return _satisfies_pessimistic(version, constraint)
    raise ValueError(f"Unknown operator: {operator!r}")


def satisfies(version: Version, terms: Iterable[Term]) -> bool:
    return all(_satisfies_term(version, term) for term in terms)


def strip_comments(text: str) -> str:
    """Blank out HCL comments so a commented-out `required_version` is not picked up.

    Quoted strings are tracked so that a `#` inside a value survives.
    """
    out = []
    i = 0
    length = len(text)
    in_string = False

    while i < length:
        char = text[i]

        if in_string:
            if char == "\\" and i + 1 < length:
                out.append(text[i : i + 2])
                i += 2
                continue
            if char == '"':
                in_string = False
            out.append(char)
            i += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue

        if char == "#" or text.startswith("//", i):
            end = text.find("\n", i)
            if end == -1:
                break
            i = end
            continue

        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end == -1:
                break
            # Keep the newlines so line-oriented content around the comment stays intact.
            out.append("\n" * text.count("\n", i, end))
            i = end + 2
            continue

        out.append(char)
        i += 1

    return "".join(out)


def is_override_file(name: str) -> bool:
    """Terraform treats `override.tf` and `*_override.tf` as override files."""
    return name == "override.tf" or name.endswith("_override.tf")


def read_constraints(path: Path) -> list[str]:
    return REQUIRED_VERSION_RE.findall(strip_comments(path.read_text()))


def find_constraint(stack_dir: Path) -> tuple[str, list[str]]:
    """Return the effective `required_version` constraint and the files it came from.

    Terraform *replaces* rather than merges here: if an override file sets
    `required_version`, the base files' constraints are discarded entirely. The last
    override file in lexicographic order wins, since Terraform applies them in that order.
    Base declarations, in the absence of an override, are ANDed together.

    Returns an empty constraint when nothing declares one; the caller decides what to
    do with that.
    """
    tf_files = sorted(stack_dir.glob("*.tf"), key=lambda p: p.name)

    for path in reversed([p for p in tf_files if is_override_file(p.name)]):
        if found := read_constraints(path):
            return ", ".join(found), [path.name]

    sources = []
    constraints = []
    for path in [p for p in tf_files if not is_override_file(p.name)]:
        if found := read_constraints(path):
            constraints.extend(found)
            sources.append(path.name)

    return ", ".join(constraints), sources


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.load(response)


def parse_release_timestamp(text: str, version_info=sys.version_info) -> datetime:
    """Read a release timestamp from the HashiCorp API, e.g. `2026-08-27T10:31:01.572Z`.

    The action runs on the runner's system python3, which is 3.10 on ubuntu-22.04.
    """
    # `datetime.fromisoformat` reads a trailing `Z` only from Python 3.11 on.
    if version_info < (3, 11) and text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    return datetime.fromisoformat(text)


def fetch_recent_releases() -> list["Release"]:
    """The newest releases with their dates, in one request."""
    releases = []
    for entry in fetch_json(RECENT_URL.format(limit=RECENT_WINDOW)):
        try:
            version = Version.parse(entry["version"])
        except (ValueError, KeyError):
            continue
        releases.append(Release(version, parse_release_timestamp(entry["timestamp_created"])))
    return releases


def _matching(versions: Iterable[Version], terms: list[Term]) -> list[Version]:
    """Those of `versions` satisfying every term, newest first.

    Prereleases are excluded unless the constraint itself names one, which mirrors how
    a caller pinning `1.16.0-beta1` clearly wants it.
    """
    include_prerelease = any(term.version.pre for term in terms)
    matches = [
        v for v in versions if satisfies(v, terms) and (include_prerelease or not v.pre)
    ]
    return sorted(matches, reverse=True)


def exact_pin(terms: list[Term]) -> Version | None:
    """The single version a constraint names outright, if it names one.

    `= 1.12.2` and `1.12.2` leave nothing to resolve, so there is no reason to ask
    HashiCorp anything — and no reason to apply a cooldown either, since a human wrote
    that version into a reviewed PR.
    """
    if len(terms) == 1 and terms[0].operator in ("", "="):
        return terms[0].version
    return None


def resolve_version(
    constraint: str,
    cooldown_days: int,
    now: datetime | None = None,
    get_recent: Callable[[], list["Release"]] = fetch_recent_releases,
) -> str:
    """Newest version satisfying `constraint` that is at least `cooldown_days` old.

    Costs one request, or none when the constraint is an exact pin. Only the recent
    window is consulted: every version outside it is older than every version inside it,
    so if the window holds any match at all, its newest match is the newest match
    overall. A constraint the window cannot satisfy at all, such as `< 1.12.0`, can only
    match old versions — the cooldown has nothing to protect against there, so it raises
    and the caller fails open to the raw constraint.

    When nothing is old enough, warns and falls back to the oldest match in the window.
    The window reaches back a few months, so a longer cooldown lands on that oldest match
    rather than blocking the deployment.
    """
    now = now or datetime.now(timezone.utc)
    terms = parse_constraint(constraint)

    if pinned := exact_pin(terms):
        exact = pinned.padded()
        log(f"Constraint names {exact} outright, so no lookup or cooldown is needed")
        return exact

    dates = {r.version: r.released for r in get_recent()}
    window = _matching(dates, terms)
    if not window:
        raise ResolveError(
            f"none of the {RECENT_WINDOW} most recent releases satisfies it, so every "
            "match is an old version and the cooldown adds nothing"
        )

    log(f"Newest version satisfying the constraint is {window[0]} (from the recent releases)")

    if cooldown_days <= 0:
        log("Cooldown disabled, using the newest matching version")
        return str(window[0])

    cutoff = now - timedelta(days=cooldown_days)
    for version in window:
        released = dates[version]
        age_days = (now - released).days
        if released <= cutoff:
            log(f"Selected {version}, released {released:%Y-%m-%d} ({age_days} days ago)")
            return str(version)
        log(f"Skipping {version}, released {released:%Y-%m-%d} ({age_days} days ago), inside cooldown")

    warn(
        "Terraform version cooldown",
        f"No version satisfying {constraint!r} in the {RECENT_WINDOW} most recent releases "
        f"is older than {cooldown_days} days. Using the oldest match ({window[-1]}) instead.",
    )
    return str(window[-1])


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a") as f:
        f.write(f"{name}={value}\n")


def main() -> None:
    stack_dir = Path(os.environ.get("STACK_DIR") or ".")

    if not stack_dir.is_dir():
        # Without this check a mistyped stack-dir looks like a stack with no
        # required_version, and the warning below would blame the wrong cause.
        warn(
            "Terraform version",
            f"stack-dir {str(stack_dir)!r} is not a directory, so no required_version can be "
            "read, no cooldown can be applied and the newest Terraform release will be "
            "installed. Check the stack-dir input.",
        )
        write_output("terraform-version", "")
        write_output("constraint", "")
        return

    constraint, sources = find_constraint(stack_dir)

    if not constraint:
        # An empty `terraform_version` makes setup-terraform install the newest release.
        # Warn rather than fail; failing would break every stack that has no
        # `required_version`.
        warn(
            "Terraform version",
            f"No required_version found in any .tf file in {stack_dir}, so no cooldown "
            "can be applied and the newest Terraform release will be installed. Declare "
            'one in a terraform block, e.g. required_version = ">= 1.10.0".',
        )
        write_output("terraform-version", "")
        write_output("constraint", "")
        return

    log(f"Found required_version {constraint!r} in {', '.join(sources)}")

    # Parsing COOLDOWN_DAYS inside the try lets a bad value like "7.0" fail open too.
    try:
        cooldown_days = int(os.environ.get("COOLDOWN_DAYS") or "7")
        version = resolve_version(constraint, cooldown_days)
    except (
        ResolveError,
        urllib.error.URLError,
        # A connection dropping mid-response raises IncompleteRead, not an OSError.
        http.client.HTTPException,
        OSError,
        ValueError,
        KeyError,
    ) as error:
        # Fail open: setup-terraform can resolve the raw constraint itself.
        warn(
            "Terraform version resolution",
            f"Could not resolve an exact version for {constraint!r} ({error}). "
            "Passing the constraint through to setup-terraform instead.",
        )
        version = constraint

    write_output("terraform-version", version)
    write_output("constraint", constraint)


if __name__ == "__main__":
    main()
