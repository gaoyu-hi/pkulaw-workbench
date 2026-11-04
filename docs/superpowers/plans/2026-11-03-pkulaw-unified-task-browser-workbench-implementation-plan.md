# 北大法宝统一任务与浏览器工作区 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把机构配置、任务创建、任务运行、浏览器人工处理和自动恢复收敛为固定的一屏工作区。

**Architecture:** 先建立共享工作区状态和唯一浏览器视图，再重做总览与运行布局；随后把“创建并开始”合并为单次编排，加入验证码稳定检测与幂等恢复，最后实现同一任务内的设置版本切换。每阶段通过纯状态测试、Python 编排测试和 Electron 夹具烟测独立验收。

**Tech Stack:** Electron + React + TypeScript、Python 3.14、DrissionPage、SQLite、Vitest、Python unittest、PyInstaller。

**Spec:** `docs/superpowers/specs/2026-11-03-pkulaw-unified-task-browser-workbench-design.md`

## Global Constraints

- 不自动识别、破解或绕过登录、滑块、验证码或机构额度。
- 不保存账号密码，不导出或共享 Cookie。
- 侧边栏不保留“内置浏览器”入口。
- 完整展开基准为至少 1536×864 逻辑像素。
- 外层应用不提供主垂直滚动，只有浏览器保留上下、左右滚动和 50%–200% 缩放。
- “创建并开始”内部必须完成零下载范围核验，任何下载前都不得跳过该门禁。
- 自动恢复只作用于发起登录或验证码等待的原始任务。
- 中途修改设置必须使用同一任务 ID、递增配置版本，并保留已完成文件与审计记录。
- 不新增 WPS、PDF、纯文本、超文本、纯净版下载格式。
- 正常应用运行不依赖 Agent、Codex 或在线大模型。

## Review Focus

- 多个页面同时存在时，旧 `WebContentsView` 边界必须立即隐藏，不能透明覆盖任务按钮。
- 验证码短暂闪烁不能触发恢复；恢复必须幂等，并且只命中原始任务。
- 单按钮启动不能产生两个任务、两个租约或核验前的下载。
- 设置版本切换不能混用旧关键词、旧年份、旧扫描日志，也不能删除已完成文件。
- 固定一屏布局必须真正消除外层滚动，而不是把内容裁掉或藏在不可见区域。

---

### Task 1: 建立共享工作区与恢复状态

**Files:**
- Create: `apps/desktop/src/shared/task-workspace.ts`
- Test: `apps/desktop/tests/task-workspace.test.ts`

**Interfaces:**
- Consumes: `TaskStatus`、`BrowserHostState` 的现有字段。
- Produces:
  - `deriveCollectorWorkspaceMode(input): CollectorWorkspaceMode`
  - `nextManualRecoveryPhase(current, event): ManualRecoveryPhase`
  - `shouldAutoResumeManual(input): boolean`
  - `CollectorWorkspaceMode = "configure"|"running"|"settings"|"manual"|"decision"`
  - `ManualRecoveryPhase = "idle"|"waiting"|"observing"|"verifying"|"resuming"|"succeeded"|"failed"`
- `ManualRecoveryView = { phase: ManualRecoveryPhase; detail: string; canRetry: boolean }`
- `idleManualRecovery: ManualRecoveryView`

- [ ] **Step 1: 写工作区模式和稳定样本失败测试**

```ts
expect(deriveCollectorWorkspaceMode({
  hasTask: true, settingsOpen: false, taskStatus: "running",
  errorCode: "", captchaVisible: false
})).toBe("running");
expect(deriveCollectorWorkspaceMode({
  hasTask: true, settingsOpen: true, taskStatus: "running",
  errorCode: "", captchaVisible: false
})).toBe("settings");
expect(deriveCollectorWorkspaceMode({
  hasTask: true, settingsOpen: false, taskStatus: "waiting_manual",
  errorCode: "MANUAL_ACTION_REQUIRED", captchaVisible: true
})).toBe("manual");
expect(shouldAutoResumeManual({
  taskId: "task-1", leaseTaskId: "task-1", taskStatus: "waiting_manual",
  errorCode: "MANUAL_ACTION_REQUIRED", captchaVisible: false,
  loginVisible: false, authenticated: true, selectorReady: true,
  stableSamples: 1
})).toBe(false);
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pnpm --dir apps/desktop vitest run tests/task-workspace.test.ts`

