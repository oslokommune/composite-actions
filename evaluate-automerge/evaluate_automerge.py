"""Evaluate whether Renovate upgrades to golden-path-boilerplate are safe to automerge.

Parses structured upgrade info from the commit message and evaluates every
planned stack against the automerge rules and Terraform plan results.

The commit message tells us WHAT was upgraded (package and update types).
The plan results tell us WHERE the upgrade caused changes and HOW severe
they are (the "changes" field). Each rule's policy names the most severe
changes value it tolerates. A template upgrade can change stacks other than
the one holding the upgraded package file (for example, the `app` template
renders a companion `app-data` stack), so every stack that has a plan result
is evaluated -- not just the upgraded ones.

Each planned stack must be attributable to an upgrade: either the stack holds
an upgraded package file itself (checked first, so standalone stacks whose
name happens to end in `-data` use their own update type), or it is a `-data`
companion rendered by a sibling upgrade and inherits that upgrade's update
type. A planned stack attributable to no upgrade blocks automerge.

Usage:
  python3 evaluate_automerge.py --commit-message <str> --rules <json> --stack-results <json>

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


def match_rule(stack: str, rules: list[Rule]) -> Rule | None:
    """Find the first rule whose pattern matches the stack path."""
    path = PurePosixPath(stack)
    for rule in rules:
        if path.full_match(rule["pattern"]):
            return rule
    return None


def evaluate_policy(
    rule: Rule,
    update_type: str,
    changes: str | None,
    default_policy: str = "no-changes",
    valid_policies: frozenset[str] = frozenset(
        {"never", "no-changes", "additive", "non-destructive", "any-changes"}
    ),
    valid_changes: frozenset[str] = frozenset(
        {"no-changes", "additive", "non-destructive", "any-changes"}
    ),
) -> bool:
    """Evaluate a single stack's changes value against the rule's policy for an update type.

    Policies and changes values share the ladder
    no-changes < additive < non-destructive < any-changes.
    A policy allows changes values up to and including its own;
    "never" allows nothing.
    """
    policy = rule.get(update_type, default_policy)

    if policy not in valid_policies:
        print(
            f"Warning: unknown policy '{policy}' for update type "
            f"'{update_type}', treating as '{default_policy}'",
            file=sys.stderr,
        )
        policy = default_policy

    # Fail safe: an unrankable changes value is tolerated by no policy
    if changes not in valid_changes:
        print(
            f"Blocking automerge: changes value '{changes}' is not rankable",
            file=sys.stderr,
        )
        return False

    if policy == "never":
        return False

    if policy == "no-changes":
        return changes == "no-changes"

    if policy == "additive":
        return changes in ("no-changes", "additive")

    if policy == "non-destructive":
        return changes in ("no-changes", "additive", "non-destructive")

    if policy == "any-changes":
        return changes in ("no-changes", "additive", "non-destructive", "any-changes")

    # Fail safe: unreachable; a policy without a branch must block
    print(
        f"Blocking automerge: policy '{policy}' has no evaluation branch",
        file=sys.stderr,
    )
    return False


def evaluate(
    commit_message: str,
    rules: list[Rule],
    stack_results: dict[str, dict],
    allowed_package: str = "oslokommune/golden-path-boilerplate",
    companion_suffix: str = "-data",
) -> bool:
    """Returns True only if every planned stack is eligible for automerge."""
    upgrades = parse_upgrades(commit_message)
    if not upgrades:
        return False

    # Fail safe: without plan results, nothing has verified the upgrade
    if not stack_results:
        return False

    # Maps packageFileDir to its update types (major, minor, patch)
    update_types_by_dir: dict[str, set[str]] = {}
    for upgrade in upgrades:
        if upgrade.get("packageName") != allowed_package:
            return False
        update_types_by_dir.setdefault(upgrade["packageFileDir"], set()).add(
            upgrade["updateType"]
        )

    for stack, result in stack_results.items():
        # Fail safe: a failed plan has verified nothing
        if not result.get("success"):
            return False

        changes = result.get("changes")

        # A stack holding an upgraded package file uses its own update type.
        # Otherwise a `-data` stack is assumed to be a companion rendered by
        # the sibling upgrade and inherits its update type.
        update_types = update_types_by_dir.get(stack)
        if update_types is None and stack.endswith(companion_suffix):
            update_types = update_types_by_dir.get(stack.removesuffix(companion_suffix))
        if update_types is None:
            return False

        rule = match_rule(stack, rules)
        if rule is None:
            return False

        for update_type in update_types:
            if not evaluate_policy(rule, update_type, changes):
                return False

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evaluate whether golden-path-boilerplate upgrades are safe to automerge"
    )
    parser.add_argument("--commit-message", required=True, help="Full commit message")
    parser.add_argument("--rules", required=True, help="JSON array of automerge rules")
    parser.add_argument(
        "--stack-results",
        required=True,
        help="JSON object mapping stack paths to plan results; reads the 'success' and 'changes' fields",
    )
    args = parser.parse_args()

    result = evaluate(
        args.commit_message,
        json.loads(args.rules),
        json.loads(args.stack_results),
    )
    print("true" if result else "false")
