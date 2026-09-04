import json
import unittest

import evaluate_automerge as ea


def _make_commit_message(upgrades: list) -> str:
    """Build a commit message containing the golden-path-renovate-summary marker."""
    inner = ",".join(json.dumps(u, separators=(",", ":")) for u in upgrades)
    return f"some text\n<!--golden-path-renovate-summary:[{inner}]-->\nmore text"


def _upgrade(
    *,
    package_file_dir="stacks/dev/app",
    dep_name="app",
    update_type="minor",
    current_value="1.0.0",
    new_value="1.1.0",
    package_name="oslokommune/golden-path-boilerplate",
):
    return {
        "packageName": package_name,
        "packageFileDir": package_file_dir,
        "depName": dep_name,
        "updateType": update_type,
        "currentValue": current_value,
        "newValue": new_value,
    }


DEFAULT_RULES = [
    {"pattern": "**/prod/**", "major": "never", "minor": "no-changes", "patch": "any-changes"},
    {"pattern": "**", "major": "no-changes", "minor": "any-changes", "patch": "any-changes"},
]


class TestPackageAllowList(unittest.TestCase):
    def test_rejects_unknown_package(self):
        upgrades = [_upgrade(package_name="oslokommune/some-other-repo")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, stack_severities))


class TestPatternMatching(unittest.TestCase):
    def test_first_match_wins_prod(self):
        """prod pattern matches first, so major=never applies."""
        upgrades = [_upgrade(package_file_dir="stacks/prod/app", update_type="major")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/prod/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, stack_severities))

    def test_first_match_wins_dev(self):
        """dev doesn't match prod pattern, falls through to ** catch-all."""
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="major")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes"}
        self.assertTrue(ea.evaluate(commit_message, DEFAULT_RULES, stack_severities))

    def test_no_matching_rule_rejects(self):
        rules = [{"pattern": "stacks/prod/**", "patch": "any-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="patch")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_double_star_matches_zero_leading_segments(self):
        """prod/my-stack matches **/prod/**."""
        rules = [{"pattern": "**/prod/**", "patch": "any-changes"}]
        upgrades = [_upgrade(package_file_dir="prod/my-stack", update_type="patch")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"prod/my-stack": "no-changes"}
        self.assertTrue(ea.evaluate(commit_message, rules, stack_severities))


class TestPolicyLadder(unittest.TestCase):
    """Policies and severities share one severity ladder: a policy names the
    highest severity it tolerates, and "unknown" is tolerated by nothing."""

    # policy -> severity -> expected eligibility
    MATRIX = {
        "never": {
            "no-changes": False,
            "additive": False,
            "no-destroy": False,
            "any-changes": False,
            "unknown": False,
        },
        "no-changes": {
            "no-changes": True,
            "additive": False,
            "no-destroy": False,
            "any-changes": False,
            "unknown": False,
        },
        "additive": {
            "no-changes": True,
            "additive": True,
            "no-destroy": False,
            "any-changes": False,
            "unknown": False,
        },
        "no-destroy": {
            "no-changes": True,
            "additive": True,
            "no-destroy": True,
            "any-changes": False,
            "unknown": False,
        },
        "any-changes": {
            "no-changes": True,
            "additive": True,
            "no-destroy": True,
            "any-changes": True,
            "unknown": False,
        },
    }

    def test_policy_severity_matrix(self):
        for policy, severities in self.MATRIX.items():
            for severity, expected in severities.items():
                with self.subTest(policy=policy, severity=severity):
                    rules = [{"pattern": "**", "minor": policy}]
                    upgrades = [_upgrade(update_type="minor")]
                    commit_message = _make_commit_message(upgrades)
                    stack_severities = {"stacks/dev/app": severity}
                    self.assertEqual(
                        expected, ea.evaluate(commit_message, rules, stack_severities)
                    )

    def test_default_policy_is_no_changes(self):
        """If the rule doesn't specify a policy for the update type, default to no-changes."""
        rules = [{"pattern": "**"}]
        upgrades = [_upgrade(update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        self.assertTrue(ea.evaluate(commit_message, rules, {"stacks/dev/app": "no-changes"}))
        self.assertFalse(ea.evaluate(commit_message, rules, {"stacks/dev/app": "additive"}))

    def test_unknown_policy_falls_back_to_no_changes(self):
        rules = [{"pattern": "**", "minor": "sometimes"}]
        upgrades = [_upgrade(update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        self.assertTrue(ea.evaluate(commit_message, rules, {"stacks/dev/app": "no-changes"}))
        self.assertFalse(ea.evaluate(commit_message, rules, {"stacks/dev/app": "additive"}))


class TestAllPlannedStacksEvaluated(unittest.TestCase):
    """Stacks changed by a render but not holding a package file (e.g. app-data) are evaluated."""

    def test_companion_stack_with_changes_rejects(self):
        """app has no changes, but the rendered app-data stack does."""
        rules = [{"pattern": "**", "minor": "no-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes", "stacks/dev/app-data": "no-destroy"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_companion_stack_without_changes_allows(self):
        rules = [{"pattern": "**", "minor": "no-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes", "stacks/dev/app-data": "no-changes"}
        self.assertTrue(ea.evaluate(commit_message, rules, stack_severities))

    def test_companion_judged_by_its_own_severity(self):
        """The companion's own plan severity decides, not the upgraded stack's."""
        rules = [{"pattern": "**", "minor": "additive"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "additive", "stacks/dev/app-data": "any-changes"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_companion_inherits_sibling_update_type(self):
        """A companion stack is judged by its sibling upgrade's type, not the whole PR's."""
        rules = [{"pattern": "**", "minor": "any-changes", "major": "no-changes"}]
        upgrades = [
            _upgrade(package_file_dir="stacks/dev/app", update_type="minor"),
            _upgrade(package_file_dir="stacks/dev/other", update_type="major"),
        ]
        commit_message = _make_commit_message(upgrades)
        # app-data inherits app's minor (any-changes); the unrelated major
        # upgrade's stricter policy does not apply to it.
        stack_severities = {
            "stacks/dev/app": "no-changes",
            "stacks/dev/app-data": "no-destroy",
            "stacks/dev/other": "no-changes",
        }
        self.assertTrue(ea.evaluate(commit_message, rules, stack_severities))

    def test_unattributable_stack_rejects(self):
        """A planned stack that belongs to no upgrade blocks automerge."""
        rules = [{"pattern": "**", "minor": "any-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes", "stacks/dev/unrelated": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_data_stack_with_own_upgrade_takes_priority(self):
        """A standalone stack named *-data uses its own update type, not the sibling's."""
        rules = [{"pattern": "**", "major": "never", "patch": "any-changes"}]
        upgrades = [
            _upgrade(package_file_dir="stacks/dev/foo", update_type="patch"),
            _upgrade(package_file_dir="stacks/dev/foo-data", update_type="major"),
        ]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/foo": "no-changes", "stacks/dev/foo-data": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))


class TestFailSafes(unittest.TestCase):
    def test_policy_without_evaluation_branch_blocks(self):
        """A policy added to valid_policies without a matching branch must block, not allow."""
        rule = {"pattern": "**", "minor": "future-policy"}
        self.assertFalse(
            ea.evaluate_policy(
                rule, "minor", "no-changes", valid_policies=frozenset({"future-policy"})
            )
        )


class TestEdgeCases(unittest.TestCase):
    def test_empty_upgrades_rejects(self):
        commit_message = "<!--golden-path-renovate-summary:[]-->"
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {"stacks/dev/app": "no-changes"}))

    def test_missing_marker_rejects(self):
        commit_message = "just a normal commit message"
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {"stacks/dev/app": "no-changes"}))

    def test_empty_stack_severities_rejects(self):
        """No plan results means nothing has verified the upgrade."""
        upgrades = [_upgrade()]
        commit_message = _make_commit_message(upgrades)
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {}))


class TestMultipleUpgrades(unittest.TestCase):
    def test_one_failure_rejects_all(self):
        upgrades = [
            _upgrade(package_file_dir="stacks/dev/app", update_type="minor"),
            _upgrade(package_file_dir="stacks/prod/app", update_type="major"),
        ]
        commit_message = _make_commit_message(upgrades)
        # dev minor tolerates any-changes; prod major is never, so even a
        # clean prod plan blocks the whole PR.
        stack_severities = {"stacks/dev/app": "no-destroy", "stacks/prod/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, stack_severities))


if __name__ == "__main__":
    unittest.main()