Expected: FAIL because `task-workspace.ts` does not exist.

- [ ] **Step 3: 实现纯状态函数**

`shouldAutoResumeManual` 只有在任务 ID 与租约 ID 一致、`stableSamples >= 2`、验证码和登录页均不可见、认证及选择器均通过时返回 `true`；分页、规模、同名和额度错误必须返回 `false`。

- [ ] **Step 4: 增加阶段转换测试并实现 reducer**

测试 `waiting → observing → verifying → resuming → succeeded`；验证码重现必须从任意非终态回到 `waiting`；重复成功事件保持 `succeeded`。

- [ ] **Step 5: 运行测试并提交**

Run: `pnpm --dir apps/desktop vitest run tests/task-workspace.test.ts`

Expected: PASS。

```powershell
git add apps/desktop/src/shared/task-workspace.ts apps/desktop/tests/task-workspace.test.ts
git commit -m "feat: define collector workspace state contracts"
```

---

### Task 2: 提取唯一浏览器视图组件

**Files:**
- Create: `apps/desktop/src/renderer/src/BrowserSurface.tsx`
- Create: `apps/desktop/src/renderer/src/useBrowserViewBounds.ts`
- Modify: `apps/desktop/src/renderer/src/BrowserPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/browser-surface.test.ts`

**Interfaces:**
- Consumes: `BrowserHostState` 和 `window.pkulaw.setBrowserBounds`。
- Produces:
  - `BrowserSurface(props)`，其中 props 固定为 `browser`、`visible`、`variant: "overview"|"task"`、`onRefresh`、`onContinue`。
  - `useBrowserViewBounds(hostRef: RefObject<HTMLDivElement | null>, visible: boolean): void`
  - `browserBoundsForVisibility(rect: DOMRect, visible: boolean): BrowserBounds`
  - DOM 属性 `data-browser-visible="true"|"false"`。

- [ ] **Step 1: 写静态渲染失败测试**

```ts
expect(renderToStaticMarkup(
  React.createElement(BrowserSurface, {
    browser: readyBrowser, visible: true, variant: "task",
    onRefresh: () => undefined, onContinue: () => undefined
  })
)).toContain('data-browser-visible="true"');
expect(visibleFalseMarkup).toContain('data-browser-visible="false"');
expect(browserBoundsForVisibility(visibleRect, false)).toEqual({
  x: 0, y: 0, width: 1, height: 1, visible: false
});
```

- [ ] **Step 2: 运行测试确认组件缺失**

Run: `pnpm --dir apps/desktop vitest run tests/browser-surface.test.ts`

Expected: FAIL because `BrowserSurface.tsx` does not exist.

- [ ] **Step 3: 实现浏览器壳层与边界 Hook**

保留地址、后退、刷新、主页、缩放和状态标签。`visible=false` 时立即调用隐藏边界 `{x:0,y:0,width:1,height:1,visible:false}`；卸载时再次隐藏。只有 `visible=true` 才根据宿主矩形更新边界。

- [ ] **Step 4: 让旧 BrowserPanel 复用 BrowserSurface**

暂时保留 BrowserPanel 包装器，删除其中重复的边界观察和工具栏实现；现有开发入口不得出现两个同时活跃的边界观察器。

