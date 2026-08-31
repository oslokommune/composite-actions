from datetime import datetime, timedelta, timezone

import pytest
import resolve_terraform_version
from resolve_terraform_version import (
    Release,
    ResolveError,
    Version,
    _matching,
    fetch_recent_releases,
    find_constraint,
    parse_constraint,
    resolve_version,
    satisfies,
    strip_comments,
)

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test reaches the real release API.

    Every lookup is injectable. A test that hits the network has forgotten to inject
    one, and its assertions would depend on whatever HashiCorp released that week.
    """

    def blocked(url, *args, **kwargs):
        raise AssertionError(f"test tried to reach the network: {url}")

    monkeypatch.setattr(resolve_terraform_version.urllib.request, "urlopen", blocked)


def matches(constraint: str, version: str) -> bool:
    return satisfies(Version.parse(version), parse_constraint(constraint))


def write_stack(tmp_path, files: dict[str, str]):
    for name, content in files.items():
        (tmp_path / name).write_text(content)
    return tmp_path


def terraform_block(constraint: str) -> str:
    return 'terraform {\n  required_version = "%s"\n}\n' % constraint


class TestDocumentedExamples:
    """The examples HashiCorp documents for version constraints.

    https://developer.hashicorp.com/terraform/language/expressions/version-constraints
    """

    @pytest.mark.parametrize(
        "constraint,version,expected",
        [
            (">= 1.2.0", "1.2.0", True),
            (">= 1.2.0", "1.2.1", True),
            (">= 1.2.0", "1.1.9", False),
            ("<= 1.2.0", "1.2.0", True),
            ("<= 1.2.0", "1.1.0", True),
            ("<= 1.2.0", "1.2.1", False),
            # `~> 1.0.4` allows patch bumps only.
            ("~> 1.0.4", "1.0.5", True),
            ("~> 1.0.4", "1.0.10", True),
            ("~> 1.0.4", "1.1.0", False),
            ("~> 1.0.4", "1.0.3", False),
            # `~> 1.1` allows minor bumps, because only two segments were written.
            ("~> 1.1", "1.2.0", True),
            ("~> 1.1", "1.10.0", True),
            ("~> 1.1", "2.0.0", False),
            ("~> 1.1", "1.0.9", False),
            (">= 1.0.0, < 2.0.0", "1.9.9", True),
            (">= 1.0.0, < 2.0.0", "2.0.0", False),
        ],
    )
    def test_matches(self, constraint, version, expected):
        assert matches(constraint, version) is expected


class TestDivergenceFromNpmSemver:
    """Cases where `setup-terraform`'s npm semver disagrees with Terraform."""

    def test_two_segment_pessimistic_allows_minor_bumps(self):
        # npm reads `~> 1.10` as >=1.10.0 <1.11.0 and would install 1.10.5.
        assert matches("~> 1.10", "1.15.8")
        assert matches("~> 1.10", "1.99.0")
        assert not matches("~> 1.10", "2.0.0")

    def test_single_segment_pessimistic_allows_major_bumps(self):
        # Only one segment written, so nothing is pinned above the major floor.
        assert matches("~> 1", "2.0.0")
        assert matches("~> 1", "1.0.0")
        assert not matches("~> 1", "0.15.0")

    def test_comma_keeps_the_lower_bound(self):
        # npm silently drops the lower bound of `>= 6.0.0, < 7.0.0`.
        assert matches(">= 6.0.0, < 7.0.0", "6.5.0")
        assert not matches(">= 6.0.0, < 7.0.0", "5.9.0")
        assert not matches(">= 6.0.0, < 7.0.0", "7.0.0")


class TestOperators:
    @pytest.mark.parametrize(
        "constraint,version,expected",
        [
            ("1.4.6", "1.4.6", True),
            ("1.4.6", "1.4.7", False),
            ("= 1.4.6", "1.4.6", True),
            ("!= 1.4.6", "1.4.7", True),
            ("!= 1.4.6", "1.4.6", False),
            ("> 1.2.0", "1.2.1", True),
            ("> 1.2.0", "1.2.0", False),
            ("< 1.2.0", "1.1.9", True),
            ("< 1.2.0", "1.2.0", False),
            # No whitespace, and a `v` prefix, both accepted by go-version.
            (">=1.10.0", "1.15.0", True),
            (">= v1.10.0", "1.15.0", True),
        ],
    )
    def test_matches(self, constraint, version, expected):
        assert matches(constraint, version) is expected

    def test_short_versions_compare_as_padded(self):
        assert matches("= 1.2", "1.2.0")
        assert matches(">= 1.2", "1.2.0")

    def test_malformed_constraint_is_rejected(self):
        with pytest.raises(ValueError):
            parse_constraint(">= not-a-version")

    def test_empty_term_is_rejected(self):
        with pytest.raises(ValueError):
            parse_constraint(">= 1.0.0,")


