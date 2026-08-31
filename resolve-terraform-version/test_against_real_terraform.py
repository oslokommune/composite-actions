"""Differential test: our constraint matcher vs. real Terraform binaries.

The matcher (`satisfies()`) reimplements Terraform's `required_version` semantics,
so the only trustworthy oracle is Terraform itself. Each constraint is written into
a throwaway stack and `terraform init` is asked whether it accepts it; the answer
must match `satisfies()`.

Terraform binaries are not part of the test environment, so this is skipped unless
`TERRAFORM_BINARIES` names them (colon-separated absolute paths). `make test-against-real-terraform`
points it at whichever terraform is on PATH; CI runs one job per version via
`hashicorp/setup-terraform`, so the matrix covers all of them between them.
"""

import json
import os
import subprocess

import pytest
from resolve_terraform_version import Version, parse_constraint, satisfies

BINARIES = [p for p in os.environ.get("TERRAFORM_BINARIES", "").split(":") if p]

# How CI runs this test, with an example, is explained in .github/workflows/ci.yml
# (job test-resolve-terraform-version).
#
# Some constraints use a matrix version as their boundary (e.g. "= 1.10.5", "<= 1.2.9") to test edge cases.
#
# The matrix versions each exercise a different edge case: a current release (1.15.8),
# a version where npm semver and Terraform disagree on "~> 1.10" (1.10.5), and an old
# 1.x release (1.2.9).
CONSTRAINTS = [
    ">= 1.10.0",
    ">=1.10.0",
    ">= 0.13.1",
    ">= 1.14.7",
    "~> 1",
    "~> 1.1",
    "~> 1.2",
    "~> 1.10",
    "~> 1.15",
    "~> 0.13",
    "~> 1.2.3",
    "~> 1.2.9",
    "~> 1.10.0",
    "~> 1.10.5",
    "~> 1.11.3",
    "= 1.10.5",
    "= 1.5.2",
    "1.2.9",
    "!= 1.10.5",
    "> 1.2",
    "> 1.2.9",
    "< 1.10.5",
    "<= 1.2.9",
    ">= 1.10.0, < 2.0.0",
    ">= 1.0.0, < 1.11.0",
    ">= 6.0.0, < 7.0.0",
    "~> 1.9, < 1.16.0",
    "!= 1.2.9, >= 1.0.0",
]

pytestmark = pytest.mark.skipif(
    not BINARIES, reason="TERRAFORM_BINARIES is not set; see this module's docstring"
)


def binary_version(binary: str) -> str:
    """Return the Terraform binary's own version, e.g. "1.15.8"."""
    output = subprocess.run([binary, "version", "-json"], capture_output=True, text=True, check=True)
    return json.loads(output.stdout)["terraform_version"]


def terraform_accepts(binary: str, constraint: str, stack_dir) -> bool:
    """Return whether the Terraform binary accepts a stack with this `required_version` constraint."""
    (stack_dir / "main.tf").write_text('terraform {\n  required_version = "%s"\n}\n' % constraint)
    result = subprocess.run(
        [binary, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=stack_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True
    if "Unsupported Terraform Core version" in result.stdout + result.stderr:
        return False
    raise AssertionError(f"Unexpected `terraform init` failure for {constraint!r}:\n{result.stderr}")


@pytest.mark.parametrize("binary", BINARIES, ids=lambda b: os.path.basename(os.path.dirname(b)))
@pytest.mark.parametrize("constraint", CONSTRAINTS)
def test_matcher_agrees_with_terraform(binary, constraint, tmp_path):
    version = binary_version(binary)
    expected = terraform_accepts(binary, constraint, tmp_path)
    actual = satisfies(Version.parse(version), parse_constraint(constraint))
    assert actual == expected, (
        f"Terraform {version} {'accepts' if expected else 'rejects'} {constraint!r}, "
        f"but the matcher said {actual}"
    )