- [ ] **Step 5: 运行前端测试和类型检查**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/browser-surface.test.ts tests/collector-panels.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/renderer/src/BrowserSurface.tsx apps/desktop/src/renderer/src/useBrowserViewBounds.ts apps/desktop/src/renderer/src/BrowserPanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/browser-surface.test.ts
git commit -m "refactor: extract shared browser surface"
```

---

### Task 3: 重做总览机构配置并移除浏览器导航

**Files:**
- Create: `apps/desktop/src/renderer/src/InstitutionWorkspace.tsx`
- Modify: `apps/desktop/src/renderer/src/App.tsx`
- Delete: `apps/desktop/src/renderer/src/BrowserPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/overview-workspace.test.ts`

**Interfaces:**
- Consumes: `BrowserSurface`、`InstitutionProfile`、`selectInstitution`、`saveInstitution`。
- Produces: `InstitutionWorkspace({ appInfo, backend, onNavigate })`；`onNavigate("single"|"batch"|"settings")`。

- [ ] **Step 1: 写总览结构失败测试**

测试渲染结果必须包含“机构配置”“授权入口”“打开机构页面”“切换账号”“进入逐篇采集”“进入批量采集”，并断言不包含“内置浏览器”侧边栏项、Hero 文案“从采集到可审计交付”。

- [ ] **Step 2: 运行测试确认旧总览失败**

Run: `pnpm --dir apps/desktop vitest run tests/overview-workspace.test.ts`

Expected: FAIL because old App still renders Hero and Browser navigation。

- [ ] **Step 3: 实现 InstitutionWorkspace**

未配置或无已认证 Session 时显示左侧配置卡和右侧 BrowserSurface；已认证时只显示紧凑状态卡。`切换账号` 打开浏览器但不自动清 Cookie，由用户自行退出和登录。

- [ ] **Step 4: 修改 App 导航和总览**

从 `navItems` 删除 `browser`，删除 Hero 和装饰；总览渲染 InstitutionWorkspace，下方保留任务概览与快速入口。删除已无引用的 BrowserPanel。

- [ ] **Step 5: 运行测试和类型检查**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/overview-workspace.test.ts tests/browser-surface.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/renderer/src/App.tsx apps/desktop/src/renderer/src/InstitutionWorkspace.tsx apps/desktop/src/renderer/src/BrowserPanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/overview-workspace.test.ts
git commit -m "feat: move institution login into overview"
```

---

### Task 4: 建立一屏任务运行框架和设置收起

**Files:**
- Create: `apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/tests/task-workspace-frame.test.ts`

**Interfaces:**
- Consumes: `BrowserSurface`、`ManualRecoveryView`。
- Produces:

```ts
interface TaskWorkspaceFrameProps {
  mode: "single" | "batch";
  taskPanel: React.ReactNode;
  settingsPanel?: React.ReactNode;
  browser: BrowserHostState;
  settingsOpen: boolean;
  manualRecovery: ManualRecoveryView;
  onToggleSettings(): void;
}
```

- [ ] **Step 1: 写设置开合失败测试**

当 `settingsOpen=false` 时断言渲染 BrowserSurface；当 `settingsOpen=true` 时断言浏览器不可见且设置面板存在；按钮文案必须是“任务设置”，不得出现“新建任务”。
测试使用 `idleManualRecovery` 作为初始值。

- [ ] **Step 2: 运行测试确认组件缺失**

Run: `pnpm --dir apps/desktop vitest run tests/task-workspace-frame.test.ts`

Expected: FAIL because `TaskWorkspaceFrame.tsx` does not exist.

- [ ] **Step 3: 实现运行框架**

宽屏固定为左任务、右浏览器；外层使用 `height:100vh; overflow:hidden` 链路。任务设置展开时 BrowserSurface 的 `visible=false`，关闭时恢复。低优先级详情使用现有弹窗容器。

- [ ] **Step 4: 将单篇和批量运行态接入框架**

保留现有任务操作和现有表单作为 Task 4 的设置内容；启动前仍沿用旧预览流程，先只完成布局迁移，避免同一次提交同时改变后端和启动语义。

