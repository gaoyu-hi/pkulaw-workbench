from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pkulaw_engine.browser_bridge import BrowserBridge
from pkulaw_engine.controls.task_control import TaskControl
from pkulaw_engine.logging_utils import JsonLineLogger
from pkulaw_engine.selections.pagination import PageSelection
from pkulaw_engine.selections.years import YearSelection
from pkulaw_engine.single_task import SingleTaskConfig, SingleTaskManager
from pkulaw_engine.sources.pkulaw import ResultsStale
from pkulaw_engine.storage import TaskStore


class _SearchFailureAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, **_kwargs: object) -> dict[str, object]:
        self.calls.append("search")
        raise ResultsStale("旧 DOM 未通过检索事务验证")

    def open_detail(self, _row: dict[str, object]) -> None:
        self.calls.append("open-detail")
        raise AssertionError("旧 DOM 不得打开详情")

    def click_word_download(self) -> None:
        self.calls.append("download")
        raise AssertionError("旧 DOM 不得触发下载")


class TaskSearchGateTests(unittest.TestCase):
    def test_unconfirmed_search_creates_no_item_and_no_download(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            logs = root / "logs"
            logs.mkdir()
            logger = JsonLineLogger(logs / "engine.jsonl")
            store = TaskStore(root / "pkulaw.sqlite3")
            task = store.create_task(
                name="旧 DOM 门禁",
                mode="single",
                task_input={
                    "queries": ["测试政策"],
                    "matchMode": "title",
                    "library": "central",
                    "pageSize": 20,
                    "outputDir": str(root / "output"),
                },
                output_dir=str(root / "output"),
            )
            bridge = BrowserBridge(logger)
            manager = SingleTaskManager(
                store.database_path,
                bridge,
                logger,
                lambda _event, _data: None,
            )
            config = SingleTaskConfig(
                queries=("测试政策",),
                match_mode="title",
                library="central",
                year_selection=YearSelection(mode="all"),
                province="全部省份",
                timeliness="全部（仅记录元数据，不筛选）",
                page_size=20,
                page_selection=PageSelection(mode="last"),
                output_dir=(root / "output").resolve(),
            )
            adapter = _SearchFailureAdapter()
            controller = TaskControl(str(task["id"]))

            with self.assertRaises(ResultsStale):
                manager._collect(  # noqa: SLF001
                    str(task["id"]),
                    config,
                    adapter,  # type: ignore[arg-type]
                    store,
                    controller,
                )

            self.assertEqual(adapter.calls, ["search"])
            self.assertEqual(store.list_task_items(str(task["id"])), [])
            self.assertEqual(bridge.download_cursor(), 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
