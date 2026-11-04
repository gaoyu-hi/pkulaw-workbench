# 北大法宝工作台 0.2.3 停止原因与年份切换修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复中央法规省份参数错误、年份切换失败、停止原因不可读、详情弹窗被浏览器覆盖和错误文案难懂的问题，形成可再次真实站点验收的 0.2.3 Windows 便携版。

**Architecture:** 在应用层建立统一的“法规库 → 省份策略”，中央法规永远不发送地方省份，中央+地方按子库分别应用；年份筛选改为点击可见节点并确认选中状态后再进入检索事务。任务停止状态改为顶部单因摘要、明确详情按钮、停靠动作和受限内部滚动；详情打开时隐藏 Electron `WebContentsView`，关闭后恢复。

**Tech Stack:** Python 3.14、SQLite、Electron 45、React 19、TypeScript、Vitest、unittest、Electron Stage 6 烟测、PyInstaller。

**Spec:** `D:\北大法宝爬取工具\后续问题记录.md`

## 方案结论

当前 0.2.2 不宜继续做正式多任务实测。应先完成一个独立的 0.2.3 修复批次，
解决五个阻断点：

1. 中央法规不再携带地方省份；
2. 年份切换成功后，才进入扫描；
3. 停止主因在视口内直接可见；
4. “查看停止原因”只需单击一次；
5. 弹窗打开时，Electron 浏览器原生视图真正隐藏。

修复后先在夹具中验证，再做真实站点零下载预览，最后只下载一条 Word。

## 修复策略

- 省份规则同时落在界面、预览引擎、单篇任务、批量任务和后端校验中；
- 年份树点击内层可见节点，并轮询确认页面已选中目标年份；
- 停止状态只保留一个用户主因，详细技术信息进入独立详情；
- 左侧运行面板允许受控内部滚动，应用外层仍不滚动；
- 不依靠 HTML `z-index` 对抗 Electron 原生网页层，改为弹窗期间隐藏浏览器；
- 所有修复先写失败测试，再实现并跑全量回归。

## Global Constraints

- 中央法规的省份控件必须隐藏，实际发送的省份值必须为 `全部省份`。
- 地方法规显示省份控件，并允许用户选择具体省份。
- 中央+地方时显示省份控件；中央子库使用 `全部省份`，地方子库使用用户选择的省份。
- 旧任务中的“中央法规 + 北京市”不能继续产生 `FILTER_NOT_APPLIED`；预览、开始、修订和恢复均必须归一化。
- 年份从 2012 切换到 2013 时，必须在页面实际选中 2013 后才允许扫描。
- 不能把“页面仍显示全部年份”当作成功，也不能只重试而不修复点击节点。
- 任务停止后，用户无需猜测或双击，必须能看到明确的“查看停止原因”按钮。
- 模态详情打开期间，内置浏览器不能覆盖弹窗任何区域；关闭后恢复浏览器。
- 不使用 `z-index` 解决 Electron 原生网页层覆盖问题。
- 全应用外层不滚动；允许左侧任务运行面板内部受控滚动。
- 主摘要只能显示一个用户可理解的主因和下一步，原始 JSON 仅在详情中展示。
- 不得解决、绕过或自动提交验证码、登录和机构额度。
- 修复期间保留已完成项、失败项、SQLite 任务、输出文件和扫描断点。
- 不扩大到 WPS、PDF、纯文本、超文本、纯净版或法宝联想选项。
- 目标版本为 `0.2.3`；未经确认不推送 GitHub、不合并、不正式发布。

## Review Focus

- `central` 与遗留 `province=北京市` 同时存在时，预览、开始、修订和恢复都不能再请求地方省份。
- `both` 场景中，中央请求不得携带北京市，地方请求不得丢失北京市。
- 年份树从 2012 切到 2013 时，页面可能要求点击内层 `.node_name` 而不是外层链接。
- 窗口高度接近现场 `1426 × 875` 时，停止摘要和详情按钮仍必须在视口内。
- 详情弹窗打开后，Electron 内置网页必须实际隐藏，而不是仅被 HTML 遮罩挡住。