- [ ] **Step 5: 运行测试**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/task-workspace-frame.test.ts tests/collector-panels.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/styles.css apps/desktop/tests/task-workspace-frame.test.ts
git commit -m "feat: add fixed task and browser workspace"
```

---

### Task 5: 实现单按钮启动的后端编排

**Files:**
- Modify: `engine/pkulaw_engine/storage.py`
- Modify: `engine/pkulaw_engine/application.py`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Create: `engine/tests/test_collector_start.py`
- Modify: `engine/tests/test_storage.py`

**Interfaces:**
- Consumes: `CollectorPreviewService.preview`、单篇/批量 Manager、浏览器租约。
- Produces:
  - `TaskStore.update_task_input(task_id, task_input, allowed_statuses: frozenset[str] = frozenset({"pending"})) -> dict`
  - Engine method `collector.start`
  - Python result `{task, preview, details}`
  - TS `CollectorStartResult = { task: TaskRecord; preview: TaskPreviewSummary; details: TaskDetails | BatchTaskDetails }`

- [ ] **Step 1: 写任务输入更新测试**

断言 pending 任务可写入 `previewSnapshot`，非 pending 任务返回
`TASK_INPUT_NOT_EDITABLE`，并断言 JSON 往返不丢失中文字段。

- [ ] **Step 2: 实现 `TaskStore.update_task_input`**

只更新 `input_json` 和 `updated_at`；受允许状态约束；写回后重新读取并返回任务。

- [ ] **Step 3: 写 `collector.start` 编排失败测试**

测试必须断言：

- 只创建一个任务；
- 只获取一个任务级租约；
- 预览下载计数为 0；
- 成功后更新任务输入并启动一次；
- 可恢复的访问门禁预览失败时没有工作线程和下载意图，任务状态为
  `waiting_manual` 且保留原始错误码；
- 输入或输出目录校验失败发生在创建任务之前；
- 相同 operationId 重放返回同一任务。

- [ ] **Step 4: 实现 `collector.start`**

按 `create pending task → acquire task lease → preview → update input with previewSnapshot → start same mode manager` 顺序执行。使用现有 operation cache 保证幂等；预览异常时记录明确错误码，不调用下载方法。

- [ ] **Step 5: 运行 Python 测试**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_storage tests.test_collector_start -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add engine/pkulaw_engine/storage.py engine/pkulaw_engine/application.py engine/tests/test_storage.py engine/tests/test_collector_start.py apps/desktop/src/shared/protocol.ts
git commit -m "feat: orchestrate preflighted collector start"
```

---

### Task 6: 用单按钮替换预览界面

**Files:**
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/collector-workflow.ts`
- Test: `apps/desktop/tests/collector-panels.test.ts`
- Test: `apps/desktop/tests/collector-workflow.test.ts`

**Interfaces:**
- Consumes: `collector.start`。
- Produces:
  - `window.pkulaw.startCollectorTask(input: TaskCreateInput): Promise<CollectorStartResult>`
  - IPC channel `tasks:start-collector`
  - 主按钮文案 `创建并开始逐篇采集` / `创建并开始批量采集`

- [ ] **Step 1: 写界面失败测试**

断言单篇和批量均不渲染 `collector-preview-button`、不包含“预览（0 下载）”，并且只有一个主启动按钮。

- [ ] **Step 2: 实现 IPC 与 preload**

主进程沿用 `ensureWritableDirectory`，调用 `collector.start`，返回任务、预览摘要和详情；不得在渲染进程先调用 `previewTask`。

- [ ] **Step 3: 改造单篇启动**

删除 preview/previewBusy/previewFormKey 的界面状态；点击“创建并开始逐篇采集”后设置忙碌状态，直接调用 `startCollectorTask`，成功后进入运行工作台。

- [ ] **Step 4: 改造批量启动**

行为与单篇一致；批量规模确认仍由运行中的任务门禁处理，不在创建页增加第二个按钮。

- [ ] **Step 5: 运行前端测试和类型检查**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/collector-panels.test.ts tests/collector-workflow.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS，且测试输出中没有遗留预览按钮。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/preload/index.ts apps/desktop/src/main/ipc.ts apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/renderer/src/collector-workflow.ts apps/desktop/tests/collector-panels.test.ts apps/desktop/tests/collector-workflow.test.ts
git commit -m "feat: start collectors with one action"
```

---

### Task 7: 实现验证码清除后的后端恢复

