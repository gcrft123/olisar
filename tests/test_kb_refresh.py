"""Coverage for knowledge sources that re-read themselves on a schedule.

Run:  uv run python -m unittest tests.test_kb_refresh -v

Two rules carry this feature, and both are pure functions so they can be exercised without a
schema or a network:

``refresh_action`` decides whether a source is queued. The hazards are that SQLite has no
timezone type — a ``DateTime(timezone=True)`` column reads back *naive*, and comparing that to
an aware ``utcnow()`` raises TypeError — and that a source claimed by a worker that then died
stays claimed forever, silently ending its schedule.

``plan_chunk_sync`` decides what a re-read costs. A passage's row id is its rowid in the
vector table, so a row that survives keeps its embedding. Matching by position instead of by
text would mark the entire tail of a page as new the first time someone inserted a paragraph,
and re-embed a whole site on a timer against a free-tier quota.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from olisar.db.models import KBStatus
from olisar.knowledge.ingest import plan_chunk_sync
from olisar.knowledge.refresh import STALE_CLAIM_HOURS, next_run_at, refresh_action

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def action(**over) -> str | None:
    """A settled, scheduled, not-yet-due source — override one field per case."""
    kwargs = {
        "status": KBStatus.ready,
        "interval_hours": 24,
        "next_refresh_at": NOW + timedelta(hours=1),
        "last_checked_at": NOW - timedelta(hours=23),
        "now": NOW,
    }
    kwargs.update(over)
    return refresh_action(**kwargs)


def rec(text: str, url: str | None = None, title: str | None = None) -> dict:
    return {"text": text, "url": url, "title": title}


class RefreshActionTests(unittest.TestCase):
    def test_unscheduled_source_is_never_queued(self) -> None:
        for status in KBStatus:
            with self.subTest(status=status):
                self.assertIsNone(
                    action(status=status, interval_hours=0, next_refresh_at=None)
                )

    def test_due_source_is_queued(self) -> None:
        self.assertEqual(action(next_refresh_at=NOW - timedelta(minutes=1)), "due")

    def test_source_due_exactly_now_is_queued(self) -> None:
        self.assertEqual(action(next_refresh_at=NOW), "due")

    def test_source_not_yet_due_is_left_alone(self) -> None:
        self.assertIsNone(action(next_refresh_at=NOW + timedelta(minutes=1)))

    def test_scheduled_source_with_no_stamp_is_queued(self) -> None:
        # A schedule written without a next-run stamp (an older row, or one set straight in
        # the database) must not sit scheduled-but-never-due.
        self.assertIsNone(action(next_refresh_at=None, interval_hours=0))
        self.assertEqual(action(next_refresh_at=None), "due")

    def test_failed_source_keeps_retrying_on_its_schedule(self) -> None:
        # A source that 404s should come back round, not fall out of the rotation — and it
        # must not retry faster than its interval either.
        self.assertEqual(
            action(status=KBStatus.error, next_refresh_at=NOW - timedelta(minutes=1)), "due"
        )
        self.assertIsNone(action(status=KBStatus.error))

    def test_already_queued_source_is_not_requeued(self) -> None:
        self.assertIsNone(action(status=KBStatus.pending, next_refresh_at=NOW - timedelta(days=9)))

    def test_live_claim_is_left_alone(self) -> None:
        self.assertIsNone(
            action(status=KBStatus.crawling, last_checked_at=NOW - timedelta(minutes=5))
        )

    def test_abandoned_claim_is_recovered(self) -> None:
        # Whatever held this is long gone; without recovery the source's schedule is over.
        stale = NOW - timedelta(hours=STALE_CLAIM_HOURS, minutes=1)
        self.assertEqual(action(status=KBStatus.crawling, last_checked_at=stale), "recovered")
        self.assertEqual(action(status=KBStatus.chunking, last_checked_at=stale), "recovered")

    def test_claim_with_no_clock_is_recovered(self) -> None:
        # The claim writes status and last_checked_at in one transaction, so in-flight with
        # no stamp at all can only be a claim made before that column existed.
        self.assertEqual(action(status=KBStatus.crawling, last_checked_at=None), "recovered")

    def test_naive_timestamps_do_not_raise(self) -> None:
        # What SQLite actually hands back. Comparing naive to aware raises TypeError, which
        # would take down the whole worker tick, not just this source.
        self.assertEqual(action(next_refresh_at=(NOW - timedelta(hours=1)).replace(tzinfo=None)), "due")
        self.assertIsNone(action(next_refresh_at=(NOW + timedelta(hours=1)).replace(tzinfo=None)))
        self.assertEqual(
            action(
                status=KBStatus.crawling,
                last_checked_at=(NOW - timedelta(hours=STALE_CLAIM_HOURS + 1)).replace(tzinfo=None),
            ),
            "recovered",
        )


class NextRunTests(unittest.TestCase):
    def test_unscheduled_has_no_next_run(self) -> None:
        self.assertIsNone(next_run_at(NOW, 0))

    def test_next_run_is_one_interval_out(self) -> None:
        self.assertEqual(next_run_at(NOW, 6), NOW + timedelta(hours=6))


class ChunkSyncTests(unittest.TestCase):
    def test_first_read_inserts_everything(self) -> None:
        plan = plan_chunk_sync([], [rec("alpha"), rec("bravo")])
        self.assertEqual([o for o, _ in plan.insert], [0, 1])
        self.assertEqual(plan.reuse, [])
        self.assertEqual(plan.delete, [])

    def test_unchanged_page_costs_nothing(self) -> None:
        # The whole point: a scheduled re-read of a page nobody edited must not spend a
        # single embedding. Every row survives, so every vector survives with it.
        existing = [(11, "alpha"), (12, "bravo")]
        plan = plan_chunk_sync(existing, [rec("alpha"), rec("bravo")])
        self.assertEqual([cid for cid, *_ in plan.reuse], [11, 12])
        self.assertEqual(plan.insert, [])
        self.assertEqual(plan.delete, [])

    def test_inserted_paragraph_only_costs_the_paragraph(self) -> None:
        # Matching by ordinal would call "bravo" and "charlie" new here, because both shifted
        # down one — the failure that made re-reading unaffordable.
        existing = [(11, "alpha"), (12, "bravo"), (13, "charlie")]
        plan = plan_chunk_sync(existing, [rec("alpha"), rec("NEW"), rec("bravo"), rec("charlie")])
        self.assertEqual([(cid, ordinal) for cid, ordinal, *_ in plan.reuse], [(11, 0), (12, 2), (13, 3)])
        self.assertEqual(plan.insert, [(1, rec("NEW"))])
        self.assertEqual(plan.delete, [])

    def test_removed_passage_is_deleted(self) -> None:
        plan = plan_chunk_sync([(11, "alpha"), (12, "gone")], [rec("alpha")])
        self.assertEqual([cid for cid, *_ in plan.reuse], [11])
        self.assertEqual(plan.insert, [])
        self.assertEqual(plan.delete, [12])

    def test_duplicate_text_is_matched_one_for_one(self) -> None:
        # Boilerplate repeated on a page must stay stable across reads rather than churning.
        plan = plan_chunk_sync([(11, "same"), (12, "same")], [rec("same"), rec("same")])
        self.assertEqual(sorted(cid for cid, *_ in plan.reuse), [11, 12])
        self.assertEqual(plan.insert, [])
        self.assertEqual(plan.delete, [])

    def test_extra_copy_of_duplicate_text_is_inserted(self) -> None:
        plan = plan_chunk_sync([(11, "same")], [rec("same"), rec("same")])
        self.assertEqual([cid for cid, *_ in plan.reuse], [11])
        self.assertEqual([o for o, _ in plan.insert], [1])

    def test_reused_passage_picks_up_moved_page_metadata(self) -> None:
        # The text is unchanged but it now lives at a different URL — the citation has to
        # follow it, or a jump-link points at a page that no longer carries the passage.
        plan = plan_chunk_sync([(11, "alpha")], [rec("alpha", url="https://b/", title="B")])
        self.assertEqual(plan.reuse, [(11, 0, "https://b/", "B")])

    def test_wholly_rewritten_page_replaces_everything(self) -> None:
        plan = plan_chunk_sync([(11, "old"), (12, "older")], [rec("new")])
        self.assertEqual(plan.reuse, [])
        self.assertEqual([o for o, _ in plan.insert], [0])
        self.assertEqual(sorted(plan.delete), [11, 12])


if __name__ == "__main__":
    unittest.main()