class TestPrereleaseOrdering:
    def test_release_outranks_its_own_prerelease(self):
        assert Version.parse("1.16.0") > Version.parse("1.16.0-beta1")

    def test_numeric_prerelease_identifiers_compare_numerically(self):
        assert Version.parse("1.16.0-rc.2") > Version.parse("1.16.0-rc.1")
        assert Version.parse("1.16.0-rc.10") > Version.parse("1.16.0-rc.9")

    def test_pessimistic_constraint_rejects_prereleases(self):
        assert not matches("~> 1.15", "1.16.0-beta1")


class TestMatching:
    AVAILABLE = ["1.9.8", "1.10.0", "1.10.5", "1.15.8", "1.15.9", "1.16.0-beta1", "2.0.0"]

    def matching(self, constraint: str) -> list[str]:
        versions = [Version.parse(v) for v in self.AVAILABLE]
        return [str(v) for v in _matching(versions, parse_constraint(constraint))]

    def test_newest_first(self):
        assert self.matching(">= 1.10.0") == ["2.0.0", "1.15.9", "1.15.8", "1.10.5", "1.10.0"]

    def test_prereleases_excluded_by_default(self):
        assert "1.16.0-beta1" not in self.matching(">= 1.10.0")

    def test_prereleases_included_when_the_constraint_names_one(self):
        assert self.matching("= 1.16.0-beta1") == ["1.16.0-beta1"]


class TestFetchRecentReleases:
    def test_unparseable_entries_are_ignored(self, monkeypatch):
        # The release feed has carried a few historical oddities; they can never match.
        entries = [
            {"version": "1.15.9", "timestamp_created": "2026-08-19T00:00:00+00:00"},
            {"version": "not-a-version", "timestamp_created": "2026-08-19T00:00:00+00:00"},
        ]
        monkeypatch.setattr(resolve_terraform_version, "fetch_json", lambda url: entries)
        assert [str(r.version) for r in fetch_recent_releases()] == ["1.15.9"]


class TestStripComments:
    def test_hash_comment_is_removed(self):
        assert 'required_version' not in strip_comments('# required_version = "1.0.0"\n')

    def test_double_slash_comment_is_removed(self):
        assert 'required_version' not in strip_comments('// required_version = "1.0.0"\n')

    def test_block_comment_is_removed(self):
        assert 'required_version' not in strip_comments('/*\nrequired_version = "1.0.0"\n*/\n')

    def test_hash_inside_a_string_survives(self):
        assert strip_comments('key = "a#b"\n') == 'key = "a#b"\n'

    def test_escaped_quote_does_not_end_the_string(self):
        assert strip_comments('key = "a\\"#b"\n') == 'key = "a\\"#b"\n'


class TestFindConstraint:
    def test_single_file(self, tmp_path):
        write_stack(tmp_path, {"main.tf": terraform_block(">= 1.10.0")})
        assert find_constraint(tmp_path) == (">= 1.10.0", ["main.tf"])

    def test_base_files_are_anded(self, tmp_path):
        write_stack(
            tmp_path,
            {
                "a.tf": terraform_block(">= 1.10.0"),
                "b.tf": terraform_block("< 2.0.0"),
            },
        )
        constraint, sources = find_constraint(tmp_path)
        assert constraint == ">= 1.10.0, < 2.0.0"
        assert sources == ["a.tf", "b.tf"]

    def test_override_replaces_base_constraints(self, tmp_path):
        write_stack(
            tmp_path,
            {
                "__gp_versions.tf": terraform_block(">= 1.10.0"),
                "__gp_versions_override.tf": terraform_block("= 1.12.2"),
            },
        )
        assert find_constraint(tmp_path) == ("= 1.12.2", ["__gp_versions_override.tf"])

    def test_bare_override_file_is_recognised(self, tmp_path):
        write_stack(
            tmp_path,
            {"main.tf": terraform_block(">= 1.10.0"), "override.tf": terraform_block("= 1.12.2")},
        )
        assert find_constraint(tmp_path) == ("= 1.12.2", ["override.tf"])

    def test_last_override_alphabetically_wins(self, tmp_path):
        write_stack(
            tmp_path,
            {
                "main.tf": terraform_block(">= 1.10.0"),
                "a_override.tf": terraform_block("= 1.11.0"),
                "b_override.tf": terraform_block("= 1.12.0"),
            },
        )
        assert find_constraint(tmp_path) == ("= 1.12.0", ["b_override.tf"])

    def test_override_without_a_constraint_is_ignored(self, tmp_path):
        write_stack(
            tmp_path,
            {"main.tf": terraform_block(">= 1.10.0"), "x_override.tf": 'terraform {\n}\n'},
        )
        assert find_constraint(tmp_path) == (">= 1.10.0", ["main.tf"])

    def test_commented_out_constraint_is_not_used(self, tmp_path):
        write_stack(
            tmp_path,
            {"main.tf": '# required_version = "= 1.0.0"\n' + terraform_block(">= 1.10.0")},
        )
        assert find_constraint(tmp_path) == (">= 1.10.0", ["main.tf"])

    def test_missing_constraint_yields_an_empty_constraint(self, tmp_path):
        # Deliberately not an error; see the comment in main().
        write_stack(tmp_path, {"main.tf": 'terraform {\n}\n'})
        assert find_constraint(tmp_path) == ("", [])

    def test_no_tf_files_yields_an_empty_constraint(self, tmp_path):
        assert find_constraint(tmp_path) == ("", [])