**Files:**
- Create: `engine/pkulaw_engine/manual_recovery.py`
- Modify: `engine/pkulaw_engine/application.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Create: `engine/tests/test_manual_recovery.py`
- Modify: `engine/tests/test_application_browser_lease.py`
- Modify: `apps/desktop/src/shared/protocol.ts`

**Interfaces:**
- Consumes: `browser.user-continue`、任务状态和控制器存活状态。
- Produces:
  - `decide_manual_recovery(task, controller_alive) -> ManualRecoveryDecision`
  - `SingleTaskManager.has_live_controller(task_id) -> bool`
  - `BatchTaskManager.has_live_controller(task_id) -> bool`
  - Engine method `task.resume-after-manual`
  - `ManualRecoveryResult = { action: "continue"|"restart"|"none"; task: TaskRecord; details: TaskDetails | BatchTaskDetails }`

- [ ] **Step 1: 写恢复决策测试**

覆盖 live controller 返回 `continue`、可恢复 stopped task 返回 `restart`、
页数上限/规模/同名/额度返回 `none`、其他 taskId 不得恢复。

- [ ] **Step 2: 实现纯恢复决策模块**

只识别 `MANUAL_ACTION_REQUIRED` 和 `LOGIN_REQUIRED` 等访问门禁错误；
`PAGE_LIMIT_REACHED`、`BATCH_SCALE_CONFIRMATION_REQUIRED`、
`CANDIDATE_SELECTION_REQUIRED`、`INSTITUTION_QUOTA_REACHED` 永不自动恢复。

- [ ] **Step 3: 写应用幂等测试**

断言相同 operationId 只调用一次 browser continue；首次继续失败后，下一次可显式重试；browser gate 未清除时不调用任务继续或重试。

- [ ] **Step 4: 实现 `task.resume-after-manual`**

先调用 `browser_bridge.user_continue()`，再根据任务模式调用 `continue_task`
或多带 operationId 的 `_start_single_task/_start_batch_task(retry_failed=True)`。
checkpoint 写入最近恢复时间和结果，不保存 Cookie 或验证码内容。

- [ ] **Step 5: 运行 Python 测试**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_manual_recovery tests.test_application_browser_lease -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add engine/pkulaw_engine/manual_recovery.py engine/pkulaw_engine/application.py engine/pkulaw_engine/single_task.py engine/pkulaw_engine/batch_task.py engine/tests/test_manual_recovery.py engine/tests/test_application_browser_lease.py apps/desktop/src/shared/protocol.ts
git commit -m "feat: resume originating task after manual access"
```

---

### Task 8: 自动检测验证码并恢复任务

**Files:**
- Create: `apps/desktop/src/renderer/src/useManualRecovery.ts`
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Modify: `apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Test: `apps/desktop/tests/manual-recovery-ui.test.ts`
- Test: `apps/desktop/tests/task-workspace.test.ts`

**Interfaces:**
- Consumes: `window.pkulaw.onBrowserState`、`getBrowserState`、`task.resume-after-manual`。
- Produces:
  - `window.pkulaw.resumeTaskAfterManual(taskId: string, operationId: string): Promise<ManualRecoveryResult>`
  - `useManualRecovery(input): ManualRecoveryView`
  - `ManualRecoveryView = { phase, detail, canRetry }`

- [ ] **Step 1: 写稳定样本和状态展示测试**

断言第 1 次清除仍是 `observing`，第 2 次清除进入 `verifying`；
恢复请求只发送一次；重现验证码返回 `waiting`；恢复失败显示“重新检测并继续”。

- [ ] **Step 2: 实现 manual recovery Hook**

只有在任务处于访问门禁等待时启动 1 秒轮询；使用两个稳定样本和唯一 operationId。任务设置展开后若出现验证码，自动退出设置并重新显示浏览器。

- [ ] **Step 3: 接入任务运行框架**

在左侧任务区显示等待、观察、核验、恢复和失败状态；浏览器始终可见。
继续成功后刷新任务详情，不跳转页面。

- [ ] **Step 4: 验证失败回退**

自动恢复失败时保留浏览器和任务，显示“重新检测并继续”；点击只重新开始稳定检测，不立即强制继续。

- [ ] **Step 5: 运行前端测试**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/manual-recovery-ui.test.ts tests/task-workspace.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/renderer/src/useManualRecovery.ts apps/desktop/src/preload/index.ts apps/desktop/src/main/ipc.ts apps/desktop/src/renderer/src/TaskWorkspaceFrame.tsx apps/desktop/src/renderer/src/TaskRuntimePanel.tsx apps/desktop/tests/manual-recovery-ui.test.ts apps/desktop/tests/task-workspace.test.ts
git commit -m "feat: auto-resume task after captcha clears"
```