---

### Task 1: 统一法规库与省份策略

**Files:**
- Create: `apps/desktop/src/renderer/src/collector-library-policy.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Modify: `apps/desktop/tests/collector-panels.test.ts`
- Modify: `apps/desktop/tests/task-settings-panel.test.ts`
- Create: `engine/pkulaw_engine/sources/library_scope.py`
- Modify: `engine/pkulaw_engine/collector_preview.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Create: `engine/tests/test_library_scope.py`
- Modify: `engine/tests/test_collector_preview.py`
- Modify: `engine/tests/test_scan_resume.py`
- Modify: `engine/tests/test_batch_scan_resume.py`

**Interfaces:**
- Produces: `CollectorLibrary = "central" | "local" | "both"`.
- Produces: `showsProvinceControl(library: CollectorLibrary): boolean`.
- Produces: `effectiveProvince(library: CollectorLibrary, selectedProvince: string): string`.
- Produces: `province_for_library(library: str, selected_province: str) -> str`.
- Produces: `province_for(self, library: str) -> str` on single, batch, and preview task configs.

- [ ] **Step 1: Write the failing renderer policy tests**

Assert these exact behaviors:

```ts
expect(showsProvinceControl("central")).toBe(false);
expect(showsProvinceControl("local")).toBe(true);
expect(showsProvinceControl("both")).toBe(true);
expect(effectiveProvince("central", "北京市")).toBe("全部省份");
expect(effectiveProvince("local", "北京市")).toBe("北京市");
expect(effectiveProvince("both", "北京市")).toBe("北京市");
```

Assert that a central `TaskSettingsPanel` render does not contain
`data-testid="collector-province-row"`, while local and both renders do.

- [ ] **Step 2: Run the renderer tests and verify RED**

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts
```

Expected: FAIL because `collector-library-policy.ts` and the conditional
province row do not exist.

- [ ] **Step 3: Implement the renderer policy**

Create `collector-library-policy.ts` with the three interfaces from the
Interfaces block. In all three forms:

- add `data-testid="collector-province-row"` to the province wrapper;
- render the wrapper only when `showsProvinceControl(library)`;
- build preview/start/settings payload with `effectiveProvince(library, province)`;
- keep the stored local province draft so switching central → local restores the
  user's previous local selection without sending it to the central request.

- [ ] **Step 4: Run the renderer tests and verify GREEN**

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/task-settings-panel.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 5: Write the failing engine scope tests**

Add tests for these exact cases:

```python
assert province_for_library("central", "北京市") == "全部省份"
assert province_for_library("local", "北京市") == "北京市"
assert province_for_library("central", "全部省份") == "全部省份"

calls = preview_both_libraries(province="北京市")
assert [call["library"] for call in calls] == ["central", "local"]
assert [call["province"] for call in calls] == ["全部省份", "北京市"]
```

Also assert a legacy central task with `province="北京市"` previews and starts
with `全部省份`, while its completed files and scan journal are unchanged.

- [ ] **Step 6: Run the engine tests and verify RED**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_library_scope tests.test_collector_preview tests.test_scan_resume tests.test_batch_scan_resume -v
```

Expected: FAIL because central and `both` currently pass the selected province
to every sublibrary.

- [ ] **Step 7: Implement the engine policy**

Create `engine/pkulaw_engine/sources/library_scope.py`:

```python
ALL_PROVINCES = "全部省份"

def province_for_library(library: str, selected_province: str) -> str:
    if library == "central":
        return ALL_PROVINCES
    return selected_province or ALL_PROVINCES
```

Add `province_for(library)` methods to single, batch, and preview configs.
Replace every per-library request use of `config.province` with
`config.province_for(library)`. This includes initial year-window discovery,
regular preview combinations, single scan combinations, batch scan
combinations, resume revalidation, and fallback searches.

