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

    def test_rejects_unknown_package_with_default_stack_severities(self):
        """The default of the severities map is empty, which must also reject."""
        upgrades = [_upgrade(package_name="oslokommune/some-other-repo")]
        commit_message = _make_commit_message(upgrades)
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {}))


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


class TestPolicies(unittest.TestCase):
    def test_never_always_rejects(self):
        rules = [{"pattern": "**", "minor": "never"}]
        upgrades = [_upgrade(update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_no_changes_rejects_when_changed(self):
        rules = [{"pattern": "**", "patch": "no-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="patch")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "additive"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_additive_allows_only_additions(self):
        rules = [{"pattern": "**", "patch": "additive"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="patch")]
        commit_message = _make_commit_message(upgrades)
        self.assertTrue(ea.evaluate(commit_message, rules, {"stacks/dev/app": "additive"}))
        self.assertFalse(ea.evaluate(commit_message, rules, {"stacks/dev/app": "no-destroy"}))

    def test_no_destroy_rejects_destruction(self):
        rules = [{"pattern": "**", "patch": "no-destroy"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="patch")]
        commit_message = _make_commit_message(upgrades)
        self.assertTrue(ea.evaluate(commit_message, rules, {"stacks/dev/app": "no-destroy"}))
        self.assertFalse(ea.evaluate(commit_message, rules, {"stacks/dev/app": "any-changes"}))

    def test_any_changes_allows_regardless(self):
        rules = [{"pattern": "**", "major": "any-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="major")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "any-changes"}
        self.assertTrue(ea.evaluate(commit_message, rules, stack_severities))

    def test_unknown_severity_always_rejects(self):
        rules = [{"pattern": "**", "major": "any-changes"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="major")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "unknown"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_default_policy_is_no_changes(self):
        """If the rule doesn't specify a policy for the update type, default to no-changes."""
        rules = [{"pattern": "**"}]
        upgrades = [_upgrade(package_file_dir="stacks/dev/app", update_type="minor")]
        commit_message = _make_commit_message(upgrades)
        stack_severities = {"stacks/dev/app": "additive"}
        self.assertFalse(ea.evaluate(commit_message, rules, stack_severities))

    def test_unhandled_policy_rejects(self):
        """A policy in valid_policies without an evaluation branch must block."""
        rule = {"pattern": "**", "minor": "future-policy"}
        self.assertFalse(
            ea.evaluate_policy(
                rule, "minor", "no-changes", valid_policies=frozenset({"future-policy"})
            )
        )


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


class TestEdgeCases(unittest.TestCase):
    def test_empty_upgrades_rejects(self):
        commit_message = "<!--golden-path-renovate-summary:[]-->"
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {"stacks/dev/app": "no-changes"}))

    def test_missing_marker_rejects(self):
        commit_message = "just a normal commit message"
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, {"stacks/dev/app": "no-changes"}))

    def test_empty_stack_severities_rejects(self):
        """The default of the severities map is empty: no plan results means
        nothing has verified the upgrade, so it must reject."""
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
        stack_severities = {"stacks/dev/app": "no-destroy", "stacks/prod/app": "no-changes"}
        self.assertFalse(ea.evaluate(commit_message, DEFAULT_RULES, stack_severities))


if __name__ == "__main__":
    unittest.main()