---

### Task 9: 实现同一任务的设置版本切换

**Files:**
- Create: `engine/pkulaw_engine/task_settings_revision.py`
- Modify: `engine/pkulaw_engine/storage.py`
- Modify: `engine/pkulaw_engine/application.py`
- Create: `engine/tests/test_task_settings_revision.py`
- Modify: `engine/tests/test_storage.py`
- Modify: `apps/desktop/src/shared/protocol.ts`

**Interfaces:**
- Consumes: 当前 task input、checkpoint、已完成 items 和文件。
- Produces:
  - `build_task_revision(task, new_input, output_dir) -> TaskRevisionPlan`
  - `TaskRevisionPlan = { changed, generation, taskInput, checkpoint }`
  - Engine method `task.apply-settings`
  - `TaskSettingsUpdateResult = { changed: boolean; generation: number; task: TaskRecord; details: TaskDetails | BatchTaskDetails }`

- [ ] **Step 1: 写纯 revision 测试**

断言相同指纹 `changed=false`；不同指纹 `generation + 1`；
清空 `scanJournal`、`currentPage`、`scanCompleted`、`previewSnapshot`，
但 checkpoint 中的历史版本和已完成文件字段保留。

- [ ] **Step 2: 实现 `build_task_revision`**

比较现有配置指纹和新输入指纹；返回新 input 与重置后的 checkpoint。
不得修改已完成 item、批次或文件表。

- [ ] **Step 3: 写应用层失败测试**

覆盖运行中先安全停止、停止超时返回 `TASK_PAUSE_TIMEOUT` 且不写设置、
无变化返回原详情、变化后同一 taskId 启动新扫描、已完成文件仍存在。

- [ ] **Step 4: 实现 `task.apply-settings`**

运行中任务先请求停止并等待 `stopped`；之后 `update_task_input`、
写入新 checkpoint 和 `configGeneration`，再用同一 taskId 和 operationId
从新配置扫描。不得创建第二任务。

- [ ] **Step 5: 运行 Python 测试**

Run:

```powershell
Set-Location engine
& 'D:\Python\python.exe' -X utf8 -m unittest tests.test_task_settings_revision tests.test_storage -v
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add engine/pkulaw_engine/task_settings_revision.py engine/pkulaw_engine/storage.py engine/pkulaw_engine/application.py engine/tests/test_task_settings_revision.py engine/tests/test_storage.py apps/desktop/src/shared/protocol.ts
git commit -m "feat: apply task setting revisions safely"
```

---

### Task 10: 接入任务设置面板与浏览器收起

**Files:**
- Create: `apps/desktop/src/renderer/src/TaskSettingsPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Test: `apps/desktop/tests/task-settings-panel.test.ts`
- Test: `apps/desktop/tests/collector-panels.test.ts`

**Interfaces:**
- Consumes: `task.apply-settings`、现有采集表单字段。
- Produces:
  - `window.pkulaw.applyTaskSettings(taskId, input, operationId): Promise<TaskSettingsUpdateResult>`
  - IPC channel `tasks:apply-settings`
  - `TaskSettingsPanel({ mode, input, busy, onApply, onClose })`

- [ ] **Step 1: 写面板失败测试**

断言运行中按钮名称为“任务设置”；打开后浏览器不可见；关闭无变化时
浏览器恢复；应用变化时调用一次 `applyTaskSettings`。

- [ ] **Step 2: 实现 IPC 和 preload**

主进程验证 taskId、outputDir、operationId，并调用 `task.apply-settings`；
返回 changed、generation 和刷新后的详情。

- [ ] **Step 3: 实现 TaskSettingsPanel**

复用单篇/批量字段，显示当前任务配置；提供“应用设置”和“关闭”。
应用期间禁用表单；成功后刷新任务详情并恢复浏览器。

- [ ] **Step 4: 接入单篇和批量**

运行态只显示“任务设置”；打开设置时 BrowserSurface 隐藏。
若设置期间出现验证码，保留未提交的表单草稿、自动退出设置状态并重新显示
浏览器；完成人工验证后恢复草稿入口，不能丢弃用户输入。

- [ ] **Step 5: 运行测试**

Run:

```powershell
pnpm --dir apps/desktop vitest run tests/task-settings-panel.test.ts tests/collector-panels.test.ts tests/task-workspace-frame.test.ts
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

