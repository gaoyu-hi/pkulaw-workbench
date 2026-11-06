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


if __name__ == "__main__":
    unittest.main()
