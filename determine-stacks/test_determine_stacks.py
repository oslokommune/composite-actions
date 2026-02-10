"""Unit tests for determine_stacks.py"""

import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from determine_stacks import (
    classify_stacks,
    determine_stack_environment,
    expand_braces,
    files_to_dirs,
    get_core_stacks,
    is_terraform_stack,
    main,
    parse_string_list,
    separate_by_environment,
)


def run_main(
    changed_files: list[str] | str = "",
    selected_stacks: str = "",
    ignored_stacks: str = "",
    core_stacks: str = "",
    override_core_stacks: bool = False,
):
    """Helper to run the main function with test parameters."""
    # Convert list to newline-delimited string
    if isinstance(changed_files, list):
        changed_files = ",".join(changed_files)

    os.environ["CHANGED_FILES"] = changed_files
    os.environ["SELECTED_STACKS"] = selected_stacks
    os.environ["IGNORED_STACKS"] = ignored_stacks
    os.environ["CORE_STACKS"] = core_stacks
    os.environ["OVERRIDE_CORE_STACKS"] = "true" if override_core_stacks else "false"

    writer = io.StringIO()
    main(writer, Path("testdata"))

    result = {}
    for line in writer.getvalue().strip().split("\n"):
        if line.strip():
            key, value = line.split("=", 1)
            result[key] = json.loads(value)
    return result


# =============================================================================
# parse_string_list() tests
# =============================================================================


def test_parse_string_list_empty():
    """Empty and whitespace-only strings return empty list."""
    assert parse_string_list("") == []
    assert parse_string_list("   ") == []
    assert parse_string_list(None) == []


def test_parse_string_list_comma_separated():
    """Comma-separated values are parsed correctly."""
    assert parse_string_list("a") == ["a"]
    assert parse_string_list("a,b,c") == ["a", "b", "c"]
    assert parse_string_list("a, b, c") == ["a", "b", "c"]
    assert parse_string_list("  a  ,  b  ") == ["a", "b"]


def test_parse_string_list_newline_delimited():
    """Newline-delimited values are parsed correctly."""
    assert parse_string_list("a\nb\nc") == ["a", "b", "c"]
    assert parse_string_list("a\n\nb") == ["a", "b"]


def test_parse_string_list_with_braces():
    """Commas inside braces are preserved."""
    assert parse_string_list("stacks/{a,b}") == ["stacks/{a,b}"]
    assert parse_string_list("stacks/{a,b},stacks/c") == ["stacks/{a,b}", "stacks/c"]
    assert parse_string_list("stacks/{a,b},stacks/{c,d}") == ["stacks/{a,b}", "stacks/{c,d}"]


# =============================================================================
# expand_braces() tests
# =============================================================================


def test_expand_braces_no_braces():
    """Patterns without braces return unchanged."""
    assert expand_braces("stacks/dev/app") == ["stacks/dev/app"]
    assert expand_braces("stacks/*/app-*") == ["stacks/*/app-*"]


def test_expand_braces_simple():
    """Single brace group is expanded."""
    assert expand_braces("stacks/{a,b}") == ["stacks/a", "stacks/b"]
    assert expand_braces("stacks/{a,b,c}") == ["stacks/a", "stacks/b", "stacks/c"]
    assert expand_braces("{a,b}/app") == ["a/app", "b/app"]


def test_expand_braces_multiple_groups():
    """Multiple brace groups are expanded."""
    assert expand_braces("{a,b}/{c,d}") == ["a/c", "a/d", "b/c", "b/d"]
    assert expand_braces("stacks/{dev,prod}/{app,dns}") == [
        "stacks/dev/app",
        "stacks/dev/dns",
        "stacks/prod/app",
        "stacks/prod/dns",
    ]


def test_expand_braces_invalid_syntax():
    """Invalid brace syntax raises ValueError."""
    invalid_patterns = [
        "{a,b",           # unmatched open
        "a,b}",           # unmatched close
        "{a,{b,c}}",      # nested
        "stacks/{}/app",  # empty braces
        "{a,,b}",         # empty alternative
    ]
    for pattern in invalid_patterns:
        try:
            expand_braces(pattern)
            assert False, f"Expected ValueError for: {pattern}"
        except ValueError:
            pass


# =============================================================================
# determine_stack_environment() tests
# =============================================================================


