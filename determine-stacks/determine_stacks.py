#!/usr/bin/env python3
"""
Determine and classify Terraform stacks based on various filters and heuristics.

To be used in CI/CD pipelines to identify which stacks to operate on.
"""

import json
import os
import re
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


def eprint(*args, **kwargs) -> None:
    """Helper function that logs to stderr to separate diagnostics from main output."""
    print(*args, file=sys.stderr, **kwargs)


def determine_stack_environment(
    stack_path: str, dev_envs={"dev", "qa", "test"}, prod_envs={"prod"}
) -> str | None:
    """Use heuristics to determine what type of environment a stack belongs to."""
    for part in Path(stack_path).parts:
        part_lower = part.lower()
        if part_lower in dev_envs:
            return "dev"
        if part_lower in prod_envs:
            return "prod"
    return None


def expand_braces(pattern: str) -> list[str]:
    """
    Expand bash-style brace patterns into multiple patterns.

    Examples:
        "stacks/{a,b}" -> ["stacks/a", "stacks/b"]
        "stacks/{dev,prod}/app" -> ["stacks/dev/app", "stacks/prod/app"]
        "{a,b}/{c,d}" -> ["a/c", "a/d", "b/c", "b/d"]
        "no-braces" -> ["no-braces"]

    Raises ValueError on invalid syntax (unmatched/nested/empty braces).
    """
    # Check for unmatched braces
    if pattern.count("{") != pattern.count("}"):
        raise ValueError(f"Unmatched braces in pattern: {pattern}")

    # Check for nested braces
    if re.search(r"\{[^{}]*\{", pattern):
        raise ValueError(f"Nested braces not supported: {pattern}")

    # Check for empty braces
    if "{}" in pattern:
        raise ValueError(f"Empty braces in pattern: {pattern}")

    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]

    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    alternatives = match.group(1).split(",")

    # Check for empty alternatives
    if any(alt.strip() == "" for alt in alternatives):
        raise ValueError(f"Empty alternative in braces: {pattern}")

    result = []
    for alt in alternatives:
        result.extend(expand_braces(prefix + alt + suffix))
    return result


def is_terraform_stack(path: Path) -> bool:
    """Use heuristics to determine if a directory is a Terraform stack."""
    # Check for at least one .tf file that mentions an S3 backend
    for tf_file in path.glob("*.tf"):
        try:
            if 'backend "s3"' in tf_file.read_text():
                return True
        except Exception:
            pass
    return False


def get_dirs_from_glob(root: Path, globs: list[str]) -> list[str]:
    """
    Expand glob patterns to matching stack directories.

    - If a pattern matches a directory, consider that directory.
    - If a pattern matches a file, consider its parent directory.
    - De-duplicate
    """
    dirs: set[Path] = set()

    for pattern in globs:
        for path in root.glob(pattern):
            candidate = path if path.is_dir() else path.parent
            # Only keep dirs under root
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_dir:
                dirs.add(candidate)

    return sorted(str(d.relative_to(root)) for d in dirs)


def get_core_stacks(
    patterns=[],
    override_default_patterns=False,
    default_patterns=[
        "**/remote-state",
        "**/networking-data",
        "**/networking",
        "**/dns",
        "**/certificates",
        "**/load-balancing-*-data",
        "**/load-balancing-*",
        "**/iam",
        "**/app-common",
        "**/datadog-common",
        "**/databases",
        "**/rds-bastion",
        "**/*-data",
    ],
) -> list[str]:
    """Get core stack patterns from PATTERNS env var or use defaults."""
    return patterns if override_default_patterns else default_patterns + patterns


def files_to_dirs(files: list[str]) -> list[str]:
    """Convert file paths to unique parent directories."""
    seen = set()
    directories = []
    for path in files:
        path_obj = PurePosixPath(path).parent
        # If a file inside .boilerplate dir has been changed, we consider the parent dir instead
        # as that's the actual stack dir
        if path_obj.name == ".boilerplate":
            path_obj = path_obj.parent

        if (d := str(path_obj)) not in seen:
            seen.add(d)
            directories.append(d)

    return sorted(directories)


