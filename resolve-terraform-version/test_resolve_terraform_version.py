from datetime import datetime, timedelta, timezone

import pytest
import resolve_terraform_version
from resolve_terraform_version import (
    Release,
    ResolveError,
    Version,
    candidate_versions,
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

    Every lookup is injectable, so a test that hits the network has forgotten to inject
    one — which previously went unnoticed, with assertions quietly depending on whatever
    HashiCorp had released that week.
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


class TestCandidateVersions:
    AVAILABLE = ["1.9.8", "1.10.0", "1.10.5", "1.15.8", "1.15.9", "1.16.0-beta1", "2.0.0"]

    def test_newest_first(self):
        result = candidate_versions(">= 1.10.0", self.AVAILABLE)
        assert [str(v) for v in result] == ["2.0.0", "1.15.9", "1.15.8", "1.10.5", "1.10.0"]

    def test_prereleases_excluded_by_default(self):
        assert "1.16.0-beta1" not in [str(v) for v in candidate_versions(">= 1.10.0", self.AVAILABLE)]

    def test_prereleases_included_when_the_constraint_names_one(self):
        result = candidate_versions("= 1.16.0-beta1", self.AVAILABLE)
        assert [str(v) for v in result] == ["1.16.0-beta1"]

    def test_unparseable_entries_in_the_index_are_ignored(self):
        result = candidate_versions(">= 1.10.0", self.AVAILABLE + ["not-a-version"])
        assert "not-a-version" not in [str(v) for v in result]


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
        # Regression test for the old `grep required_version *.tf | sed` approach, which
        # took whichever match came first alphabetically and so ignored the override.
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
        # Deliberately not an error: an empty `terraform_version` is what the previous
        # implementation produced, and npm semver resolves it to the newest release.
        # Turning that into a failure would break every stack without a
        # `required_version`, so it is tracked as its own change.
        write_stack(tmp_path, {"main.tf": 'terraform {\n}\n'})
        assert find_constraint(tmp_path) == ("", [])

    def test_no_tf_files_yields_an_empty_constraint(self, tmp_path):
        assert find_constraint(tmp_path) == ("", [])


class TestResolveVersion:
    """Resolution, with every lookup injected.

    `AVAILABLE` stands in for the full index (every version, no dates). `RECENT` stands in
    for the recent-releases window, which carries dates inline — the newest few versions
    only, exactly as the real endpoint behaves.
    """

    AVAILABLE = ["1.9.8", "1.10.0", "1.11.4", "1.15.6", "1.15.7", "1.15.8", "1.15.9"]

    DATES = {
        "1.9.8": NOW - timedelta(days=800),
        "1.10.0": NOW - timedelta(days=400),
        "1.11.4": NOW - timedelta(days=300),
        "1.15.6": NOW - timedelta(days=90),
        "1.15.7": NOW - timedelta(days=70),
        "1.15.8": NOW - timedelta(days=43),
        "1.15.9": NOW - timedelta(days=1),
    }

    RECENT = ["1.15.8", "1.15.9"]

    def resolve(self, constraint, cooldown_days=7, available=None, dates=None, recent=None):
        dates = dates if dates is not None else self.DATES
        available = available if available is not None else self.AVAILABLE
        recent = recent if recent is not None else self.RECENT

        self.probed = []
        self.calls = []

        def get_recent():
            self.calls.append("recent")
            return [Release(Version.parse(v), dates[v]) for v in recent]

        def get_versions():
            self.calls.append("index")
            return list(available)

        def get_release_date(version):
            self.calls.append(f"date:{version}")
            self.probed.append(version)
            return dates[version]

        return resolve_version(
            constraint,
            cooldown_days,
            now=NOW,
            get_recent=get_recent,
            get_versions=get_versions,
            get_release_date=get_release_date,
        )

    def test_skips_a_version_inside_the_cooldown(self):
        # This is the issue: today `>= 1.10.0` installs 1.15.9, released yesterday.
        assert self.resolve(">= 1.10.0") == "1.15.8"

    def test_common_case_costs_one_request(self):
        # The recent window carries dates inline, so neither the full index nor any
        # per-version date lookup is needed.
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
        assert self.probed == []

    def test_falls_back_to_the_index_when_the_window_holds_no_match(self):
        # `< 1.15.0` predates the window entirely, so the full index is unavoidable.
        assert self.resolve("< 1.15.0") == "1.11.4"
        assert self.calls[:2] == ["recent", "index"]

    def test_falls_back_to_the_index_when_the_whole_window_is_too_fresh(self):
        assert self.resolve(">= 1.10.0", cooldown_days=60) == "1.15.7"
        assert "index" in self.calls

    def test_dates_already_known_are_not_fetched_again(self):
        # 1.15.8 and 1.15.9 came from the window; only older candidates cost a request.
        self.resolve(">= 1.10.0", cooldown_days=60)
        assert "1.15.9" not in self.probed
        assert "1.15.8" not in self.probed
        assert self.probed == ["1.15.7"]

    def test_falls_back_to_newest_when_nothing_is_old_enough(self):
        dates = {v: NOW - timedelta(days=1) for v in self.AVAILABLE}
        assert self.resolve(">= 1.10.0", dates=dates) == "1.15.9"

    def test_longer_cooldown_reaches_further_back(self):
        # 1.15.7 is 70 days old, 1.15.6 is 90, so an 80-day cooldown lands on 1.15.6.
        assert self.resolve(">= 1.10.0", cooldown_days=80) == "1.15.6"
        assert self.resolve(">= 1.10.0", cooldown_days=120) == "1.11.4"

    def test_pessimistic_and_range_resolve_alike_below_terraform_2(self):
        assert self.resolve("~> 1.10") == self.resolve(">= 1.10.0") == "1.15.8"

    def test_no_matching_version_raises(self):
        with pytest.raises(ResolveError, match="No released Terraform version"):
            self.resolve(">= 99.0.0")
