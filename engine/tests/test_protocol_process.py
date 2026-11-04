from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from typing import Any

from pkulaw_engine.storage import TaskStore

ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "1.0.0"


class ProtocolProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self._start_process()

    def _start_process(self) -> None:
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "pkulaw_engine",
                "--data-dir",
                self.tempdir.name,
                "--app-version",
                "0.1.0-test",
            ],
            cwd=ENGINE_ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.reader = threading.Thread(target=self._read_stdout, daemon=True)
        self.reader.start()

    def tearDown(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self._close_process_streams()
        self.tempdir.cleanup()

    def _close_process_streams(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()
        if self.process.stderr is not None:
            self.process.stderr.close()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            if not line.strip():
                continue
            self.messages.put(json.loads(line))

    def _receive(self, timeout: float = 5.0) -> dict[str, Any]:
        return self.messages.get(timeout=timeout)

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        request_id = request_id or str(uuid.uuid4())
        self._send(
            {
                "type": "request",
                "protocolVersion": PROTOCOL_VERSION,
                "requestId": request_id,
                "method": method,
                "params": params or {},
                "timestamp": "test",
            }
        )
        events: list[dict[str, Any]] = []
        while True:
            message = self._receive()
            if message.get("type") == "event":
                events.append(message)
                continue
            self.assertEqual(message.get("requestId"), request_id)
            return message, events

    def test_handshake_tasks_errors_duplicate_and_shutdown(self) -> None:
        ready = self._receive()
        self.assertEqual(ready["type"], "event")
        self.assertEqual(ready["event"], "engine.ready")
        self.assertEqual(ready["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tasks.pagination.v1", ready["data"]["capabilities"])
        self.assertIn("browser.lease.v1", ready["data"]["capabilities"])

        handshake, _ = self._request(
            "system.handshake",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "client": {"name": "test-host", "version": "0.1.0"},
            },
        )
        self.assertTrue(handshake["ok"])
        self.assertEqual(handshake["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn(
            "tasks.pagination.v1",
            handshake["result"]["capabilities"],
        )
        self.assertIn("browser.lease.v1", handshake["result"]["capabilities"])

        ping, _ = self._request("system.ping")
        self.assertEqual(ping["result"]["status"], "ok")

        mismatched, _ = self._request(
            "system.handshake",
            {
                "protocolVersion": "9.9.9",
                "client": {"name": "test-host", "version": "0.1.0"},
            },
        )
        self.assertFalse(mismatched["ok"])
        self.assertEqual(mismatched["error"]["code"], "PROTOCOL_VERSION_MISMATCH")

        assert self.process.stdin is not None
        self.process.stdin.write("{not-valid-json}\n")
        self.process.stdin.flush()
        invalid_json = self._receive()
        self.assertFalse(invalid_json["ok"])
        self.assertEqual(invalid_json["error"]["code"], "INVALID_JSON")

        create_request_id = "duplicate-create-request"
        created, events = self._request(
            "task.create",
            {
                "name": "协议测试任务",
                "mode": "batch",
                "input": {"keywords": ["草原"]},
                "outputDir": "D:/protocol-test-output",
            },
            create_request_id,
        )
        self.assertTrue(created["ok"])
        task = created["result"]["task"]
        self.assertEqual(task["mode"], "batch")
        created_event = self._receive()
        self.assertEqual(created_event["event"], "task.created")
        self.assertEqual(created_event["data"]["task"]["id"], task["id"])

        duplicated, _ = self._request(
            "task.create",
            {
                "name": "不应重复创建",
                "mode": "single",
                "input": {},
            },
            create_request_id,
        )
        self.assertEqual(duplicated["result"]["task"]["id"], task["id"])

        listed, _ = self._request("task.list", {"limit": 10})
        self.assertEqual(listed["result"]["tasks"][0]["id"], task["id"])

        invalid, _ = self._request("not.a.method")
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "METHOD_NOT_FOUND")

        shutdown, _ = self._request("system.shutdown")
        self.assertEqual(shutdown["result"]["status"], "stopping")
        self.process.wait(timeout=5)
        self.assertEqual(self.process.returncode, 0)
        self.assertTrue((Path(self.tempdir.name) / "pkulaw.sqlite3").is_file())
        self.assertTrue((Path(self.tempdir.name) / "logs" / "engine.jsonl").is_file())

    def test_python_process_restart_recovers_interrupted_task(self) -> None:
        ready = self._receive()
        self.assertEqual(ready["event"], "engine.ready")

        store = TaskStore(Path(self.tempdir.name) / "pkulaw.sqlite3")
        try:
            task = store.create_task(
                name="进程异常恢复",
                mode="single",
                task_input={"queries": ["异常恢复"]},
                output_dir=str(Path(self.tempdir.name) / "output"),
            )
            store.update_task(
                task["id"],
                status="running",
                current_stage="collecting",
                checkpoint={"query": "异常恢复", "page": 1},
            )
        finally:
            store.close()

        self.process.kill()
        self.process.wait(timeout=5)
        self.reader.join(timeout=2)
        self._close_process_streams()
        self._start_process()
        restarted_ready = self._receive()
        self.assertEqual(restarted_ready["event"], "engine.ready")
        handshake, _ = self._request(
            "system.handshake",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "client": {"name": "restart-test", "version": "0.1.0"},
            },
        )
        self.assertTrue(handshake["ok"])
        fetched, _ = self._request("task.get", {"taskId": task["id"]})
        self.assertEqual(fetched["result"]["task"]["status"], "stopped")
        self.assertEqual(
            fetched["result"]["task"]["error"]["code"],
            "APPLICATION_INTERRUPTED",
        )
        lease, _ = self._request("browser.lease")
        self.assertTrue(lease["result"]["lease"]["available"])


if __name__ == "__main__":
    unittest.main()
