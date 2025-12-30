"""Unit tests for determine-stacks.py"""

import sys
import os
import json
import io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from determine_stacks import run, DEFAULT_PATTERNS, is_terraform_stack


def run_main(changed_files=None, glob_filter="", event_name="push"):
    changed_files = changed_files or []

    os.environ["GLOB_FILTER"] = glob_filter
    os.environ["GITHUB_EVENT_NAME"] = event_name
    os.environ["DEV_FILES_PATH"] = "stacks/dev"
    os.environ["PROD_FILES_PATH"] = "stacks/prod"
    os.environ["CHANGED_FILES"] = json.dumps(changed_files)

    writer = io.StringIO()
    root = Path("testdata")
    run(writer, root)

    lines = writer.getvalue().strip().split("\n")

    result = {}
    for line in lines:
        if line.strip():
            key, value = line.split("=", 1)
            result[key] = json.loads(value)

    return result


def test_mixed_stacks():
    """Mix of infrastructure and app stacks."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/networking/main.tf",
        "stacks/dev/app-custom/main.tf",
        "stacks/dev/iam/main.tf",
        "stacks/prod/dns/main.tf",
        "stacks/prod/app-hello/main.tf",
    ]
    result = run_main(changed_files=files)

    assert result["dev-sequential"] == ["stacks/dev/networking", "stacks/dev/iam"]
    assert result["dev-parallel"] == ["stacks/dev/app-custom", "stacks/dev/app-too-tikki"]
    assert result["prod-sequential"] == ["stacks/prod/dns"]
    assert result["prod-parallel"] == ["stacks/prod/app-hello"]
    assert result["all-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
        "stacks/dev/iam",
        "stacks/dev/networking",
        "stacks/prod/app-hello",
        "stacks/prod/dns",
    ]


def test_non_matching_glob_pattern():
    """Non-matching glob pattern."""
    result = run_main(changed_files=None, glob_filter="stacks/doesnotexist/*", event_name="workflow_dispatch")

    assert result["dev-sequential"] == []
    assert result["dev-parallel"] == []
    assert result["prod-sequential"] == []
    assert result["prod-parallel"] == []
    assert result["all-stacks"] == []


def test_glob_pattern():
    """Mix of stacks, filter by glob pattern."""
    result = run_main(changed_files=None, glob_filter="stacks/*/app-*", event_name="workflow_dispatch")

    assert result["dev-sequential"] == []
    assert result["dev-parallel"] == ["stacks/dev/app-custom", "stacks/dev/app-too-tikki"]
    assert result["prod-sequential"] == []
    assert result["prod-parallel"] == ["stacks/prod/app-hello", "stacks/prod/app-too-tikki"]
    assert result["all-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
        "stacks/prod/app-hello",
        "stacks/prod/app-too-tikki",
    ]


def test_is_terraform_stack_with_backend():
    """Directory with backend "s3" in .tf file is a valid Terraform stack."""
    root = Path("testdata")
    assert is_terraform_stack(root, "stacks/dev/app-too-tikki") is True
    assert is_terraform_stack(root, "stacks/dev/networking") is True


def test_is_terraform_stack_without_backend():
    """Directory without backend "s3" in .tf file is not a valid Terraform stack."""
    root = Path("testdata")
    # backup/bin has no .tf files
    assert is_terraform_stack(root, "stacks/dev/backup/bin") is False


def test_is_terraform_stack_nonexistent_dir():
    """Non-existent directory is not a valid Terraform stack."""
    root = Path("testdata")
    assert is_terraform_stack(root, "stacks/dev/nonexistent") is False


def test_filters_non_terraform_directories():
    """Non-Terraform directories are filtered out from results."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/backup/bin/script.sh",  # Not a Terraform stack
    ]
    result = run_main(changed_files=files)

    assert result["dev-parallel"] == ["stacks/dev/app-too-tikki"]
    assert "stacks/dev/backup/bin" not in result["dev-parallel"]
    assert "stacks/dev/backup/bin" not in result["dev-sequential"]
    assert result["all-stacks"] == ["stacks/dev/app-too-tikki"]
