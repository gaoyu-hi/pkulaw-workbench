import unittest

from pkulaw_engine.search_governor import SearchActionGovernor


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class SearchActionGovernorTests(unittest.TestCase):
    def test_first_result_action_does_not_wait(self) -> None:
        sleeps: list[float] = []
        governor = SearchActionGovernor(
            clock=FakeClock(100.0),
            sleep=sleeps.append,
        )

        governor.before_result_action("keyword-submission")

        self.assertEqual(sleeps, [])

    def test_second_action_waits_only_the_remaining_interval(self) -> None:
        clock = FakeClock(100.0)
        sleeps: list[float] = []
        governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)

        governor.after_result_action()
        clock.advance(0.4)
        governor.before_result_action("year-filter-switch")

        self.assertEqual(sleeps, [0.6])

    def test_elapsed_interval_at_least_one_second_does_not_wait(self) -> None:
        clock = FakeClock(100.0)
        sleeps: list[float] = []
        governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)

        governor.after_result_action()
        clock.advance(1.2)
        governor.before_result_action("year-filter-switch")

        self.assertEqual(sleeps, [])

    def test_captcha_clearing_forces_ten_second_cooldown(self) -> None:
        clock = FakeClock(100.0)
        sleeps: list[float] = []
        governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)

        governor.after_result_action()
        self.assertFalse(governor.note_captcha_cleared())
        governor.before_result_action("keyword-submission")

        self.assertEqual(sleeps, [10.0])

    def test_captcha_before_first_action_forces_cooldown(self) -> None:
        clock = FakeClock(100.0)
        sleeps: list[float] = []
        governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)

        self.assertFalse(governor.note_captcha_cleared())
        governor.before_result_action("keyword-submission")

        self.assertEqual(sleeps, [10.0])

    def test_second_captcha_within_five_actions_releases_boundary(self) -> None:
        governor = SearchActionGovernor(
            clock=FakeClock(),
            sleep=lambda _: None,
        )

        self.assertFalse(governor.note_captcha_cleared())
        for _ in range(4):
            governor.after_result_action()
            governor.before_result_action("year-filter-switch")

        self.assertTrue(governor.note_captcha_cleared())

    def test_page_actions_do_not_increment_keyword_submission_count(self) -> None:
        governor = SearchActionGovernor(clock=FakeClock(), sleep=lambda _: None)

        governor.before_result_action("year-filter-switch")
        governor.after_result_action()
        governor.before_result_action("year-filter-switch")

        self.assertEqual(governor.snapshot()["keyword_submissions"], 0)
        self.assertEqual(governor.snapshot()["year_filter_switches"], 2)

    def test_snapshot_exposes_expected_fields(self) -> None:
        governor = SearchActionGovernor(clock=FakeClock(), sleep=lambda _: None)

        governor.before_result_action("keyword-submission")
        governor.note_captcha_cleared()

        snapshot = governor.snapshot()
        self.assertEqual(snapshot["keyword_submissions"], 1)
        self.assertEqual(snapshot["year_filter_switches"], 0)
        self.assertEqual(snapshot["captcha_waits"], 1)
        self.assertFalse(snapshot["released_to_boundary"])

    def test_restored_governor_keeps_counts_and_same_action_breaker(
        self,
    ) -> None:
        first = SearchActionGovernor(
            clock=FakeClock(1_000.0),
            sleep=lambda _: None,
        )
        first.record_action_started(
            "keyword-submission",
            "action-1",
            "人工智能|central",
        )
        first.record_action_completed("action-1")
        self.assertFalse(first.record_captcha("action-1"))

        restored = SearchActionGovernor.restore(
            first.snapshot(),
            clock=FakeClock(1_010.0),
            sleep=lambda _: None,
        )
        restored.record_action_started(
            "keyword-submission",
            "action-1",
            "人工智能|central",
        )

        self.assertTrue(restored.record_captcha("action-1"))
        snapshot = restored.snapshot()
        self.assertEqual(snapshot["captcha_waits"], 2)
        self.assertEqual(snapshot["keyword_submissions"], 2)
        self.assertTrue(snapshot["released_to_boundary"])
        self.assertEqual(snapshot["breaker_reason"], "same-action-captcha")

    def test_restored_governor_preserves_five_action_window(self) -> None:
        governor = SearchActionGovernor(
            clock=FakeClock(),
            sleep=lambda _: None,
        )
        governor.record_action_started(
            "year-filter-switch",
            "action-1",
            "人工智能|central",
        )
        self.assertFalse(governor.record_captcha("action-1"))
        governor.record_action_completed("action-1")

        for index in range(2, 5):
            governor.record_action_started(
                "year-filter-switch",
                f"action-{index}",
                "人工智能|central",
            )
            governor.record_action_completed(f"action-{index}")

        restored = SearchActionGovernor.restore(
            governor.snapshot(),
            clock=FakeClock(5.0),
            sleep=lambda _: None,
            random_fn=lambda: 0.5,
        )
        restored.record_action_started(
            "year-filter-switch",
            "action-5",
            "人工智能|central",
        )

        self.assertTrue(restored.record_captcha("action-5"))
        self.assertEqual(
            restored.snapshot()["breaker_reason"],
            "captcha-within-five-actions",
        )

    def test_captcha_on_different_action_outside_window_does_not_break(
        self,
    ) -> None:
        governor = SearchActionGovernor(clock=FakeClock(), sleep=lambda _: None)
        governor.record_action_started(
            "year-filter-switch",
            "action-1",
            "人工智能|central",
        )
        self.assertFalse(governor.record_captcha("action-1"))
        governor.record_action_completed("action-1")

        for index in range(2, 8):
            governor.record_action_started(
                "year-filter-switch",
                f"action-{index}",
                "人工智能|central",
            )
            governor.record_action_completed(f"action-{index}")

        governor.record_action_started(
            "year-filter-switch",
            "action-8",
            "人工智能|central",
        )
        self.assertFalse(governor.record_captcha("action-8"))
        self.assertFalse(governor.snapshot()["released_to_boundary"])

    def test_captcha_clear_cooldown_survives_restore(self) -> None:
        clock = FakeClock(100.0)
        sleeps: list[float] = []
        governor = SearchActionGovernor(clock=clock, sleep=sleeps.append)
        governor.record_action_started(
            "keyword-submission",
            "action-1",
            "人工智能|central",
        )
        governor.record_captcha("action-1")
        governor.record_captcha_cleared("action-1")

        restored = SearchActionGovernor.restore(
            governor.snapshot(),
            clock=FakeClock(103.0),
            sleep=sleeps.append,
        )
        restored.record_action_started(
            "keyword-submission",
            "action-2",
            "人工智能|central",
        )

        self.assertEqual(sleeps, [7.0])

    def test_restore_empty_snapshot_keeps_legacy_defaults(self) -> None:
        governor = SearchActionGovernor.restore(
            {},
            clock=FakeClock(),
            sleep=lambda _: None,
        )

        self.assertEqual(governor.before_result_action("keyword-submission"), None)
        self.assertEqual(governor.snapshot()["result_action_count"], 1)


if __name__ == "__main__":
    unittest.main()
