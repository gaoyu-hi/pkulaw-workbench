from __future__ import annotations

import unittest

from pkulaw_engine.task_capabilities import derive_task_capabilities


def _task(
    *,
    status: str = "stopped",
    error_code: str = "TASK_STOPPED",
    checkpoint: dict[str, object] | None = None,
    archived_at: str | None = None,
) -> dict[str, object]:
    return {
        "id": "task-1",
        "status": status,
        "error": {"code": error_code, "message": error_code},
        "checkpoint": checkpoint or {},
        "archivedAt": archived_at,
    }


class TaskCapabilityTests(unittest.TestCase):
    def test_stopped_task_has_fresh_entry_but_no_live_continue(self) -> None:
        capabilities = derive_task_capabilities(
            _task(
                checkpoint={
                    "currentPage": 2,
                    "scanCompleted": False,
                }
            ),
            controller_alive=False,
            lease={"taskId": "", "leaseId": ""},
        )

        self.assertFalse(capabilities.can_continue_manual)
        self.assertTrue(capabilities.can_resume_from_checkpoint)
        self.assertFalse(capabilities.can_edit_settings)
        self.assertTrue(capabilities.can_create_fresh_task)
        self.assertFalse(capabilities.lease_held)

    def test_access_wait_requires_a_live_controller_to_continue(self) -> None:
        task = _task(
            status="waiting_manual",
            error_code="MANUAL_ACTION_REQUIRED",
        )

        live = derive_task_capabilities(
            task,
            controller_alive=True,
            lease={"taskId": "task-1", "leaseId": "lease-1"},
        )
        dead = derive_task_capabilities(
            task,
            controller_alive=False,
            lease={"taskId": "", "leaseId": ""},
        )

        self.assertTrue(live.can_continue_manual)
        self.assertFalse(live.can_create_fresh_task)
        self.assertTrue(live.lease_held)
        self.assertEqual(live.lease_task_id, "task-1")
        self.assertFalse(dead.can_continue_manual)
        self.assertTrue(dead.can_resume_from_checkpoint)

    def test_paused_task_can_edit_and_resume(self) -> None:
        capabilities = derive_task_capabilities(
            _task(
                status="paused",
                error_code="",
                checkpoint={"currentPage": 1},
            ),
            controller_alive=True,
            lease={"taskId": "task-1", "leaseId": "lease-1"},
        )

        self.assertTrue(capabilities.can_edit_settings)
        self.assertTrue(capabilities.can_resume_from_checkpoint)
        self.assertFalse(capabilities.can_create_fresh_task)

    def test_hard_stop_and_scan_mismatch_block_checkpoint_resume(self) -> None:
        for code in (
            "INSTITUTION_QUOTA_REACHED",
            "CAPTURE_BOUNDARY_REACHED",
            "SCAN_JOURNAL_CONFIG_MISMATCH",
        ):
            with self.subTest(code=code):
                capabilities = derive_task_capabilities(
                    _task(
                        error_code=code,
                        checkpoint={"currentPage": 2},
                    ),
                    controller_alive=False,
                    lease={"taskId": "", "leaseId": ""},
                )
                self.assertFalse(capabilities.can_resume_from_checkpoint)
                self.assertTrue(capabilities.can_create_fresh_task)
                self.assertTrue(capabilities.resume_block_reason)

    def test_archived_and_live_tasks_do_not_offer_fresh_creation(self) -> None:
        archived = derive_task_capabilities(
            _task(archived_at="2026-11-06T00:00:00Z"),
            controller_alive=False,
            lease={"taskId": "", "leaseId": ""},
        )
        running = derive_task_capabilities(
            _task(status="running", error_code=""),
            controller_alive=True,
            lease={"taskId": "task-1", "leaseId": "lease-1"},
        )

        self.assertFalse(archived.can_create_fresh_task)
        self.assertFalse(running.can_create_fresh_task)


if __name__ == "__main__":
    unittest.main()