def test_get_environment_mappings():
    """All environment aliases map correctly."""
    # Dev aliases
    for env in ["dev", "qa", "test"]:
        assert determine_stack_environment(f"stacks/{env}/app") == "dev", (
            f"{env} should map to dev"
        )
    # Prod aliases
    assert determine_stack_environment("stacks/prod/applications/app") == "prod"


def test_get_environment_first_match_wins():
    """First matching path component determines environment."""
    assert determine_stack_environment("stacks/dev/prod-monitor") == "dev"
    assert determine_stack_environment("stacks/prod/dev-tools") == "prod"


def test_get_environment_no_match():
    """Paths without known environment return None."""
    assert determine_stack_environment("example/my-stack") is None
    assert determine_stack_environment("stacks/shared/app") is None


def test_get_environment_case_insensitive():
    """Environment detection is case-insensitive."""
    assert determine_stack_environment("stacks/DEV/app") == "dev"
    assert determine_stack_environment("stacks/PROD/app") == "prod"


def test_get_environment_no_substring_match():
    """Only exact path components match, not substrings."""
    assert determine_stack_environment("stacks/developer-tools/app") is None
    assert determine_stack_environment("stacks/productivity/app") is None


# =============================================================================
# is_terraform_stack() tests
# =============================================================================


def test_is_terraform_stack():
    """Terraform stack detection based on backend "s3" in .tf files."""
    root = Path("testdata")
    # Valid stacks
    assert is_terraform_stack(root / "stacks/dev/app-too-tikki") is True
    assert is_terraform_stack(root / "stacks/dev/networking") is True
    # Not a stack (no .tf files with S3 backend)
    assert is_terraform_stack(root / "stacks/dev/backup/bin") is False
    # Non-existent
    assert is_terraform_stack(root / "stacks/dev/nonexistent") is False


# =============================================================================
# files_to_dirs() tests
# =============================================================================


def test_files_to_dirs_basic():
    """File paths are converted to parent directories."""
    files = ["stacks/dev/app/main.tf", "stacks/prod/dns/config.tf"]
    assert files_to_dirs(files) == ["stacks/dev/app", "stacks/prod/dns"]


def test_files_to_dirs_deduplication():
    """Duplicate directories are removed."""
    files = [
        "stacks/dev/app/main.tf",
        "stacks/dev/app/variables.tf",
        "stacks/dev/app/outputs.tf",
    ]
    assert files_to_dirs(files) == ["stacks/dev/app"]


def test_files_to_dirs_boilerplate():
    """Files in .boilerplate are mapped to parent stack directory."""
    files = ["stacks/dev/app/.boilerplate/something"]
    assert files_to_dirs(files) == ["stacks/dev/app"]


# =============================================================================
# separate_by_environment() tests
# =============================================================================


def test_separate_by_environment():
    """Directories are correctly separated by environment."""
    dirs = ["stacks/dev/app", "stacks/prod/dns", "stacks/shared/common"]
    dev, prod, skipped = separate_by_environment(dirs)
    assert dev == ["stacks/dev/app"]
    assert prod == ["stacks/prod/dns"]
    assert skipped == ["stacks/shared/common"]


# =============================================================================
# get_core_stacks() tests
# =============================================================================


def test_get_core_stacks_defaults():
    """Default core stack patterns are returned."""
    patterns = get_core_stacks()
    assert "**/networking" in patterns
    assert "**/dns" in patterns
    assert "**/iam" in patterns


def test_get_core_stacks_with_user_patterns():
    """User patterns are appended to defaults."""
    patterns = get_core_stacks(patterns=["**/custom-infra"])
    assert "**/networking" in patterns
    assert "**/custom-infra" in patterns


def test_get_core_stacks_override():
    """Override replaces defaults with user patterns."""
    patterns = get_core_stacks(
        patterns=["**/custom-only"], override_default_patterns=True
    )
    assert patterns == ["**/custom-only"]
    assert "**/networking" not in patterns


# =============================================================================
# classify_stacks() tests
# =============================================================================


def test_classify_stacks():
    """Stacks are classified into matching and non-matching."""
    paths = ["stacks/dev/networking", "stacks/dev/app", "stacks/dev/dns"]
    patterns = ["**/networking", "**/dns"]
    hit, miss = classify_stacks(paths, patterns)
    assert hit == ["stacks/dev/networking", "stacks/dev/dns"]
    assert miss == ["stacks/dev/app"]


# =============================================================================
# Integration tests
# =============================================================================


