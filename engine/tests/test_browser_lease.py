from __future__ import annotations

import threading
import unittest

from pkulaw_engine.browser_lease import BrowserLeaseBusy, BrowserLeaseManager


class BrowserLeaseManagerTests(unittest.TestCase):
    def test_acquire_release_and_state_contract(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        leases = BrowserLeaseManager(event_sink=lambda event, data: events.append((event, data)))

        available = leases.state()
        self.assertTrue(available["available"])
        self.assertEqual(available["leaseId"], "")

        acquired = leases.acquire("task-a", "single")
        self.assertTrue(acquired["acquired"])
        self.assertTrue(acquired["leaseId"])
        state = leases.state()
        self.assertFalse(state["available"])
        self.assertEqual(state["taskId"], "task-a")
        self.assertEqual(state["mode"], "single")
        self.assertTrue(state["createdAt"])
        self.assertTrue(state["updatedAt"])

        self.assertFalse(leases.release("wrong-lease"))
        self.assertTrue(leases.release(acquired["leaseId"]))
        self.assertTrue(leases.state()["available"])
        self.assertEqual(
            [event for event, _ in events],
            ["browser.lease-acquired", "browser.lease-released"],
        )

    def test_same_task_is_idempotent_and_different_task_is_busy(self) -> None:
        leases = BrowserLeaseManager()
        first = leases.acquire("task-a", "single")
        repeated = leases.acquire("task-a", "single")
        self.assertFalse(repeated["acquired"])
        self.assertEqual(first["leaseId"], repeated["leaseId"])

        with self.assertRaises(BrowserLeaseBusy) as busy:
            leases.acquire("task-b", "single")
        self.assertEqual(busy.exception.code, "BROWSER_LEASE_BUSY")
        self.assertEqual(busy.exception.lease["taskId"], "task-a")

    def test_cross_mode_acquisition_is_mutually_exclusive(self) -> None:
        leases = BrowserLeaseManager()
        leases.acquire("single-a", "single")
        for task_id, mode in (
            ("batch-a", "batch"),
            ("data-a", "data"),
        ):
            with self.subTest(mode=mode):
                with self.assertRaises(BrowserLeaseBusy):
                    leases.acquire(task_id, mode)

    def test_concurrent_acquire_has_exactly_one_winner(self) -> None:
        leases = BrowserLeaseManager()
        barrier = threading.Barrier(8)
        results: list[dict[str, object]] = []
        errors: list[str] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            barrier.wait()
            try:
                result = leases.acquire(f"task-{index}", "single")
                with lock:
                    results.append(result)
            except BrowserLeaseBusy as exc:
                with lock:
                    errors.append(exc.code)

        threads = [
            threading.Thread(target=worker, args=(index,))
            for index in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 7)
        self.assertEqual(set(errors), {"BROWSER_LEASE_BUSY"})

    def test_validate_and_release_are_owner_checked(self) -> None:
        leases = BrowserLeaseManager()
        lease = leases.acquire("task-a", "data")
        validated = leases.validate(lease["leaseId"], "task-a", "data")
        self.assertEqual(validated["leaseId"], lease["leaseId"])
        with self.assertRaisesRegex(Exception, "不属于"):
            leases.validate(lease["leaseId"], "task-b", "data")
        self.assertFalse(leases.release_for_task("task-b"))
        self.assertTrue(leases.release_for_task("task-a"))

    def test_reset_discards_process_local_lease(self) -> None:
        leases = BrowserLeaseManager()
        leases.acquire("task-a", "batch")
        self.assertTrue(leases.reset("python_restart"))
        self.assertTrue(leases.state()["available"])
        fresh = leases.acquire("task-b", "batch")
        self.assertTrue(fresh["acquired"])


if __name__ == "__main__":
    unittest.main()