Do not remove or rewrite the user's stored `province` field. Normalize only the
effective per-sublibrary request so switching back to local keeps the user's
selected province.

- [ ] **Step 8: Run the engine tests and verify GREEN**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_library_scope tests.test_collector_preview tests.test_scan_resume tests.test_batch_scan_resume -v
Set-Location ..
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add apps/desktop/src/renderer/src/collector-library-policy.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskSettingsPanel.tsx apps/desktop/tests/collector-panels.test.ts apps/desktop/tests/task-settings-panel.test.ts engine/pkulaw_engine/sources/library_scope.py engine/pkulaw_engine/collector_preview.py engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py engine/tests/test_library_scope.py engine/tests/test_collector_preview.py engine/tests/test_scan_resume.py engine/tests/test_batch_scan_resume.py
git commit -m "fix: scope province filters by regulation library"
```

---

### Task 2: 修复年份树切换与选中确认

**Files:**
- Modify: `engine/pkulaw_engine/sources/pkulaw.py`
- Modify: `engine/pkulaw_engine/sources/search_transaction.py`
- Test: `engine/tests/test_pkulaw_adapter_operations.py`
- Test: `engine/tests/test_search_transaction.py`
- Test: `engine/tests/test_scan_resume.py`

**Interfaces:**
- Produces: `_wait_for_filter_selection(key: str, value: str, root_selector: str, timeout_seconds: float = 8.0) -> dict[str, Any]`.
- Extends `SourceAccessError` with optional `details: dict[str, Any]`.
- Extends `SearchTransitionTimeout` with optional `details: dict[str, Any]`.

- [ ] **Step 1: Write the failing year-switch regression test**

Add a test equivalent to:

```python
def test_year_tree_clicks_visible_node_name_before_returning_changed(self):
    result = apply_tree_filter("year", "2013")
    script = self.tab.run_js.call_args_list[0].args[0]
    self.assertIn("node_name", script)
    self.assertIn("pointerdown", script)
    self.assertIn("mouseup", script)
    self.assertEqual(result["expected"], "2013")
    self.assertEqual(result["actual"], "2013")
```

Add a second test where the first click leaves the tree on `全部年份`.
Expected: the adapter raises `YearFilterUnavailable` with details containing
`filterKey="year"`, `expected="2013"`, and `actual="全部年份"`.

- [ ] **Step 2: Run the tests and verify RED**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_pkulaw_adapter_operations tests.test_search_transaction -v
```

Expected: FAIL because the current implementation clicks the outer item and
returns `changed=True` without confirming that 2013 became selected.

- [ ] **Step 3: Implement the visible-node click**

In the tree branch of `_apply_filter_action`, use this target order:

1. visible `.node_name` inside the matched item;
2. matched item itself.

Dispatch `pointerdown`, `mousedown`, `pointerup`, `mouseup`, and `click`
events with `bubbles: true`. Do not use `HTMLElement.doubleClick()` or
synthetic keyboard events.

- [ ] **Step 4: Confirm the filter actually changed**

After a changed action, poll `_wait_for_filter_selection` until the selected
label equals the requested year. A selection is confirmed only when the year
root contains a current/selected/active item whose cleaned label equals the
requested label.

If confirmation times out, raise `YearFilterUnavailable` with structured
details. Do not fall through to a generic `FILTER_NOT_APPLIED`.

- [ ] **Step 5: Preserve structured details through the transaction**

Add optional `details` to `SourceAccessError` and
`SearchTransitionTimeout`. When `_run_refinement` catches a year transition
timeout, copy `expected`, `actual`, `currentYear`, `availableYears`, and
`filterKey` into `YearFilterUnavailable.details`.

- [ ] **Step 6: Verify 2012 → 2013 without losing 2012**

Add a scan-resume test where 2012 completes with one row, then 2013 begins.
Assert:

- 2013 search request contains `year="2013"`;
- no completed 2012 row or page-journal entry is removed;
- a persistent 2013 failure records its current year and actual selected filter;
- a resumed task retries from 2013 rather than restarting 2012.

