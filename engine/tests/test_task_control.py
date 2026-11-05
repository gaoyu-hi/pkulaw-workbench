from __future__ import annotations

import threading
import time
import unittest

from pkulaw_engine.controls.task_control import TaskControl, TaskStopped


class TaskControlTests(unittest.TestCase):
    def test_manual_pause_waits_at_boundary_and_resume_continues(self) -> None:
        control = TaskControl("task-1")
        paused = threading.Event()
        finished = threading.Event()

        def worker() -> None:
            control.wait_at_boundary(on_pause=lambda _reason: paused.set())
            finished.set()

        thread = threading.Thread(target=worker)
        control.thread = thread
        self.assertTrue(control.request_pause("用户手动暂停"))
        thread.start()
        self.assertTrue(paused.wait(1))
        self.assertTrue(control.paused)
        self.assertFalse(finished.is_set())

        self.assertTrue(control.request_resume())
        self.assertTrue(finished.wait(1))
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())

    def test_pause_and_stop_requests_are_idempotent(self) -> None:
        control = TaskControl("task-2")
        self.assertTrue(control.request_pause())
        self.assertFalse(control.request_pause())
        self.assertTrue(control.request_stop())
        self.assertFalse(control.request_stop())
        with self.assertRaises(TaskStopped):
            control.check_stop()

    def test_stop_releases_paused_worker(self) -> None:
        control = TaskControl("task-3")
        paused = threading.Event()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                control.wait_at_boundary(on_pause=lambda _reason: paused.set())
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        control.thread = thread
        control.request_pause()
        thread.start()
        self.assertTrue(paused.wait(1))
        control.request_stop()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], TaskStopped)

    def test_manual_continue_and_choice_are_single_use(self) -> None:
        control = TaskControl("task-4")
        continued = threading.Event()
        chosen: list[str] = []

        def continue_worker() -> None:
            control.wait_for_continue()
            continued.set()

        continue_thread = threading.Thread(target=continue_worker)
        continue_thread.start()
        time.sleep(0.05)
        self.assertTrue(control.request_continue())
        self.assertFalse(control.request_continue())
        self.assertTrue(continued.wait(1))
        continue_thread.join(timeout=1)

        choice_thread = threading.Thread(
            target=lambda: chosen.append(control.wait_for_choice("item-1"))
        )
        choice_thread.start()
        time.sleep(0.05)
        control.request_choice("item-1", "candidate-1")
        choice_thread.join(timeout=1)
        self.assertEqual(chosen, ["candidate-1"])


if __name__ == "__main__":
    unittest.main()
