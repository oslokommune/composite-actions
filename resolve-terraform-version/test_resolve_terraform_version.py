#!/usr/bin/env python3
"""Tests for resolve_terraform_version.py. Run with: python3 test_resolve_terraform_version.py"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from resolve_terraform_version import read_denylist, read_required_versions, to_range

SCRIPT = Path(__file__).with_name("resolve_terraform_version.py")


def read_constraints(directory):
    return [constraint for _, constraint in read_required_versions(directory)]


def write_files(files):
    directory = tempfile.mkdtemp()
    for name, content in files.items():
        Path(directory, name).write_text(content)
    return directory


class TestToRange(unittest.TestCase):
    def test_ranges(self):
        cases = [
            # (required_version constraints, excluded versions, expected range)
            ([">= 1.9.0"], [], ">=1.9.0"),
            (["= 1.9.2"], [], "=1.9.2"),
            (["1.9.0"], [], "=1.9.0"),
            (["= 1.9"], [], "=1.9.0"),
            (["<2, >1.10.0"], [], "<2.0.0 >1.10.0"),
            (["~> 1.9.0"], [], ">=1.9.0 <1.10.0"),
            (["~> 1.9"], [], ">=1.9.0 <2.0.0"),
            (["~> 1"], [], ">=1.0.0"),
            ([">= 1.9.0", "< 1.10.0"], [], ">=1.9.0 <1.10.0"),
            ([], [], "latest"),
            ([], ["1.9.3"], "<1.9.3 || >1.9.3"),
            ([">= 1.9.0"], ["1.9.3"], ">=1.9.0 <1.9.3 || >=1.9.0 >1.9.3"),
            (
                ["<2, >1.10.0"],
                ["1.16.4", "1.15.9", "1.16.3"],
                "<2.0.0 >1.10.0 <1.15.9 || <2.0.0 >1.10.0 >1.15.9 <1.16.3"
                " || <2.0.0 >1.10.0 >1.16.3 <1.16.4 || <2.0.0 >1.10.0 >1.16.4",
            ),
            (["~> 1.9.0, != 1.9.3"], [], ">=1.9.0 <1.10.0 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3"),
            (["~> 1.9.0, != 1.9.3"], ["1.9.3"], ">=1.9.0 <1.10.0 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3"),
            (["~> 1.9.0, != 1.9.2"], ["1.9.3"], ">=1.9.0 <1.10.0 <1.9.2 || >=1.9.0 <1.10.0 >1.9.2 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3"),
            # Sorted by number, not by text
            ([], ["1.10.0", "1.9.0"], "<1.9.0 || >1.9.0 <1.10.0 || >1.10.0"),
            # A deny list that excludes every allowed version is ignored
            (["= 1.9.1"], ["1.9.1"], "=1.9.1"),
            ([">= 1.9.1, <= 1.9.1"], ["1.9.1"], ">=1.9.1 <=1.9.1"),
            (["= 1.9.1, != 1.9.2"], ["1.9.1"], "=1.9.1 <1.9.2 || =1.9.1 >1.9.2"),
            # A deny list that leaves some allowed version is used
            (["= 1.9.1"], ["1.9.2"], "=1.9.1 <1.9.2 || =1.9.1 >1.9.2"),
            ([">= 1.9.1, <= 1.9.2"], ["1.9.1"], ">=1.9.1 <=1.9.2 <1.9.1 || >=1.9.1 <=1.9.2 >1.9.1"),
        ]
        for constraints, excluded, want in cases:
            with self.subTest(constraints=constraints, excluded=excluded):
                self.assertEqual(to_range(constraints, excluded), want)

    def test_rejects_unsupported_constraints(self):
        for constraint in ["banana", ">= 1.10.0-beta1", ">= 1.2.3.4", ">= 1.9.0 < 2.0.0"]:
            with self.subTest(constraint=constraint), self.assertRaises(ValueError):
                to_range([constraint], [])


class TestReadRequiredVersions(unittest.TestCase):
    def test_reads_all_tf_files(self):
        directory = write_files(
            {
                "versions.tf": 'terraform {\n  required_version = ">= 1.9.0"\n}\n',
                "main.tf": 'terraform {\n  # required_version = "= 1.5.7"\n  required_version = "< 1.10.0"\n}\n',
                "outputs.tf": 'output "x" { value = 1 }\n',
                "main.tf.json": '{"terraform": {"required_version": "= 1.5.7"}}',
                "notes.txt": 'required_version = "= 1.5.7"\n',
            }
        )
        self.assertEqual(read_constraints(directory), ["< 1.10.0", ">= 1.9.0"])

    def test_override_files(self):
        versions = 'terraform {\n  required_version = "~> 1.9.0"\n}\n'
        cases = [
            ("override file replaces required_version", {"versions_override.tf": 'terraform {\n  required_version = "~> 1.10.0"\n}\n'}, ["~> 1.10.0"]),
            ("override.tf replaces required_version", {"override.tf": 'terraform {\n  required_version = "= 1.5.7"\n}\n'}, ["= 1.5.7"]),
            ("override file without required_version keeps primary", {"backend_override.tf": 'terraform {\n  backend "s3" {}\n}\n'}, ["~> 1.9.0"]),
            (
                "last override file wins",
                {"a_override.tf": 'terraform {\n  required_version = "= 1.5.7"\n}\n', "b_override.tf": 'terraform {\n  required_version = "~> 1.10.0"\n}\n'},
                ["~> 1.10.0"],
            ),
            (
                "override file with required_version wins over a later one without",
                {"a_override.tf": 'terraform {\n  required_version = "= 1.5.7"\n}\n', "b_override.tf": 'terraform {\n  backend "s3" {}\n}\n'},
                ["= 1.5.7"],
            ),
            ("name without underscore is primary", {"nooverride.tf": 'terraform {\n  required_version = "!= 1.9.3"\n}\n'}, ["!= 1.9.3", "~> 1.9.0"]),
            ("reads files starting with underscores", {"__gp_versions.tf": 'terraform {\n  required_version = "< 1.9.5"\n}\n'}, ["< 1.9.5", "~> 1.9.0"]),
            ("ignores hidden files", {".#versions.tf": 'terraform {\n  required_version = "= 1.5.7"\n}\n'}, ["~> 1.9.0"]),
        ]
        for name, files, want in cases:
            with self.subTest(name):
                directory = write_files({"versions.tf": versions, **files})
                self.assertEqual(read_constraints(directory), want)

    def test_reads_only_terraform_blocks(self):
        cases = [
            ("single-line block", 'terraform { required_version = "~> 1.5.0" }\n', ["~> 1.5.0"]),
            ("block comment", '/*\nterraform {\n  required_version = "= 0.13.0"\n}\n*/\n', []),
            ("line comments", 'terraform {\n  // required_version = "= 0.13.0"\n  # required_version = "= 0.12.0"\n}\n', []),
            ("module argument", 'module "x" {\n  required_version = "= 0.13.0"\n}\n', []),
            ("nested block", 'terraform {\n  cloud {\n    required_version = "= 0.13.0"\n  }\n}\n', []),
            ("attribute outside block", 'required_version = "= 0.13.0"\n', []),
            ("braces in strings", 'locals {\n  x = "}"\n}\nterraform {\n  y = "{"\n  required_version = ">= 1.9"\n}\n', [">= 1.9"]),
            ("comment marker in string", 'terraform {\n  x = "a # b /* c"\n  required_version = ">= 1.9"\n}\n', [">= 1.9"]),
        ]
        for name, content, want in cases:
            with self.subTest(name):
                self.assertEqual(read_constraints(write_files({"main.tf": content})), want)

    def test_skips_directories(self):
        directory = write_files({"versions.tf": 'terraform {\n  required_version = ">= 1.9.0"\n}\n'})
        Path(directory, "modules.tf").mkdir()
        self.assertEqual(read_constraints(directory), [">= 1.9.0"])

    def test_no_required_version(self):
        directory = write_files({"main.tf": 'output "x" { value = 1 }\n'})
        self.assertEqual(read_constraints(directory), [])


class TestReadDenylist(unittest.TestCase):
    def test_valid(self):
        directory = write_files(
            {
                "denylist.json": """{
  "denied": [
    {"version": "1.9.3", "reason": "breaks the S3 backend"},
    {"version": "1.10"},
    {"version": "banana", "reason": "not a version"},
    "1.9.2"
  ]
}"""
            }
        )
        self.assertEqual(
            read_denylist(Path(directory, "denylist.json")),
            {"1.9.3": "breaks the S3 backend", "1.10.0": "no reason given"},
        )

    def test_invalid(self):
        # gh api writes the error response to stdout when the download fails
        for content in ["1.9.3 # not JSON", '{"message":"Not Found","status":"404"}', "[]"]:
            directory = write_files({"denylist.json": content})
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_denylist(Path(directory, "denylist.json"))

    def test_missing_file(self):
        with self.assertRaises(OSError):
            read_denylist(Path(tempfile.mkdtemp(), "missing.json"))


class TestMain(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True)

    def test_applies_denylist(self):
        directory = write_files(
            {
                "versions.tf": 'terraform {\n  required_version = "~> 1.9.0"\n}\n',
                "denylist.json": '{"denied": [{"version": "1.9.3", "reason": "broken"}]}',
            }
        )
        result = self.run_script("--dir", directory, "--denylist", str(Path(directory, "denylist.json")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), ">=1.9.0 <1.10.0 <1.9.3 || >=1.9.0 <1.10.0 >1.9.3")
        self.assertIn("1.9.3 (broken)", result.stderr)

    def test_ignores_unreadable_denylist(self):
        directory = write_files({"versions.tf": 'terraform {\n  required_version = "~> 1.9.0"\n}\n'})
        result = self.run_script("--dir", directory, "--denylist", str(Path(directory, "missing.json")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), ">=1.9.0 <1.10.0")
        self.assertIn("::warning::", result.stderr)

    def test_fails_on_unsupported_constraint(self):
        directory = write_files({"versions.tf": 'terraform {\n  required_version = "banana"\n}\n'})
        result = self.run_script("--dir", directory)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("versions.tf: unsupported required_version constraint 'banana'", result.stderr)

    def test_ignores_denylist_that_excludes_every_allowed_version(self):
        directory = write_files(
            {
                "versions.tf": 'terraform {\n  required_version = "= 1.9.1"\n}\n',
                "denylist.json": '{"denied": [{"version": "1.9.1", "reason": "broken"}]}',
            }
        )
        result = self.run_script("--dir", directory, "--denylist", str(Path(directory, "denylist.json")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "=1.9.1")
        self.assertIn("::warning::Ignoring deny list", result.stderr)


if __name__ == "__main__":
    unittest.main()
