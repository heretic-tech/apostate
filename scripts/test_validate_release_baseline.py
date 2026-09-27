"""Focused checks for scripts/validate-release-baseline.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("validate-release-baseline.py")


def _contents_hash(root: Path, names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0")
        digest.update((root / "patches" / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _make_fixture() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "build").mkdir()
    (root / "patches").mkdir()
    names = ["0001-first.patch", "0002-second.patch"]
    for name, body in zip(names, [b"first\n", b"second\n"]):
        (root / "patches" / name).write_bytes(body)
    (root / "patches" / "series").write_text("\n".join(names) + "\n")
    (root / "build" / "CHROMIUM_VERSION").write_text("152.0.7977.83\n")
    series_hash = hashlib.sha256((root / "patches" / "series").read_bytes()).hexdigest()
    contents_hash = _contents_hash(root, names)
    (root / "build" / "MANIFEST.lock").write_text(
        '# Generated fixture\n'
        'chromium_version     = "152.0.7977.83"\n'
        f'patch_series_sha256  = "{series_hash}"\n'
        f'patch_contents_sha256 = "{contents_hash}"\n'
        "\n[outputs]\n"
    )
    return temporary, root


class ValidateReleaseBaselineTests(unittest.TestCase):
    def run_validator(self, root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), *extra],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_valid_fixture_passes(self):
        temporary, root = _make_fixture()
        with temporary:
            result = self.run_validator(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release baseline valid", result.stdout)

    def test_a_moved_patch_set_is_a_notice_by_default_and_fatal_for_a_release(self):
        """MANIFEST.lock is a build record, so it is stale between builds.

        Builds are checkpoints rather than a per-commit step, so every patch
        edit moves these two digests and leaves them moved until the next
        build. Failing the everyday run on that makes a permanently red gate
        that nobody reads, which is how two red jobs went unnoticed. The
        release gate is where it has to be fatal, because that is the point
        at which the recorded output hashes are about to be published as
        evidence for the patch set being tagged.
        """
        temporary, root = _make_fixture()
        with temporary:
            (root / "patches" / "0001-first.patch").write_bytes(b"changed\n")
            default = self.run_validator(root)
            release = self.run_validator(root, "--release")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertIn("earlier patch set", default.stdout)
        self.assertIn("patch_contents_sha256", default.stdout)
        self.assertNotEqual(release.returncode, 0)
        self.assertIn("earlier patch set", release.stderr)
        self.assertIn("--refresh", release.stderr)

    def test_refresh_moves_both_digests_and_nothing_else(self):
        """The refresh needs no build, so the release gate has a one-command fix.

        Both digests are functions of the patch files, so recomputing them is
        repository arithmetic. Every other field in the file is evidence from
        a build and has to survive untouched, which is the property that
        keeps a digest-only refresh honest about what it is claiming.
        """
        temporary, root = _make_fixture()
        with temporary:
            manifest = root / "build" / "MANIFEST.lock"
            before = manifest.read_text()
            (root / "patches" / "0001-first.patch").write_bytes(b"changed\n")
            first = self.run_validator(root, "--refresh")
            after = manifest.read_text()
            second = self.run_validator(root, "--refresh")
            checked = self.run_validator(root, "--release")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("refreshed patch_contents_sha256", first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already records", second.stdout)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        moved = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
        self.assertEqual(
            ["patch_contents_sha256"],
            [a.split("=", 1)[0].strip() for a, _ in moved])

    def test_refresh_refuses_to_bless_a_broken_series(self):
        temporary, root = _make_fixture()
        with temporary:
            manifest = root / "build" / "MANIFEST.lock"
            before = manifest.read_text()
            series = root / "patches" / "series"
            series.write_text("0002-second.patch\n0001-first.patch\n")
            result = self.run_validator(root, "--refresh")
            self.assertEqual(before, manifest.read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing to refresh", result.stderr)

    def test_a_truncated_digest_is_a_defect_rather_than_drift(self):
        temporary, root = _make_fixture()
        with temporary:
            manifest = root / "build" / "MANIFEST.lock"
            manifest.write_text(manifest.read_text().replace(
                'patch_series_sha256  = "', 'patch_series_sha256  = "nothex'))
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid patch_series_sha256", result.stderr)

    def test_missing_patch_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            (root / "patches" / "0002-second.patch").unlink()
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing patch 0002-second.patch", result.stderr)

    def test_duplicate_patch_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text(series.read_text() + "0001-first.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references 0001-first.patch 2 times", result.stderr)

    def test_version_drift_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            (root / "build" / "CHROMIUM_VERSION").write_text("152.0.7977.84\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chromium version drift", result.stderr)

    def test_an_undocumented_reorder_fails_and_a_documented_one_passes(self):
        """The series is dependency-ordered, and that is what has to survive.

        Numeric order is not the rule -- 0084 and 0085 follow 0096 in the
        real series -- so the check is that a departure from it was written
        down. An unexplained swap is indistinguishable from a bad
        merge; an explained one is the author's declared apply order.
        """
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text("0002-second.patch\n0001-first.patch\n")
            silent = self.run_validator(root)
            series.write_text(
                "# 0001 rewrites the branch 0002 reaches, so it follows it.\n"
                "0002-second.patch\n0001-first.patch\n")
            documented = self.run_validator(root)
        self.assertNotEqual(silent.returncode, 0)
        self.assertIn("out of numeric order", silent.stderr)
        self.assertIn("nothing says why", silent.stderr)
        self.assertEqual(documented.returncode, 0, documented.stderr)

    def test_a_comment_separated_from_the_entries_does_not_exempt_them(self):
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text(
                "# Apostate patch series.\n\n0002-second.patch\n0001-first.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("out of numeric order", result.stderr)

    def test_a_blank_line_alone_does_not_exempt_an_entry(self):
        """A run of its own is not an explanation.

        Removing the comment above a documented group leaves the group on
        its own between blank lines. That has to fail like any other
        unexplained reorder, so the comparison carries across runs.
        """
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text("0002-second.patch\n\n0001-first.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("0001-first.patch is out of numeric order after "
                      "0002-second.patch", result.stderr)

    def test_entries_around_a_documented_group_are_still_compared(self):
        temporary, root = _make_fixture()
        with temporary:
            patches = root / "patches"
            (patches / "0003-third.patch").write_bytes(b"third\n")
            series = patches / "series"
            series.write_text(
                "0001-first.patch\n\n"
                "# 0003 is placed early on purpose.\n"
                "0003-third.patch\n\n"
                "0002-second.patch\n")
            documented = self.run_validator(root, "--series-only")
            series.write_text(
                "0002-second.patch\n\n"
                "# 0003 is placed early on purpose.\n"
                "0003-third.patch\n\n"
                "0001-first.patch\n")
            shuffled = self.run_validator(root, "--series-only")
        self.assertEqual(documented.returncode, 0, documented.stderr)
        self.assertNotEqual(shuffled.returncode, 0)
        self.assertIn("0001-first.patch is out of numeric order after "
                      "0002-second.patch", shuffled.stderr)

    def test_a_patch_cannot_be_listed_before_the_patch_that_creates_its_file(self):
        """The one dependency edge the diffs state outright.

        No patch declares its dependencies, so a general dependency-order
        check is not available from the repository alone. Creation is the
        exception: a diff against /dev/null says which patch brings a file
        into existence, and nothing can edit that file earlier.
        """
        temporary, root = _make_fixture()
        with temporary:
            patches = root / "patches"
            (patches / "0003-create.patch").write_text(
                "--- /dev/null\n+++ b/base/apostate/added.cc\n"
                "@@ -0,0 +1 @@\n+int value = 1;\n")
            (patches / "0004-edit.patch").write_text(
                "--- a/base/apostate/added.cc\n+++ b/base/apostate/added.cc\n"
                "@@ -1 +1 @@\n-int value = 1;\n+int value = 2;\n")
            series = root / "patches" / "series"
            series.write_text(
                "0001-first.patch\n0002-second.patch\n\n"
                "# Deliberately reordered, and wrongly so.\n"
                "0004-edit.patch\n0003-create.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("before 0003-create.patch, which creates "
                      "base/apostate/added.cc", result.stderr)

    def test_a_reused_number_and_an_unnumbered_entry_fail(self):
        temporary, root = _make_fixture()
        with temporary:
            patches = root / "patches"
            (patches / "0002-another.patch").write_bytes(b"another\n")
            (patches / "hotfix.patch").write_bytes(b"hotfix\n")
            series = patches / "series"
            series.write_text(series.read_text() + "0002-another.patch\nhotfix.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses number 0002 twice", result.stderr)
        self.assertIn("'hotfix.patch' is not a numbered patch", result.stderr)


if __name__ == "__main__":
    unittest.main()
