import json
import os
import shlex
import stat
import tempfile
import unittest

import resolve_stack_config as rsc


def _resolve(stack_config_obj, stack_dir: str) -> tuple[str, list[str]]:
    """Run resolve() with the test's tempdir as env-file location."""
    return rsc.resolve(
        json.dumps(stack_config_obj),
        stack_dir,
        env_file_dir=tempfile.gettempdir(),
    )


def _read_env_file(path: str) -> dict[str, str]:
    """Parse a written env file back into a dict for assertions."""
    result: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            key, _, raw_value = line.partition("=")
            parsed = shlex.split(raw_value)
            result[key] = parsed[0] if parsed else ""
    return result


class TestMatching(unittest.TestCase):
    def test_first_match_wins(self):
        """Critical: later catch-all patterns must not override an earlier specific match."""
        blocks = [
            {"pattern": "stacks/dev/**", "envVars": {"FOO": "dev"}},
            {"pattern": "**", "envVars": {"FOO": "fallback"}},
        ]
        path, exported = _resolve(blocks, "stacks/dev/app")
        self.assertEqual(exported, ["FOO"])
        self.assertEqual(_read_env_file(path)["FOO"], "dev")

    def test_no_match_produces_empty_path(self):
        """No match returns an empty path (no file written) so the caller can skip sourcing."""
        blocks = [{"pattern": "stacks/dev/**", "envVars": {"FOO": "bar"}}]
        path, exported = _resolve(blocks, "stacks/prod/app")
        self.assertEqual(exported, [])
        self.assertEqual(path, "")


class TestSchemaValidation(unittest.TestCase):
    def test_invalid_json_raises(self):
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            rsc.resolve("{not json", "stacks/dev/app", tempfile.gettempdir())

    def test_empty_string_is_valid_and_means_no_blocks(self):
        """Empty string is the action's default — must not be rejected as invalid JSON.
        Returns the same "" path as no-match, so the caller's source-guard handles both."""
        path, exported = rsc.resolve("", "stacks/dev/app", tempfile.gettempdir())
        self.assertEqual(exported, [])
        self.assertEqual(path, "")

    def test_malformed_blocks_rejected(self):
        """Each shape error should produce a distinct, actionable message."""
        cases = [
            ('{"pattern": "x"}', "must be a JSON array"),
            ('["not-an-object"]', "must be an object"),
            ('[{"envVars": {"FOO": "bar"}}]', "requires a string 'pattern'"),
            ('[{"pattern": "**", "envVars": "foo"}]', "envVars must be an object"),
        ]
        for raw, expected_msg in cases:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, expected_msg):
                    rsc.resolve(raw, "stacks/dev/app", tempfile.gettempdir())


class TestKeyValidation(unittest.TestCase):
    def test_keys_with_shell_metachars_rejected(self):
        """Shell metachars in keys would break out of KEY=VAL when sourcing — must be blocked at the regex layer."""
        for bad_key in ("X; rm -rf /; FOO", "FOO BAR", "FOO=BAR", "1FOO"):
            with self.subTest(bad_key=bad_key):
                with self.assertRaisesRegex(ValueError, "must match"):
                    _resolve(
                        [{"pattern": "**", "envVars": {bad_key: "x"}}],
                        "stacks/dev/app",
                    )

    def test_tf_var_with_lowercase_suffix_allowed(self):
        """Terraform's TF_VAR_<name> convention uses lowercase suffixes; the regex must permit this."""
        path, _ = _resolve(
            [{"pattern": "**", "envVars": {"TF_VAR_region": "eu-west-1"}}],
            "stacks/dev/app",
        )
        self.assertEqual(_read_env_file(path)["TF_VAR_region"], "eu-west-1")


class TestValueValidation(unittest.TestCase):
    def test_non_string_value_rejected(self):
        """Non-string values would crash shlex.quote with an unhelpful TypeError;
        convert to a clean ValueError at validation time."""
        with self.assertRaisesRegex(ValueError, "must be a string"):
            _resolve([{"pattern": "**", "envVars": {"FOO": 123}}], "stacks/dev/app")


class TestEnvFileSecurity(unittest.TestCase):
    def test_shell_injection_via_value_does_not_execute(self):
        """The crown-jewel security test: a value designed to break out of single-quoting
        must round-trip as a literal string after `source`."""
        evil = "'; rm -rf /; echo $(uname) `id` \"x\""
        path, _ = _resolve(
            [{"pattern": "**", "envVars": {"FOO": evil}}], "stacks/dev/app"
        )
        self.assertEqual(_read_env_file(path)["FOO"], evil)

    def test_env_file_is_user_only_readable(self):
        """File contains values that aren't strictly secret today but might be tomorrow;
        keep it 0600 as a baseline."""
        path, _ = _resolve(
            [{"pattern": "**", "envVars": {"FOO": "bar"}}], "stacks/dev/app"
        )
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class TestForwardCompatibility(unittest.TestCase):
    def test_unknown_block_keys_are_warned_not_rejected(self):
        """Unknown keys must be warned-and-ignored, not rejected, so that future
        extensions can be added without breaking older versions of this action."""
        path, _ = _resolve(
            [
                {
                    "pattern": "**",
                    "envVars": {"FOO": "bar"},
                    "futureFeature": {"some": "value"},
                }
            ],
            "stacks/dev/app",
        )
        self.assertEqual(_read_env_file(path)["FOO"], "bar")


if __name__ == "__main__":
    unittest.main()
