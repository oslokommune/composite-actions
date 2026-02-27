"""Evaluate whether Renovate upgrades to golden-path-boilerplate are safe to automerge.

Parses structured upgrade info from the commit message and evaluates it against
automerge rules and Terraform plan results.

Usage:
  python3 evaluate_automerge.py --commit-message <str> --rules <json> --stack-changes <json>

Output: prints "true" or "false" to stdout.
"""

import argparse
import json
import re
import sys
from pathlib import PurePosixPath
from typing import NotRequired, TypedDict


class Upgrade(TypedDict):
    packageName: str
    packageFileDir: str
    depName: str
    updateType: str
    currentValue: str
    newValue: str


class Rule(TypedDict):
    pattern: str
    major: NotRequired[str]
    minor: NotRequired[str]
    patch: NotRequired[str]


def parse_upgrades(
    commit_message: str,
    marker: re.Pattern = re.compile(r"<!--golden-path-renovate-summary:\[(.+?)\]-->"),
) -> list[Upgrade] | None:
    """Extract the upgrades array from the commit message marker.

    Returns None if the marker is not found.
    """
    match = marker.search(commit_message)
    if not match:
        return None
    return json.loads(f"[{match.group(1)}]")


def match_rule(package_file_dir: str, rules: list[Rule]) -> Rule | None:
    """Find the first rule whose pattern matches the packageFileDir."""
    path = PurePosixPath(package_file_dir)
    for rule in rules:
        if path.full_match(rule["pattern"]):
            return rule
    return None


def evaluate_upgrade(
    upgrade: Upgrade,
    rule: Rule,
    stack_changes: dict[str, bool],
    default_policy: str = "no-changes",
    valid_policies: frozenset[str] = frozenset({"never", "no-changes", "any-changes"}),
) -> bool:
    """Evaluate a single upgrade against its matched rule and plan result."""
    update_type = upgrade["updateType"]
    policy = rule.get(update_type, default_policy)

    if policy not in valid_policies:
        print(
            f"Warning: unknown policy '{policy}' for update type "
            f"'{update_type}', treating as '{default_policy}'",
            file=sys.stderr,
        )
        policy = default_policy

    if policy == "never":
        return False

    if policy == "any-changes":
        return True

    # policy == "no-changes": allow only if the stack has no Terraform changes
    package_file_dir = upgrade["packageFileDir"]
    has_changes = stack_changes.get(package_file_dir, False)
    return not has_changes


def evaluate(
    commit_message: str,
    rules: list[Rule],
    stack_changes: dict[str, bool],
    allowed_package: str = "oslokommune/golden-path-boilerplate",
) -> bool:
    """Returns True if all upgrades in the commit are eligible for automerge."""
    upgrades = parse_upgrades(commit_message)
    if upgrades is None:
        return False

    if len(upgrades) == 0:
        return False

    for upgrade in upgrades:
        if upgrade.get("packageName") != allowed_package:
            return False

        rule = match_rule(upgrade["packageFileDir"], rules)
        if rule is None:
            return False

        if not evaluate_upgrade(upgrade, rule, stack_changes):
            return False

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate whether golden-path-boilerplate upgrades are safe to automerge"
    )
    parser.add_argument("--commit-message", required=True, help="Full commit message")
    parser.add_argument("--rules", required=True, help="JSON array of automerge rules")
    parser.add_argument(
        "--stack-changes",
        required=True,
        help="JSON object mapping stack paths to booleans",
    )
    args = parser.parse_args()

    result = evaluate(
        args.commit_message,
        json.loads(args.rules),
        json.loads(args.stack_changes),
    )
    print("true" if result else "false")
