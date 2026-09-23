"""Coverage for how Olisar spells and orders its release versions.

Run:  uv run python -m unittest tests.test_versioning -v

From 2.0 on a stable release has two numbers and a beta carries the number of the release
it leads up to (2.0.beta-1, 2.0.beta-2, then 2.0), while the version files keep the semver
spelling npm needs (2.0.0-beta.1). Every one of those spellings reaches the comparison: the
app reports the semver one, tags and image labels carry the display one, and uv.lock holds
PEP 440's. The ordering is what decides whether an install moves, so the cases where a
wrong answer would move one the wrong way are pinned here.
"""

from __future__ import annotations

import unittest

from olisar.versioning import display, is_beta, is_newer, parse, same_version


class ParseTests(unittest.TestCase):
    def test_every_spelling_of_a_beta_is_the_same_release(self) -> None:
        for spelling in ("v2.0.beta-1", "2.0.beta-1", "2.0.0-beta.1", "2.0.0b1", "V2.0.BETA-1"):
            with self.subTest(spelling=spelling):
                self.assertEqual(parse(spelling), (2, 0, 0, 1))

    def test_a_stable_release_has_no_beta(self) -> None:
        self.assertEqual(parse("v2.0"), (2, 0, 0, None))
        self.assertEqual(parse("2.0.0"), (2, 0, 0, None))

    def test_pre_2_0_releases_still_parse(self) -> None:
        self.assertEqual(parse("v1.4.5"), (1, 4, 5, None))

    def test_not_a_version(self) -> None:
        for junk in ("", None, "main", "latest", "v2", "2.0.alpha-1", "2.0.beta-"):
            with self.subTest(junk=junk):
                self.assertIsNone(parse(junk))


class SpellingTests(unittest.TestCase):
    def test_display_drops_the_third_number_from_2_0_on(self) -> None:
        self.assertEqual(display("2.0.0"), "2.0")
        self.assertEqual(display("2.0.0-beta.3"), "2.0.beta-3")
        self.assertEqual(display("v2.1"), "2.1")

    def test_display_keeps_the_third_number_before_2_0(self) -> None:
        """Those releases were tagged v1.5.0, so that's what they're called."""
        self.assertEqual(display("1.5.0"), "1.5.0")
        self.assertEqual(display("v1.4.5"), "1.4.5")

    def test_tag_and_package_forms(self) -> None:
        beta = parse("2.0.beta-1")
        self.assertEqual(beta.tag(), "v2.0.beta-1")
        self.assertEqual(beta.package(), "2.0.0-beta.1")
        self.assertEqual(parse("v2.0").package(), "2.0.0")

    def test_display_passes_an_unparseable_label_through(self) -> None:
        self.assertEqual(display("vmain"), "main")
        self.assertEqual(display(""), "")


class OrderTests(unittest.TestCase):
    def test_a_beta_sorts_below_its_own_release(self) -> None:
        """Beta testers land on 2.0 when it ships, and a stable install never sees 2.0.beta-9
        as an upgrade over 2.0."""
        self.assertTrue(is_newer("v2.0", "2.0.0-beta.9"))
        self.assertFalse(is_newer("v2.0.beta-9", "2.0.0"))

    def test_betas_count_up(self) -> None:
        self.assertTrue(is_newer("v2.0.beta-2", "2.0.0-beta.1"))
        self.assertTrue(is_newer("v2.0.beta-10", "2.0.0-beta.9"))

    def test_the_first_beta_is_newer_than_every_1_x(self) -> None:
        self.assertTrue(is_newer("v2.0.beta-1", "1.5.0"))
        self.assertTrue(is_newer("v2.0.beta-1", "1.99.99"))

    def test_the_next_beta_is_newer_than_the_last_stable(self) -> None:
        self.assertTrue(is_newer("v2.1.beta-1", "2.0.0"))

    def test_two_and_three_number_spellings_are_the_same_release(self) -> None:
        """The tag says v2.0, the app says 2.0.0: nothing to update."""
        self.assertTrue(same_version("v2.0", "2.0.0"))
        self.assertFalse(is_newer("v2.0", "2.0.0"))
        self.assertTrue(same_version("v2.0.beta-1", "2.0.0-beta.1"))

    def test_an_unparseable_string_still_compares(self) -> None:
        """A `main` image label or the 0.0.0 sentinel must not raise or look newer."""
        self.assertFalse(is_newer("main", "2.0.0"))
        self.assertTrue(is_newer("v2.0", "0.0.0"))

    def test_is_beta(self) -> None:
        self.assertTrue(is_beta("2.0.0-beta.1"))
        self.assertFalse(is_beta("v2.0"))
        self.assertFalse(is_beta("main"))


if __name__ == "__main__":
    unittest.main()
