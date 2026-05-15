"""Resolve stack-config blocks against a stack-dir and emit a sourceable env file.

Iterates the block list, finds the first block whose pattern matches the current
stack-dir, validates the envVars map, and writes shell-quoted KEY=VALUE lines to
a private tempfile. The path is printed to stdout so the caller can capture it.

Usage:
  python3 resolve_stack_config.py --stack-config <json> --stack-dir <path>
                                  [--env-file-dir <path>]

Output: prints the absolute path of the env-file to stdout.
"""

import argparse
import json
import os
import re
import shlex
import stat
import sys
import tempfile
from pathlib import PurePosixPath
from typing import NotRequired, TypedDict


class Block(TypedDict):
    pattern: str
    envVars: NotRequired[dict[str, str]]


# Env var names must look like a conventional shell identifier. This rejects
# garbage (spaces, semicolons, leading digits) that would produce baffling
# downstream errors. Not a security boundary — the caller workflow is already
# trusted with full job privileges.
KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Only envVars is recognized today; unknown keys are warned-and-ignored so that
# future extensions can be added without breaking older versions of this action.
KNOWN_BLOCK_KEYS = frozenset({"pattern", "envVars"})


def find_match(stack_dir: str, blocks: list[Block]) -> Block | None:
    """Return the first block whose pattern matches stack_dir, or None."""
    path = PurePosixPath(stack_dir)
    for block in blocks:
        if path.full_match(block["pattern"]):
            return block
    return None


def validate_block_shape(raw_blocks: object) -> list[Block]:
    """Validate that the parsed JSON matches the expected schema."""
    if not isinstance(raw_blocks, list):
        raise ValueError("stack-config must be a JSON array")
    result: list[Block] = []
    for i, block in enumerate(raw_blocks):
        if not isinstance(block, dict):
            raise ValueError(f"stack-config[{i}] must be an object")
        pattern = block.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError(f"stack-config[{i}] requires a string 'pattern'")
        env_vars = block.get("envVars", {})
        if not isinstance(env_vars, dict):
            raise ValueError(f"stack-config[{i}].envVars must be an object")
        unknown = set(block) - KNOWN_BLOCK_KEYS
        for key in sorted(unknown):
            print(
                f"::warning::stack-config[{i}]: unknown key '{key}' ignored "
                f"(recognized keys: {sorted(KNOWN_BLOCK_KEYS)})",
                file=sys.stderr,
            )
        result.append(block)  # type: ignore[arg-type]
    return result


def validate_env_vars(env_vars: dict[str, object]) -> dict[str, str]:
    """Validate keys and values of an envVars map."""
    validated: dict[str, str] = {}
    for key, value in env_vars.items():
        if not isinstance(key, str) or not KEY_RE.match(key):
            raise ValueError(
                f"envVars key {key!r} must match {KEY_RE.pattern}"
            )
        if not isinstance(value, str):
            raise ValueError(
                f"envVars[{key!r}] must be a string, got {type(value).__name__}"
            )
        validated[key] = value
    return validated


def write_env_file(env_vars: dict[str, str], env_file_dir: str) -> str:
    """Write shell-quoted KEY=VALUE lines to a private tempfile, return its path."""
    fd, path = tempfile.mkstemp(suffix=".env", dir=env_file_dir, text=True)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        for key in sorted(env_vars):
            f.write(f"{key}={shlex.quote(env_vars[key])}\n")
    return path


def resolve(
    stack_config: str, stack_dir: str, env_file_dir: str
) -> tuple[str, list[str]]:
    """Resolve the matching block and produce an env file.

    Returns (env_file_path, exported_keys). When no pattern matches (or the
    input has no blocks), returns ("", []) and no file is written; the caller
    is expected to guard its source against an empty path.
    """
    try:
        raw_blocks = json.loads(stack_config) if stack_config.strip() else []
    except json.JSONDecodeError as e:
        raise ValueError(f"stack-config is not valid JSON: {e}") from e

    blocks = validate_block_shape(raw_blocks)
    match = find_match(stack_dir, blocks)
    if match is None:
        return "", []

    env_vars = validate_env_vars(match.get("envVars", {}))
    if not env_vars:
        return "", []
    return write_env_file(env_vars, env_file_dir), sorted(env_vars)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Resolve a stack-config block list against a stack-dir"
    )
    parser.add_argument(
        "--stack-config",
        required=True,
        help="JSON array of stack-config blocks",
    )
    parser.add_argument(
        "--stack-dir",
        required=True,
        help="Path of the current Terraform stack (e.g., stacks/dev/app)",
    )
    parser.add_argument(
        "--env-file-dir",
        default=os.environ.get("RUNNER_TEMP", tempfile.gettempdir()),
        help="Directory in which to create the env file (default: $RUNNER_TEMP)",
    )
    args = parser.parse_args()

    try:
        env_file_path, exported = resolve(
            args.stack_config, args.stack_dir, args.env_file_dir
        )
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        sys.exit(1)

    for key in exported:
        print(f"Exported {key}", file=sys.stderr)

    print(env_file_path)
