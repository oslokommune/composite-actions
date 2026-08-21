#!/usr/bin/env python3
"""
Resolve an exact Terraform CLI version from a stack's `required_version` constraint.

Input is the environment: STACK_DIR (the directory to scan for `*.tf`, default `.`) and
COOLDOWN_DAYS (default 7). Output is written to the file named by GITHUB_OUTPUT, or to
stdout when that is unset. Progress goes to stdout either way.

See README.md for why the constraint is not simply handed to `setup-terraform`.

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

That costs one HTTP request. An exact pin such as `= 1.12.2` costs none, since there is
nothing to choose between and a human already chose it. Only a constraint whose newest
match predates the recent-releases window, such as `< 1.12.0`, falls back to the full
index and per-version date lookups.

`terraform-version` is what gets handed to `hashicorp/setup-terraform`. It is normally an
exact version as above, but resolution fails open: if the release API is unreachable, or
nothing satisfying the constraint is old enough, the output is a `::warning` plus the
newest match or the raw constraint, which `setup-terraform` accepts either way.
"""

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple

# Newest releases with their dates inline. One request answers the common case.
RECENT_URL = "https://api.releases.hashicorp.com/v1/releases/terraform?limit={limit}"
RECENT_WINDOW = 20

# Every version ever published, but with no release dates, so dates then cost one request
# each via RELEASE_URL. Only reached when the recent window cannot answer — a constraint
# whose newest match predates the window, such as `< 1.12.0`.
INDEX_URL = "https://releases.hashicorp.com/terraform/index.json"
RELEASE_URL = "https://api.releases.hashicorp.com/v1/releases/terraform/{version}"

# Caps the per-candidate date lookups on the full-index path, so a pathological constraint
# cannot make hundreds of requests.
MAX_DATE_PROBES = 25

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

REQUIRED_VERSION_RE = re.compile(r"""required_version\s*=\s*"([^"]*)\"""")


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

    Returns an empty constraint when nothing declares one, which the caller passes
    through to `setup-terraform` unchanged. That installs the newest release, which is
    what already happens today. Making it an error instead would be a breaking change for
    any stack without a `required_version`, so it is tracked separately.
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


def fetch_recent_releases() -> list["Release"]:
    """The newest releases, with their dates, in one request.

    This is the cheap path: unlike the full index, this endpoint carries
    `timestamp_created` inline, so one request answers both "which versions exist" and
    "when were they released".
    """
    releases = []
    for entry in fetch_json(RECENT_URL.format(limit=RECENT_WINDOW)):
        try:
            version = Version.parse(entry["version"])
        except (ValueError, KeyError):
            continue
        releases.append(Release(version, datetime.fromisoformat(entry["timestamp_created"])))
    return releases


def fetch_all_versions() -> list[str]:
    """Every published Terraform CLI version, in one request. No dates, hence RELEASE_URL."""
    return list(fetch_json(INDEX_URL)["versions"].keys())


def fetch_release_date(version: str) -> datetime:
    created = fetch_json(RELEASE_URL.format(version=version))["timestamp_created"]
    return datetime.fromisoformat(created)


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


def candidate_versions(constraint: str, available: Iterable[str]) -> list[Version]:
    """Versions satisfying the constraint, newest first, from raw version strings."""
    parsed = []
    for raw in available:
        try:
            parsed.append(Version.parse(raw))
        except ValueError:
            # The index carries a few historical oddities; they can never be a match.
            continue
    return _matching(parsed, parse_constraint(constraint))


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
    get_versions: Callable[[], list[str]] = fetch_all_versions,
    get_release_date: Callable[[str], datetime] = fetch_release_date,
) -> str:
    """Newest version satisfying `constraint` that is at least `cooldown_days` old.

    Costs one request in the common case, none when the constraint is an exact pin, and
    falls back to the full index only when the recent window cannot answer.

    Falls back to the newest match, with a warning, when nothing is old enough — an
    unreleased-yet cooldown should not block a deployment.
    """
    now = now or datetime.now(timezone.utc)
    terms = parse_constraint(constraint)

    if pinned := exact_pin(terms):
        log(f"Constraint names {pinned} outright, so no lookup or cooldown is needed")
        return str(pinned)

    cutoff = now - timedelta(days=cooldown_days) if cooldown_days > 0 else None

    # Every version outside the recent window is older than every version inside it, so
    # if the window holds any match at all, its newest match is the newest match overall.
    recent = {r.version: r.released for r in get_recent()}
    known_dates = {str(v): released for v, released in recent.items()}

    if window := _matching(recent, terms):
        log(f"Newest version satisfying the constraint is {window[0]} (from the recent releases)")
        if selected := _first_outside_cooldown(window, cutoff, now, known_dates.get):
            return selected

    # Either the window held no match, or everything in it was still inside the cooldown.
    candidates = candidate_versions(constraint, get_versions())
    if not candidates:
        raise ResolveError(f"No released Terraform version satisfies {constraint!r}")

    log(f"{len(candidates)} version(s) satisfy the constraint, newest is {candidates[0]}")

    def dated(version: str) -> datetime:
        return known_dates.get(version) or get_release_date(version)

    if selected := _first_outside_cooldown(candidates[:MAX_DATE_PROBES], cutoff, now, dated):
        return selected

    warn(
        "Terraform version cooldown",
        f"No version satisfying {constraint!r} is older than {cooldown_days} days. "
        f"Using the newest match ({candidates[0]}) instead.",
    )
    return str(candidates[0])


def _first_outside_cooldown(
    candidates: list[Version],
    cutoff: datetime | None,
    now: datetime,
    released_at: Callable[[str], datetime | None],
) -> str | None:
    """Walk newest-first and return the first candidate older than `cutoff`.

    `released_at` may return None for a version whose date is not to hand, which skips it
    rather than spending a request; the full-index pass supplies dates for everything.
    """
    if cutoff is None:
        log("Cooldown disabled, using the newest matching version")
        return str(candidates[0])

    for version in candidates:
        released = released_at(str(version))
        if released is None:
            continue
        age_days = (now - released).days
        if released <= cutoff:
            log(f"Selected {version}, released {released:%Y-%m-%d} ({age_days} days ago)")
            return str(version)
        log(f"Skipping {version}, released {released:%Y-%m-%d} ({age_days} days ago), inside cooldown")

    return None


def write_output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a") as f:
        f.write(f"{name}={value}\n")


def main() -> None:
    stack_dir = Path(os.environ.get("STACK_DIR") or ".")
    cooldown_days = int(os.environ.get("COOLDOWN_DAYS") or "7")

    constraint, sources = find_constraint(stack_dir)

    if not constraint:
        # Today's behaviour, kept deliberately: an empty `terraform_version` means npm
        # semver installs the newest release. Warn rather than fail, because failing would
        # break every stack that has no `required_version`.
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

    try:
        version = resolve_version(constraint, cooldown_days)
    except (ResolveError, urllib.error.URLError, OSError, ValueError, KeyError) as error:
        # Fail open: hand the raw constraint to setup-terraform, which is what callers
        # did before this action existed.
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