def separate_by_environment(dirs: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Separate directories into (dev, prod, skipped) based on path components."""
    result: dict[str | None, list[str]] = {"dev": [], "prod": [], None: []}
    for d in dirs:
        result[determine_stack_environment(d)].append(d)
    return result["dev"], result["prod"], result[None]


def classify_stacks(
    paths: list[str], patterns: list[str]
) -> tuple[list[str], list[str]]:
    """Classify stacks into a group of paths that match patterns and those that don't."""
    hit = []
    miss = []

    for p in paths:
        pp = PurePosixPath(p)
        matched = False
        for i, pat in enumerate(patterns):
            if pp.match(pat):
                hit.append(p)
                matched = True
                break
        if not matched:
            miss.append(p)

    return hit, miss


def parse_string_list(s: str | None) -> list[str]:
    """Parse a comma-separated or newline-delimited string into a list."""
    if not s or not s.strip():
        return []
    lines = [item.strip() for item in s.splitlines() if item.strip()]
    # Parse as newline-delimited list
    if len(lines) > 1:
        return lines
    line = lines[0]
    # Split on commas not inside braces (comma not followed by } without { between)
    parts = re.split(r",(?![^{]*})", line)
    return [p.strip() for p in parts if p.strip()]


def expand_patterns(patterns: list[str]) -> list[str]:
    """Expand braces in a list of patterns."""
    return [expanded for p in patterns for expanded in expand_braces(p)]


def main(writer: TextIO = sys.stdout, root: Path = Path()) -> dict:
    selected_stacks = expand_patterns(parse_string_list(os.environ.get("SELECTED_STACKS", "")))
    ignored_stacks = expand_patterns(parse_string_list(os.environ.get("IGNORED_STACKS", "")))
    user_supplied_core_stacks = expand_patterns(parse_string_list(os.environ.get("CORE_STACKS", "")))
    override_core_stacks = os.environ.get("OVERRIDE_CORE_STACKS", "false") == "true"
    changed_files = parse_string_list(os.environ.get("CHANGED_FILES", ""))

    if selected_stacks:
        # If stacks are explicitly selected, use those
        dirs = get_dirs_from_glob(root, selected_stacks)
    else:
        # We use changed files if no stacks are explicitly selected
        dirs = files_to_dirs(changed_files)

    # Filter to valid Terraform stacks and exclude any that match ignored patterns
    terraform_dirs = [d for d in dirs if is_terraform_stack(root / d)]

    if non_terraform_dirs := sorted(set(dirs) - set(terraform_dirs)):
        eprint(f"Skipped non-Terraform directories: {non_terraform_dirs}")

    # Filter out valid, but ignored stacks
    included_dirs = [
        d
        for d in terraform_dirs
        if not any(PurePosixPath(d).match(pattern) for pattern in ignored_stacks)
    ]

    if ignored_dirs := sorted(set(terraform_dirs) - set(included_dirs)):
        eprint(f"Skipped ignored directories: {ignored_dirs}")

    # Separate by environment
    dev_dirs, prod_dirs, unknown = separate_by_environment(included_dirs)
    if unknown:
        eprint(f"Skipped stacks with unknown environment: {sorted(unknown)}")

    # Classify into core and apps
    core_stacks = get_core_stacks(user_supplied_core_stacks, override_core_stacks)
    dev_core_stacks, dev_apps_stacks = classify_stacks(dev_dirs, core_stacks)
    prod_core_stacks, prod_apps_stacks = classify_stacks(prod_dirs, core_stacks)

    # Combine results for convenience use
    all_dev_stacks = sorted(dev_core_stacks + dev_apps_stacks)
    all_prod_stacks = sorted(prod_core_stacks + prod_apps_stacks)
    all_stacks = sorted(all_dev_stacks + all_prod_stacks)

    result = {
        "dev-core-stacks": dev_core_stacks,
        "dev-apps-stacks": dev_apps_stacks,
        "prod-core-stacks": prod_core_stacks,
        "prod-apps-stacks": prod_apps_stacks,
        "all-dev-stacks": all_dev_stacks,
        "all-prod-stacks": all_prod_stacks,
        "all-stacks": all_stacks,
    }

    if writer:
        # Write outputs in GitHub Actions format
        for key, value in result.items():
            writer.write(f"{key}={json.dumps(value)}\n")

    return result


if __name__ == "__main__":
    main()