- [ ] **Step 6: 提交**

```powershell
git add apps/desktop/src/renderer/src/TaskSettingsPanel.tsx apps/desktop/src/renderer/src/SingleCollectorPanel.tsx apps/desktop/src/renderer/src/BatchCollectorPanel.tsx apps/desktop/src/preload/index.ts apps/desktop/src/main/ipc.ts apps/desktop/tests/task-settings-panel.test.ts apps/desktop/tests/collector-panels.test.ts
git commit -m "feat: edit running task settings in place"
```

---

### Task 11: 一屏、验证码和发布包端到端验收

**Files:**
- Modify: `apps/desktop/src/main/index.ts`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Modify: `deployment/使用手册.md`
- Modify: `deployment/README_快速开始.md`
- Modify: `docs/PKULAW_WORKBENCH_0.2.2_TEAM_MANUAL_ZH.md`
- Create: `docs/2026-11-03_UNIFIED_WORKSPACE_ACCEPTANCE.md`

**Interfaces:**
- Consumes: 前十个任务的全部产品接口。
- Produces: 可打包的端到端工作区和验收证据。

- [ ] **Step 1: 扩展 Electron 夹具烟测**

在 1536×864 逻辑视口断言
`document.documentElement.scrollHeight <= window.innerHeight + 1`；
任务区与浏览器的顶部和底部差值均不超过 2px；任务设置打开后
`data-browser-visible="false"`；关闭后为 `true`；侧边栏无 Browser 入口；
总览无 Hero。

- [ ] **Step 2: 扩展验证码夹具烟测**

模拟验证码出现，确认任务进入 manual；模拟一次清除仍等待；
第二次稳定清除必须只产生 1 次 `task.resume-after-manual` 请求；
另一任务不得被调用；验证码重现后稳定计数归零且不恢复。

- [ ] **Step 3: 扩展单按钮和设置版本烟测**

断言创建并开始前后的 task 数量只增加 1、preview 的
`downloadsStarted/downloadIntentsCreated/tasksCreated` 均为 0，随后 worker
启动一次；修改设置后 `configGeneration` 增加 1、`scanJournal={}`、
`currentPage=0`，已完成文件路径仍存在。

- [ ] **Step 4: 运行完整回归**

Run:

```powershell
pnpm test
pnpm build:portable
pnpm verify:portable
```

Expected: 全部退出码为 0；Python、Vitest、TypeScript、便携包首次启动、
一键启动和强制退出恢复全部通过。

- [ ] **Step 5: 真实站点人工监督验收**

由用户完成机构登录或验证码；只执行一次小范围零下载核验和一次受控恢复，
不启动大规模下载，不记录或导出 Cookie。

- [ ] **Step 6: 更新文档并提交**

文档必须说明侧边栏变化、机构登录位置、任务设置、单按钮启动、
验证码自动恢复和外层/浏览器滚动边界。

```powershell
git add apps/desktop/src/main/index.ts apps/desktop/src/renderer/src/styles.css deployment/使用手册.md deployment/README_快速开始.md docs/PKULAW_WORKBENCH_0.2.2_TEAM_MANUAL_ZH.md docs/2026-11-03_UNIFIED_WORKSPACE_ACCEPTANCE.md
git commit -m "test: verify unified task browser workspace"
```

## Self-Review

- Spec 的侧边栏、总览机构登录、左右同屏、任务设置、单按钮启动、
  验证码自动恢复、设置版本和滚动边界均映射到具体任务。
- “验证码自动完成”被明确限定为人工完成后的检测与恢复，不包含绕过。
- 任务启动和设置更新都在后端编排，不依赖渲染层串联多个 API。
- 自动恢复、设置版本和浏览器边界均包含失败关闭与回归测试。