- [ ] **Step 7: Run the focused and resume tests**

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_pkulaw_adapter_operations tests.test_search_transaction tests.test_scan_resume -v
Set-Location ..
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add engine/pkulaw_engine/sources/pkulaw.py engine/pkulaw_engine/sources/search_transaction.py engine/tests/test_pkulaw_adapter_operations.py engine/tests/test_search_transaction.py engine/tests/test_scan_resume.py
git commit -m "fix: confirm year tree selection before scanning"
```

---

### Task 3: 生成单一、可理解的失败原因

**Files:**
- Modify: `apps/desktop/src/renderer/src/task-presentation.ts`
- Modify: `apps/desktop/src/renderer/src/TaskPauseDetailDialog.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/tests/task-presentation.test.ts`
- Modify: `apps/desktop/tests/task-runtime.test.ts`

**Interfaces:**
- Produces:

```ts
export interface TaskFailureView {
  code: string;
  title: string;
  summary: string;
  nextStep: string;
  severity: "error" | "fatal";
}

export function taskFailureView(task: TaskRecord): TaskFailureView | null;
```

- [ ] **Step 1: Write failing presentation tests**

Add fixtures for:

1. `YEAR_FILTER_UNAVAILABLE` with `expected="2026"` and an available list whose
   maximum is `"2025"`;
2. `YEAR_FILTER_UNAVAILABLE` while moving from 2012 to 2013 with
   `actual="全部年份"`;
3. legacy `FILTER_NOT_APPLIED` for `北京市` while library is `central`;
4. `FILTER_NOT_APPLIED` after `YEAR_FILTER_UNAVAILABLE` in preflight.

Expected user-facing titles:

```text
所选年份不可用
未能切换到 2013 年
中央法规不支持按省份筛选
年份筛选失败，后续检索已停止
```

Assert the main summary does not contain Python dictionary text such as
`{'ok': False` or `'exact filter option missing'`.

- [ ] **Step 2: Run the tests and verify RED**

```powershell
pnpm --dir apps/desktop vitest run tests/task-presentation.test.ts tests/task-runtime.test.ts
```

Expected: FAIL because all filter errors currently share one generic title and
display the raw message.

- [ ] **Step 3: Implement `taskFailureView`**

Use structured `error.details` first. Fall back to checkpoint metadata for old
tasks. Rules:

- expected year outside available list: state requested year and highest
  available year;
- actual year differs from expected year: state expected year and actual year;
- central + non-all province: state that central law does not support province
  filtering;
- nested `collectorPreflight` error: display the earliest domain failure as the
  primary cause and label the outer error as a consequence;
- unknown errors keep the existing generic fallback and raw details.

- [ ] **Step 4: Use the new presentation in the dialog**

`TaskPauseDetailDialog` must show:

1. friendly title;
2. one-sentence summary;
3. one concrete next step;
4. technical code and stage in a secondary fact grid;
5. full raw JSON only in the bottom expandable area.

- [ ] **Step 5: Run tests and typecheck**

```powershell
pnpm --dir apps/desktop vitest run tests/task-presentation.test.ts tests/task-runtime.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/renderer/src/task-presentation.ts apps/desktop/src/renderer/src/TaskPauseDetailDialog.tsx apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/tests/task-presentation.test.ts apps/desktop/tests/task-runtime.test.ts
git commit -m "fix: explain task failures in user-facing language"
```

---

### Task 4: 固定停止摘要与明确详情入口

**Files:**
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Modify: `apps/desktop/tests/task-runtime.test.ts`
- Modify: `apps/desktop/tests/collector-panels.test.ts`

**Interfaces:**
- Produces component:

```tsx
TaskFailureSummaryCard({
  failure,
  onOpen
}: {
  failure: TaskFailureView;
  onOpen: () => void;
})
```

- Produces `TaskRuntimePanel` prop:

```ts
onPauseDetailOpenChange?: (open: boolean) => void;
```

- [ ] **Step 1: Write failing layout tests**

Assert:

- a stopped task with an error renders exactly one
  `data-testid="task-failure-summary"`;
- the summary appears before `data-testid="task-search-context"`;
- the summary contains one `查看停止原因` button;
- `task-runtime-scroll` contains the remaining detailed runtime cards;
- the normal summary no longer renders both `pause-reason-card` and
  `task-error-card`;
- the summary and button are outside the internal scroll element;
- no stopped card requires a double click.

- [ ] **Step 2: Run tests and verify RED**

```powershell
pnpm --dir apps/desktop vitest run tests/task-runtime.test.ts tests/collector-panels.test.ts
```

Expected: FAIL because the current failure card is rendered near the bottom,
duplicates the error card, and has no bounded runtime scroll.

- [ ] **Step 3: Rebuild the runtime card order**

Use this order:

```text
primary action bar
progress snapshot
failure summary and “查看停止原因” button
internally scrollable runtime details
```

For non-error tasks, no failure summary is rendered.

- [ ] **Step 4: Implement the bounded task-panel scroll**

At the task workspace:

- `.single-run-card` and `.batch-run-card` use a vertical flex layout;
- `.task-runtime-scroll` uses `min-height: 0`, `overflow-y: auto`,
  `overscroll-behavior: contain`;
- heading, progress and failure summary remain outside that scroll;
- outer `.app-shell`, `main`, workspace, and body must not gain page scrolling.

- [ ] **Step 5: Verify the 1426 × 875 layout**

In Stage 6 smoke assert:

- the failure summary bottom is inside the primary card viewport;
- the `查看停止原因` button is inside the viewport;
- the full raw error is outside the visible summary;
- clicking the button once opens the dialog;
- `task-runtime-scroll.scrollHeight > clientHeight` demonstrates long details
  remain accessible without outer scrolling.

- [ ] **Step 6: Run tests and typecheck**

```powershell
pnpm --dir apps/desktop vitest run tests/task-runtime.test.ts tests/collector-panels.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/task-runtime.test.ts apps/desktop/tests/collector-panels.test.ts
git commit -m "fix: keep stopped-task reasons visible and readable"
```

---

### Task 5: 弹窗打开时隐藏 Electron 浏览器层

**Files:**
- Modify: `apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Modify: `apps/desktop/tests/task-workspace-frame.test.ts`
- Modify: `apps/desktop/tests/task-runtime.test.ts`

**Interfaces:**
- Produces `TaskWorkspaceFrame` prop:

```ts
pauseDetailOpen: boolean;
```

- Consumes `onPauseDetailOpenChange` from Task 4.

- [ ] **Step 1: Write failing frame tests**

Render the frame with `pauseDetailOpen=false`:

```ts
expect(markup).toContain('data-browser-visible="true"');
```

Render with `pauseDetailOpen=true`:

```ts
expect(markup).toContain('data-browser-visible="false"');
expect(markup).toContain("browser collapsed");
```

Assert that closing the detail returns `data-browser-visible` to true.

- [ ] **Step 2: Run the test and verify RED**

```powershell
pnpm --dir apps/desktop vitest run tests/task-workspace-frame.test.ts tests/task-runtime.test.ts
```

Expected: FAIL because the frame only considers `settingsOpen`.

- [ ] **Step 3: Lift pause-detail state**

Add a `pauseDetailOpen` state to both collector panels. Pass
`onPauseDetailOpenChange={setPauseDetailOpen}` into `TaskRuntimePanel`.
When selected task changes, close the detail and reset the flag.

- [ ] **Step 4: Hide the native browser view**

Pass `pauseDetailOpen` into `TaskWorkspaceFrame`. Set:

```tsx
visible={!settingsOpen && !pauseDetailOpen}
```

The renderer must hide the browser view before the modal is shown. Closing the
modal re-renders the browser with its last size and session.

- [ ] **Step 5: Add the non-overlap layout state**

Add `dialog-open` to `task-workspace-body` when either settings or pause detail
is open. In this state the browser surface is collapsed and the task panel uses
the full content width. Do not attempt to solve the issue through a larger HTML
`z-index`.

- [ ] **Step 6: Run tests and typecheck**

```powershell
pnpm --dir apps/desktop vitest run tests/task-workspace-frame.test.ts tests/task-runtime.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/task-workspace-frame.test.ts apps/desktop/tests/task-runtime.test.ts
git commit -m "fix: hide embedded browser behind task dialogs"
```

---

### Task 6: 扩展 Electron 集成烟测与真实站点零下载验收

**Files:**
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/desktop/src/main/fixture-server.ts`
- Modify: `apps/desktop/tests/task-workspace.test.ts`
- Modify: `docs/2026-11-03_USABILITY_SEARCH_REMEDIATION_ACCEPTANCE.md`

**Interfaces:**
- Consumes all interfaces from Tasks 1–5.

- [ ] **Step 1: Extend the fixture**

Add deterministic fixture states for:

- a stopped task with a long raw error;
- central, local, and both library selections;
- a year tree where clicking the outer node alone does nothing and clicking the
  inner `.node_name` changes the selected year.

- [ ] **Step 2: Extend Stage 6 assertions**

At `1426 × 875`, assert:

- central has no `collector-province-row`;
- local and both have the row;
- the stopped failure summary and detail button are inside the task card;
- `document.body.scrollHeight <= innerHeight + 1`;
- one click opens the detail dialog;
- while open, `data-browser-visible="false"`;
- closing the dialog returns `data-browser-visible="true"`;
- the raw Python dictionary is not shown in the summary;
- 2012 → 2013 completes without `YEAR_FILTER_UNAVAILABLE`.

- [ ] **Step 3: Run Stage 6 and verify RED/GREEN**

```powershell
pnpm --dir apps/desktop smoke:stage6
```

Expected after implementation: exit 0 and a JSON report containing all
assertions above.

- [ ] **Step 4: Run real-site zero-download previews**

Use an authenticated embedded browser and do not start downloads:

1. central-law preview with a stale `province="北京市"`;
2. expected request province: `全部省份`;
3. local-law Beijing preview with range `2008–2026`;
4. expected effective years include 2012, 2013, and 2017 where present;
5. expected `downloadsStarted=0`, `downloadIntentsCreated=0`,
   `tasksCreated=0`.

Do not proceed if a captcha appears; wait for the user to complete it.

- [ ] **Step 5: Run one bounded single-item check**

Only after both previews pass:

- use the 2012 Beijing result already found by the user;
- set page limit to 1;
- download exactly one Word item;
- verify the file identity and do not run a full multi-year download.

- [ ] **Step 6: Commit**

```powershell
git add apps/desktop/src/main/index.ts apps/desktop/src/main/fixture-server.ts apps/desktop/tests/task-workspace.test.ts docs/2026-11-03_USABILITY_SEARCH_REMEDIATION_ACCEPTANCE.md
git commit -m "test: cover stop diagnostics and year transitions"
```

---

### Task 7: 文档、版本和 0.2.3 便携包

**Files:**
- Modify: `package.json`
- Modify: `apps/desktop/package.json`
- Modify: `engine/pyproject.toml`
- Modify: `engine/pkulaw_engine/__init__.py`
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/desktop/src/renderer/src/App.tsx`
- Modify: `deployment/README_快速开始.md`
- Modify: `deployment/使用手册.md`
- Modify: `deployment/故障排查.md`
- Modify: `deployment/发布与验收.md`
- Create: `docs/PKULAW_WORKBENCH_0.2.3_TEAM_MANUAL_ZH.md`
- Create: `docs/2026-11-04_STOP_DIAGNOSTICS_YEAR_SWITCH_ACCEPTANCE.md`

**Interfaces:**
- Consumes all behavior and evidence from Tasks 1–6.

- [ ] **Step 1: Bump all runtime metadata**

Set these exact values:

```text
root package version: 0.2.3
desktop package version: 0.2.3
Python package version: 0.2.3
engine version: 0.2.3
portable version: 0.2.3
```

Do not rewrite historical 0.2.2 test reports.

- [ ] **Step 2: Update user documentation**

Document:

- central hides province and uses all provinces;
- local shows province;
- both applies province only to local records;
- the exact “查看停止原因” single-click workflow;
- the fact that the browser temporarily collapses while the dialog is open;
- year-switch failures and what the user should do next;
- no captcha automation.

- [ ] **Step 3: Run the full regression**

```powershell
pnpm test
pnpm --dir apps/desktop smoke:stage6
```

Expected: Python, Vitest, TypeScript and Electron smoke all pass.

- [ ] **Step 4: Prepare the packaging environment**

```powershell
& 'D:\Python\python.exe' -m venv .packaging-venv
& '.\.packaging-venv\Scripts\python.exe' -m pip install -r engine\requirements-build.txt
$env:PKULAW_PACKAGING_PYTHON = (Resolve-Path '.\.packaging-venv\Scripts\python.exe').Path
```

Expected: the virtual environment can import `PyInstaller`.

- [ ] **Step 5: Build and verify the portable package**

```powershell
pnpm build:portable
pnpm verify:portable
pnpm package:source
```

Expected: all exit 0; record ZIP size and SHA-256.

- [ ] **Step 6: Write the acceptance report**

Record:

- baseline commit;
- exact test counts;
- Stage 6 result;
- real-site zero-download previews;
- one bounded Word download;
- portable ZIP name, size and SHA-256;
- source ZIP name, size and SHA-256;
- any deferred issue that does not block 0.2.3.

- [ ] **Step 7: Commit**

```powershell
git add package.json apps/desktop/package.json engine/pyproject.toml engine/pkulaw_engine/__init__.py apps/desktop/src/main/index.ts apps/desktop/src/renderer/src/App.tsx deployment/README_快速开始.md deployment/使用手册.md deployment/故障排查.md deployment/发布与验收.md docs/PKULAW_WORKBENCH_0.2.3_TEAM_MANUAL_ZH.md docs/2026-11-04_STOP_DIAGNOSTICS_YEAR_SWITCH_ACCEPTANCE.md
git commit -m "release: prepare version 0.2.3"
```

---

## Execution Preflight

- Create a clean implementation workspace from the retained 0.2.2 source archive.
- Verify the baseline tests before changing code.
- Use a new branch named `fix/0.2.3-stop-diagnostics-year-switch`.
- Never edit or extract over `D:\北大法宝爬取工具\最新版_20261104`.
- Keep all generated release files in a separate `修复工作区_0.2.3`.
- Do not push, merge, or publish without explicit confirmation.

## Final Acceptance Gate

The release is not accepted until all are true:

- Python, Vitest and TypeScript are green;
- Electron Stage 6 smoke is green;
- central hides province;
- local shows province;
- both sends all provinces to central and Beijing to local;
- 2012 → 2013 switches successfully;
- stop summary is visible without scrolling;
- one click opens the complete reason dialog;
- the native browser is hidden while that dialog is open;
- real-site zero-download previews pass;
- one bounded Word download passes;
- portable and source ZIP hashes are recorded;
- current tasks and output files remain recoverable.

## Self-Review

- Requirement coverage: all recorded stop-reason, browser-overlay, central
  province, year-transition, and explanation problems have an owning task.
- Type consistency: `CollectorLibrary`, `effectiveProvince`,
  `province_for_library`, `TaskFailureView`, `pauseDetailOpen`, and
  `onPauseDetailOpenChange` are defined before their consumers.
- Review focus: each listed failure mode has a corresponding assertion in Task
  1, 2, 4, 5, or 6.
- Scope: no download-format expansion and no captcha bypass are included.
- Release safety: real-site testing remains zero-download until the bounded
  single-item gate.