class TestResolveVersion:
    """Resolution, with the one lookup injected.

    `RECENT` stands in for the recent-releases window, which carries dates inline — the
    newest few versions only, exactly as the real endpoint behaves.
    """

    DATES = {
        "1.15.6": NOW - timedelta(days=90),
        "1.15.7": NOW - timedelta(days=70),
        "1.15.8": NOW - timedelta(days=43),
        "1.15.9": NOW - timedelta(days=1),
    }

    RECENT = ["1.15.6", "1.15.7", "1.15.8", "1.15.9"]

    def resolve(self, constraint, cooldown_days=7, dates=None, recent=None):
        dates = dates if dates is not None else self.DATES
        recent = recent if recent is not None else self.RECENT

        self.calls = []

        def get_recent():
            self.calls.append("recent")
            return [Release(Version.parse(v), dates[v]) for v in recent]

        return resolve_version(constraint, cooldown_days, now=NOW, get_recent=get_recent)

    def test_skips_a_version_inside_the_cooldown(self):
        # 1.15.9 is the newest match but was released yesterday, inside the 7-day cooldown.
        assert self.resolve(">= 1.10.0") == "1.15.8"

    def test_costs_at_most_one_request(self):
        # The recent window carries dates inline, so one request answers everything.
        self.resolve(">= 1.10.0")
        assert self.calls == ["recent"]

    def test_exact_pin_costs_no_requests(self):
        # Nothing to resolve, and a human already chose it, so no cooldown check either.
        assert self.resolve("= 1.15.9") == "1.15.9"
        assert self.calls == []

    def test_bare_exact_pin_costs_no_requests(self):
        assert self.resolve("1.12.2") == "1.12.2"
        assert self.calls == []

    def test_cooldown_of_zero_takes_the_newest(self):
        assert self.resolve(">= 1.10.0", cooldown_days=0) == "1.15.9"

    def test_no_match_in_the_window_raises(self):
        # `< 1.15.0` predates the window entirely. Every version it matches is old, so
        # the cooldown adds nothing; the caller fails open to the raw constraint, which
        # setup-terraform resolves to the newest match itself.
        with pytest.raises(ResolveError, match="most recent releases"):
            self.resolve("< 1.15.0")

    def test_falls_back_to_newest_when_nothing_is_old_enough(self):
        dates = {v: NOW - timedelta(days=1) for v in self.RECENT}
        assert self.resolve(">= 1.10.0", dates=dates) == "1.15.9"

    def test_longer_cooldown_reaches_further_back_in_the_window(self):
        # 1.15.7 is 70 days old, 1.15.6 is 90, so an 80-day cooldown lands on 1.15.6.
        assert self.resolve(">= 1.10.0", cooldown_days=80) == "1.15.6"

    def test_cooldown_longer_than_the_whole_window_falls_back_to_newest(self):
        # Matches older than the window may exist, but only the window is consulted.
        assert self.resolve(">= 1.10.0", cooldown_days=365) == "1.15.9"

    def test_pessimistic_and_range_resolve_alike_below_terraform_2(self):
        assert self.resolve("~> 1.10") == self.resolve(">= 1.10.0") == "1.15.8"

    def test_no_matching_version_raises(self):
        with pytest.raises(ResolveError, match="most recent releases"):
            self.resolve(">= 99.0.0")