def test_mixed_stacks():
    """Mix of core and dependent stacks across environments."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/networking/main.tf",
        "stacks/dev/app-custom/main.tf",
        "stacks/dev/iam/main.tf",
        "stacks/prod/dns/main.tf",
        "stacks/prod/app-hello/main.tf",
    ]
    result = run_main(changed_files=files)

    assert result["dev-core-stacks"] == ["stacks/dev/iam", "stacks/dev/networking"]
    assert result["dev-apps-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
    ]
    assert result["prod-core-stacks"] == ["stacks/prod/dns"]
    assert result["prod-apps-stacks"] == ["stacks/prod/app-hello"]
    assert result["all-dev-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
        "stacks/dev/iam",
        "stacks/dev/networking",
    ]
    assert result["all-prod-stacks"] == ["stacks/prod/app-hello", "stacks/prod/dns"]
    assert result["all-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
        "stacks/dev/iam",
        "stacks/dev/networking",
        "stacks/prod/app-hello",
        "stacks/prod/dns",
    ]


def test_glob_pattern():
    """Selection with glob pattern."""
    result = run_main(selected_stacks="stacks/*/app-*")

    assert result["dev-apps-stacks"] == [
        "stacks/dev/app-custom",
        "stacks/dev/app-too-tikki",
    ]
    assert result["prod-apps-stacks"] == [
        "stacks/prod/app-hello",
        "stacks/prod/app-too-tikki",
    ]
    assert result["dev-core-stacks"] == []
    assert result["prod-core-stacks"] == []


def test_glob_pattern_with_braces():
    """Selection with brace expansion pattern."""
    result = run_main(selected_stacks="stacks/prod/{app-hello,dns}")

    assert result["prod-apps-stacks"] == ["stacks/prod/app-hello"]
    assert result["prod-core-stacks"] == ["stacks/prod/dns"]
    assert result["dev-apps-stacks"] == []
    assert result["dev-core-stacks"] == []


def test_non_matching_glob_pattern():
    """Non-matching glob pattern returns empty arrays."""
    result = run_main(selected_stacks="stacks/doesnotexist/*")

    assert result["dev-core-stacks"] == []
    assert result["dev-apps-stacks"] == []
    assert result["prod-core-stacks"] == []
    assert result["prod-apps-stacks"] == []
    assert result["all-stacks"] == []


def test_filters_non_terraform_directories():
    """Non-Terraform directories are filtered out."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/backup/bin/script.sh",
    ]
    result = run_main(changed_files=files)

    assert result["dev-apps-stacks"] == ["stacks/dev/app-too-tikki"]
    assert result["all-stacks"] == ["stacks/dev/app-too-tikki"]


def test_ignored_stacks():
    """Ignored stack patterns filter out matching stacks."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/networking/main.tf",
        "stacks/prod/app-hello/main.tf",
    ]
    result = run_main(changed_files=files, ignored_stacks="**/app-*")

    # app-* stacks should be filtered out
    assert result["dev-core-stacks"] == ["stacks/dev/networking"]
    assert result["dev-apps-stacks"] == []
    assert result["prod-core-stacks"] == []
    assert result["prod-apps-stacks"] == []


def test_custom_core_stacks():
    """Custom core stack patterns work alongside defaults."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/networking/main.tf",
    ]
    # Add app-too-tikki as a core stack
    result = run_main(changed_files=files, core_stacks="**/app-too-tikki")

    assert result["dev-core-stacks"] == [
        "stacks/dev/app-too-tikki",
        "stacks/dev/networking",
    ]
    assert result["dev-apps-stacks"] == []


def test_override_core_stacks():
    """Override core stacks replaces default patterns."""
    files = [
        "stacks/dev/app-too-tikki/main.tf",
        "stacks/dev/networking/main.tf",
    ]
    # Only app-* should be considered core now
    result = run_main(
        changed_files=files, core_stacks="**/app-*", override_core_stacks=True
    )

    assert result["dev-core-stacks"] == ["stacks/dev/app-too-tikki"]
    assert result["dev-apps-stacks"] == ["stacks/dev/networking"]


def test_no_changes():
    """No changed files results in empty outputs."""
    result = run_main(changed_files=[])

    assert result["dev-core-stacks"] == []
    assert result["dev-apps-stacks"] == []
    assert result["prod-core-stacks"] == []
    assert result["prod-apps-stacks"] == []
    assert result["all-stacks"] == []
