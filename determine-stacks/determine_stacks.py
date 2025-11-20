#!/usr/bin/env python3
"""
Classify Terraform stacks into sequential and parallel deployment groups.

Reads from environment variables:
- DEV_FILES: JSON array of changed dev stack files
- PROD_FILES: JSON array of changed prod stack files
- GITHUB_EVENT_NAME: Event type (workflow_dispatch or push)
- GITHUB_OUTPUT: Path to GitHub Actions output file

Writes to GITHUB_OUTPUT:
- dev-sequential: JSON array of dev stacks to deploy sequentially
- dev-parallel: JSON array of dev stacks to deploy in parallel
- prod-sequential: JSON array of prod stacks to deploy sequentially
- prod-parallel: JSON array of prod stacks to deploy in parallel
"""

import os
import json
import sys
from pathlib import Path, PurePosixPath
from itertools import chain

# Default ordered patterns (first match wins)
DEFAULT_PATTERNS = [
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
    "**/*-data"
]

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)

# Example:
#   Input: 'stacks/dev/dns,stacks/**/iam,stacks/dev/app-*'
#   Output: ['stacks/dev/app-hello', 'stacks/dev/dns', 'stacks/prod/iam'] # Order might be different.
#
# Does not support brackets {iam,dns}
def get_dirs_with_glob_pattern(root:Path, glob_pattern_str: str) -> list[str]:
    dirs = set()
    glob_patterns = [p.strip() for p in glob_pattern_str.split(",") if p.strip()]
    for glob_pattern in glob_patterns:
        for path in root.glob(glob_pattern):
            eprint("considering path:", path)
            if path.is_dir():
                dirs.add(str(path.relative_to(root)))
    eprint()
    return sorted(dirs)


# Example:
#   Output: ['**/remote-state', '**/networking-data', '**/networking', ...]
def get_patterns() -> list[str]:
    patterns_env = os.environ.get("PATTERNS", "")
    if patterns_env.strip():
        return [line.strip() for line in patterns_env.strip().split("\n") if line.strip()]
    return DEFAULT_PATTERNS

# Example:
#   Input: 'stacks/dev/app/main.tf', treat_as_dir=False
#   Output: 'stacks/dev/app'
#
# Example:
#   Input: 'stacks/dev/networking', treat_as_dir=True
#   Output: 'stacks/dev/networking'
def extract_directory(path: str, treat_as_dir: bool) -> str:
    normalized = path.rstrip("/")

    if treat_as_dir:
        return normalized

    # Extract parent directory from file path
    if "/" in normalized:
        return normalized.rsplit("/", 1)[0]

    return normalized

# Example:
#   Input: '["stacks/dev/app/.boilerplate/main.tf"]'
#   Output: ['stacks/dev/app']
#
# Example:
#   Input: '["stacks/dev/networking"]', treat_as_dir=True
#   Output: ['stacks/dev/networking']
def parents_to_dirs(files_json: str, treat_as_dir: bool = False) -> list[str]:
    try:
        file_paths = json.loads(files_json or "[]")
    except json.JSONDecodeError:
        file_paths = []

    directories = []
    seen = set()

    for path in file_paths:
        directory = extract_directory(path, treat_as_dir)
        path_obj = PurePosixPath(directory)

        # Special case: .boilerplate files live under stack/.boilerplate,
        # but plan/apply should operate at the stack root
        if path_obj.name == ".boilerplate":
            path_obj = path_obj.parent

        normalized_dir = str(path_obj)
        if normalized_dir not in seen:
            seen.add(normalized_dir)
            directories.append(normalized_dir)

    return sorted(directories)


# Example:
#   Input: 'stacks/dev/networking', ['**/remote-state', '**/networking']
#   Output: 1
#
# Example:
#   Input: 'stacks/dev/app', ['**/networking', '**/iam']
#   Output: None
def first_match(p: str, patterns: list[str]) -> int | None:
    pp = PurePosixPath(p)
    for index, pat in enumerate(patterns):
        if pp.match(pat):
            return index
    return None

# Example:
#   Input: ['stacks/dev/iam', 'stacks/prod/networking']
#   Output: (['stacks/dev/iam'], ['stacks/prod/networking'])
def separate_environment(changed_dirs: list[str], dev_files_path: str, prod_files_path: str) -> tuple[list[str], list[str]]:
    dev_dirs = []
    prod_dirs = []

    for directory in changed_dirs:
        if directory.startswith(dev_files_path):
            dev_dirs.append(directory)
        elif directory.startswith(prod_files_path):
            prod_dirs.append(directory)

    return dev_dirs, prod_dirs

# Example:
#   Input: ['stacks/dev/remote-state', 'stacks/dev/networking', 'stacks/dev/app-hello'], DEFAULT_PATTERNS
#   Output: (['stacks/dev/remote-state', 'stacks/dev/networking'], ['stacks/dev/app-hello'])
def classify(paths: list[str], patterns: list[str]) -> tuple[list[str], list[str]]:
    buckets = [[] for _ in patterns]
    parallel: list[str] = []
    for p in paths:
        idx = first_match(p, patterns)
        (parallel if idx is None else buckets[idx]).append(p)
    sequential = list(chain.from_iterable(buckets))
    return sequential, parallel


def run(writer, root:Path):
    eprint("Running in directory:", root.absolute())

    glob_filter = os.environ.get("GLOB_FILTER", "[]")
    manually_triggered = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    dev_files_path = os.environ.get("DEV_FILES_PATH")
    prod_files_path = os.environ.get("PROD_FILES_PATH")

    eprint("glob_filter:", glob_filter)
    eprint("manually_triggered:", manually_triggered)
    eprint("dev_files_path:", dev_files_path)
    eprint("prod_files_path:", prod_files_path)

    if manually_triggered:
        dirs = get_dirs_with_glob_pattern(root, glob_filter)
    else:
        dirs = parents_to_dirs(os.environ.get("CHANGED_FILES", "[]"), treat_as_dir=manually_triggered)

    dev_dirs, prod_dirs = separate_environment(dirs, dev_files_path, prod_files_path)

    patterns = get_patterns()
    seq_dev, par_dev = classify(dev_dirs, patterns)
    seq_prod, par_prod = classify(prod_dirs, patterns)

    writer.write(f"dev-sequential={json.dumps(seq_dev)}\n")
    writer.write(f"dev-parallel={json.dumps(par_dev)}\n")
    writer.write(f"prod-sequential={json.dumps(seq_prod)}\n")
    writer.write(f"prod-parallel={json.dumps(par_prod)}\n")

def main():
    eprint("Calculating stacks groups...")
    run(sys.stdout, Path())

if __name__ == "__main__":
    main()
