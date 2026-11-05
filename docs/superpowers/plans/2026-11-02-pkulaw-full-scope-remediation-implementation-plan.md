# 北大法宝全量正式场景可靠性整改实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 0.2.1 在真实“碳中和”全量场景中暴露的翻页、验证码、下载格式、旧 DOC、正文完整性、来源语义、进度和错误反馈问题，并发布可真实运行的 `0.2.2`。

**Architecture:** 先建立统一 AccessState 和分页事务，再把网站原生目录 XLS 与本地派生 XLSX/HTML-XLS 分离；随后接入内置离线 DOC 转换和正文完整性门禁，最后统一进度、来源模型、UI 和全量真实验收。

**Tech Stack:** Electron + React + TypeScript、Python 3.14、DrissionPage、SQLite、python-docx、openpyxl、xlrd、PyInstaller、Vitest、Python unittest。

**Spec:** `docs/superpowers/specs/2026-11-02-pkulaw-full-scope-remediation-design.md`

## Global Constraints

- 目标版本固定为 `0.2.2`。
- 真实最终验收关键词固定为“碳中和”。
- 不使用西藏等窄场景替代最终验收。
- 不绕过登录、滑块、验证码、校园权限或机构额度。
- 不保存账号密码，不导出 Cookie。
- 网站原生目录固定为 BIFF XLS；XLSX 和 HTML-XLS 必须标记为本地派生或兼容格式。
- 正常应用运行不依赖 Agent、Codex 或在线大模型。
- 继续执行阶段 5 来源治理边界和任务暂停、恢复、停止、归档、删除语义。
- 当前目录不是 Git 仓库；每个任务完成后保存测试记录、文件清单和 SHA-256 快照，不初始化 Git。

## Review Focus

- 表单提交后路由从 `/law/lar` 变为 `/law/search/RecordSearch`，不能误读旧页。
- 真实验证码消失前点击“继续”，不能产生立即重复暂停。
- 原生目录实际为 `.xls`，不能再次等待 `.xlsx` 直到超时。
- 内容位于 `.kuan-content` 且 `innerText` 为空，不能把章节目录当成完整正文。
- 批量已收到正式 ZIP、仅正文转换失败时，不能把原件来源改成“待核验”。

---

### Task 1: 建立全量缺陷红基线

**Files:**
- Create: `engine/tests/fixtures/pkulaw-v6/record-search-results.html`
- Create: `engine/tests/fixtures/pkulaw-v6/pagination-dead-click.html`
- Create: `engine/tests/fixtures/pkulaw-v6/captcha-dragtext-visible.html`
- Create: `engine/tests/fixtures/pkulaw-v6/captcha-overlay-hidden.html`
- Create: `engine/tests/fixtures/pkulaw-v6/detail-fulltext-clauses.html`
- Create: `engine/tests/test_full_scope_contracts.py`
- Modify: `engine/tests/test_pkulaw_adapter_operations.py`
- Modify: `engine/tests/test_browser_bridge.py`
- Modify: `engine/tests/test_processing.py`
- Modify: `engine/tests/test_storage.py`

**Interfaces:**
- Consumes: 现有 V6 页面适配器、BrowserBridge、TaskStore 和文档处理测试夹具。
- Produces: 后续任务共用的失败用例、真实页面形状和期望错误码。

- [ ] **Step 1: 添加结果路由夹具**

`record-search-results.html` 必须包含真实结果 DOM、`#right_form`、分页和隐藏页码字段，但不再包含 `#subLibraries`。

- [ ] **Step 2: 添加死亡点击与验证码夹具**

`pagination-dead-click.html` 的“下一页”存在但点击不推进；`captcha-dragtext-visible.html` 包含可见 `.drag_text`；`captcha-overlay-hidden.html` 包含不可见 `#captcha-overlay`。

- [ ] **Step 3: 添加完整长正文夹具**

`detail-fulltext-clauses.html` 包含 6 个章节、38 个 `.kuan-content` 条文，且容器 `innerText` 为空、`textContent` 完整。

- [ ] **Step 4: 编写失败测试**

测试名必须至少包括：

```text
test_record_search_route_is_results_profile
test_dead_next_click_uses_form_fallback
test_dragtext_captcha_is_visible
test_hidden_overlay_is_not_captcha
test_continue_rejected_while_captcha_remains
test_fulltext_extracts_all_38_clauses
test_native_catalog_xls_contract
test_partial_processing_progress_is_not_complete
test_batch_extraction_failure_keeps_pkulaw_source
```

- [ ] **Step 5: 运行红基线**

Run:

```text
python -m unittest engine.tests.test_full_scope_contracts -v
```

Expected: 新测试因缺少统一模块或旧行为不满足而失败；失败原因必须对应上述缺陷。

- [ ] **Step 6: 保存基线快照**

保存测试命令、退出码和失败摘要到 `docs/0.2.2_TASK_1_RED_BASELINE.txt`。

---

### Task 2: 统一验证码状态与受控分页

**Files:**
- Create: `engine/pkulaw_engine/sources/access_state.py`
- Create: `engine/pkulaw_engine/sources/page_navigation.py`
- Modify: `engine/pkulaw_engine/sources/pkulaw.py`
- Modify: `engine/pkulaw_engine/browser_bridge.py`
- Modify: `engine/pkulaw_engine/sources/selector_profile.py`
- Modify: `engine/pkulaw_engine/sources/profiles/pkulaw-v6.json`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/BrowserPanel.tsx`
- Test: `engine/tests/test_full_scope_contracts.py`
- Test: `engine/tests/test_pkulaw_adapter_operations.py`
- Test: `engine/tests/test_browser_bridge.py`

**Interfaces:**
- Consumes: `tab`, selector profile selectors, SearchSession。
- Produces:
  - `read_access_state(tab, selectors) -> AccessState`
  - `transition_next_page(tab, expected_page, selectors, session, logger) -> PageNavigationResult`
  - Browser state fields `captchaSelector`, `captchaBox`, `captchaCheckedAt`

- [ ] **Step 1: 写 AccessState 失败测试**

断言可见 `.drag_text` 为验证码、隐藏 `#captcha-overlay` 不是验证码、URL 为 `RecordSearch` 时返回 results page kind。

- [ ] **Step 2: 实现 `access_state.py`**

`AccessState` 使用 spec 定义的字段；可见性至少检查尺寸、display、visibility 和 opacity。

- [ ] **Step 3: 让 BrowserBridge 和 PkulawAdapter 共用 AccessState**

删除两套独立验证码查询；BrowserBridge 返回同一字段。

- [ ] **Step 4: 写分页失败测试**

模拟点击后页码停留；断言只执行一次表单提交，最终到第 2 页，SearchSession 仍匹配。

- [ ] **Step 5: 实现 `transition_next_page`**

点击后 4 秒未推进时提交一次 `#right_form`；成功后连续两次稳定才返回；重复调用幂等。

- [ ] **Step 6: 修复结果路由识别**

`/law/search/RecordSearch` 通过真实结果 DOM 识别为 `results`，不得要求 `#subLibraries`。

- [ ] **Step 7: 修复验证码继续门禁**

验证码仍可见时返回 `CAPTCHA_VISIBLE`；消失后连续两次采样才返回 `CAPTCHA_CLEARED` 并允许继续。

- [ ] **Step 8: 运行回归**

Run:

```text
python -m unittest engine.tests.test_full_scope_contracts engine.tests.test_pkulaw_adapter_operations engine.tests.test_browser_bridge -v
pnpm --dir apps/desktop test
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

---

### Task 3: 网站原生目录与派生格式契约

**Files:**
- Create: `engine/pkulaw_engine/catalog_formats.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/validators/catalog.py`
- Modify: `engine/pkulaw_engine/download_intent.py`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Test: `engine/tests/test_full_scope_contracts.py`
- Test: `engine/tests/test_batch_gate.py`
- Test: `engine/tests/test_batch_execution.py`
- Test: `apps/desktop/src/renderer/src/BatchCollectorPanel.test.tsx`

**Interfaces:**
- Consumes: 网站 `catalogExcel` 下载和当前批次行身份。
- Produces:
  - `resolve_catalog_format(value) -> CatalogFormatContract`
  - `convert_catalog(source_xls, requested_format, destination) -> Path`
  - 所有目录 intent 的网站源契约固定 `expectedSuffixes=(".xls",)`

- [ ] **Step 1: 写原生 XLS 契约测试**

断言 `native-xls` 只接受 BIFF `.xls`；`.xlsx` 出现在网站源事件时立即返回 `CATALOG_NATIVE_FORMAT_MISMATCH`。

- [ ] **Step 2: 写派生格式测试**

同一 XLS 分别转换为合法 XLSX 和 HTML-XLS；结果必须标记 `native=false`、`sourceFormat=xls`。

- [ ] **Step 3: 实现 `catalog_formats.py`**

保留 API 兼容值 `xls`、`xlsx`、`html-xls`，内部映射到 `native-xls`、`derived-xlsx`、`compat-html-xls`。

- [ ] **Step 4: 修改批量下载流程**

先消费网站原生 XLS，再按请求派生最终目录；派生成功后才创建 ZIP intent。

- [ ] **Step 5: 修改错误卡数据**

格式不匹配时返回实际文件名、后缀、签名、downloadId、intentId、保存路径和建议动作。

- [ ] **Step 6: 修改 UI 文案**

显示“网站原生 XLS”“本地转换 XLSX”“兼容 HTML-XLS”，不得写“网站原生 XLSX/HTML-XLS”。

- [ ] **Step 7: 运行回归**

Run:

```text
python -m unittest engine.tests.test_batch_gate engine.tests.test_batch_execution engine.tests.test_full_scope_contracts -v
pnpm --dir apps/desktop test
```

Expected: PASS。

---

### Task 4: 内置旧版 DOC 转换与完整正文提取

**Files:**
- Create: `engine/pkulaw_engine/body_quality.py`
- Modify: `engine/pkulaw_engine/processors/documents.py`
- Modify: `engine/pkulaw_engine/sources/pkulaw.py`
- Modify: `engine/pkulaw-engine.spec`
- Create: `engine/vendor/antiword/win-x64/antiword.exe`
- Create: `engine/vendor/antiword/LICENSE.txt`
- Test: `engine/tests/test_full_scope_contracts.py`
- Test: `engine/tests/test_processing.py`
- Test: `engine/tests/test_policy.py`

**Interfaces:**
- Consumes: Word `.doc/.docx` 和详情页 DOM。
- Produces:
  - `extract_document(path, runtime_root) -> DocumentExtractionResult`
  - `extract_complete_detail(tab, selectors) -> dict`
  - `assess_body_completeness(text, metadata) -> BodyCompleteness`

- [ ] **Step 1: 写真实样本测试**

对 `engine/tests/fixtures/real-word-doc/*.doc` 断言内置转换器可在没有系统 antiword、LibreOffice、Word COM 的环境中提取中文正文。

- [ ] **Step 2: 写正文完整性测试**

长正文夹具断言提取 38 个条文、6 个章节，缺条或章节后直接接下一章节时返回 `suspicious`。

- [ ] **Step 3: 接入并校验 antiword 二进制**

记录版本和 SHA-256；运行前校验文件存在、可执行且版本输出符合锁定值。

- [ ] **Step 4: 实现统一文档提取入口**

`.docx` 使用 python-docx；`.doc` 优先内置 antiword，失败后依次尝试系统 LibreOffice 和 Word COM。

- [ ] **Step 5: 实现完整详情提取**

合并标题、直接文本、章节标题、`.kuan-content` 的 `textContent` 和表格，不依赖容器 `innerText`。

- [ ] **Step 6: 运行测试**

Run:

```text
python -m unittest engine.tests.test_policy engine.tests.test_processing engine.tests.test_full_scope_contracts -v
```

Expected: 三个真实 DOC 样本和 38 条正文夹具全部通过。

---

### Task 5: SQLite v6 来源模型与报表

**Files:**
- Modify: `engine/pkulaw_engine/storage.py`
- Modify: `engine/pkulaw_engine/processing_task.py`
- Modify: `engine/pkulaw_engine/processors/reporting.py`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/DataPanel.tsx`
- Test: `engine/tests/test_storage.py`
- Test: `engine/tests/test_processing.py`
- Test: `apps/desktop/src/renderer/src/task-presentation.test.ts`

**Interfaces:**
- Consumes: `DocumentExtractionResult`、`BodyCompleteness`、原件来源。
- Produces: schema v6 和文档字段 `textSourceType`、`extractionMethod`、`converterName`、`converterVersion`、`bodyCompleteness`、`reviewStatus`。

- [ ] **Step 1: 写 v5→v6 迁移测试**

旧数据库迁移后保留全部数据，新列有安全默认值。

- [ ] **Step 2: 写来源拆分测试**

批量 ZIP 已取得但 DOC 转换失败时，`sourceType` 仍为“北大法宝原件”或“北大法宝ZIP”，`bodyCompleteness=failed`，`reviewStatus=pending_review`。

- [ ] **Step 3: 实现 schema v6**

更新迁移、写入和反序列化；`PRAGMA integrity_check` 必须保持 `ok`。

- [ ] **Step 4: 更新 CSV/XLSX/报告**

增加“正文来源、提取工具、提取状态、完整性、核验状态”列。

- [ ] **Step 5: 运行测试**

Run:

```text
python -m unittest engine.tests.test_storage engine.tests.test_processing -v
pnpm --dir apps/desktop test
```

Expected: PASS。

---

### Task 6: 进度、状态、错误与时间线

**Files:**
- Create: `engine/pkulaw_engine/progress.py`
- Modify: `engine/pkulaw_engine/single_task.py`
- Modify: `engine/pkulaw_engine/batch_task.py`
- Modify: `engine/pkulaw_engine/processing_task.py`
- Modify: `engine/pkulaw_engine/storage.py`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/TaskRuntimePanel.tsx`
- Modify: `apps/desktop/src/renderer/src/task-presentation.ts`
- Modify: `apps/desktop/src/renderer/src/TaskCenterPanel.tsx`
- Test: `engine/tests/test_task_manager_controls.py`
- Test: `engine/tests/test_processing.py`
- Test: `apps/desktop/src/renderer/src/task-presentation.test.ts`

**Interfaces:**
- Consumes: 扫描页数、条目数、批次阶段和成功/失败计数。
- Produces:
  - `ProgressSnapshot`
  - checkpoint 字段 `progressSnapshot`
  - 去重后的阶段时间线

- [ ] **Step 1: 写进度测试**

断言扫描第 2/19 页不会仍为 10%；批量目录和 ZIP 阶段递增；0/3 成功不得生成 100% 成功语义。

- [ ] **Step 2: 实现 `progress.py`**

单篇权重：预检 5%、扫描 25%、逐条下载 65%、收尾 5%。批量权重：扫描 20%、目录与校验 20%、ZIP 与校验 30%、发布 30%。

- [ ] **Step 3: 接入三个任务管理器**

每次状态变化写入 `progressSnapshot`，terminal partial 保留真实计数。

- [ ] **Step 4: 后端事件去重**

同一阶段、动作、错误码和计数在 2 秒内重复时不新增 task event；真实阶段变化必须保留。

- [ ] **Step 5: 更新错误卡**

增加实际文件、格式契约、downloadId、intentId、完整性和恢复动作。

- [ ] **Step 6: 运行测试**

Run:

```text
python -m unittest engine.tests.test_task_manager_controls engine.tests.test_processing -v
pnpm --dir apps/desktop test
pnpm --dir apps/desktop typecheck
```

Expected: PASS。

---

### Task 7: 搜索模式预检、网页缩放与文案统一

**Files:**
- Modify: `apps/desktop/src/renderer/src/SingleCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BatchCollectorPanel.tsx`
- Modify: `apps/desktop/src/renderer/src/BrowserPanel.tsx`
- Modify: `apps/desktop/src/main/browser-host.ts`
- Modify: `apps/desktop/src/main/ipc.ts`
- Modify: `apps/desktop/src/preload/index.ts`
- Modify: `apps/desktop/src/shared/protocol.ts`
- Modify: `apps/desktop/src/renderer/src/styles.css`
- Test: `apps/desktop/src/renderer/src/SingleCollectorPanel.test.tsx`
- Test: `apps/desktop/src/renderer/src/BatchCollectorPanel.test.tsx`

**Interfaces:**
- Consumes: 检索表单和 BrowserHost WebContentsView。
- Produces:
  - 0 下载预览结果
  - `setBrowserZoom(factor: number): Promise<number>`
  - 按 Session 持久化缩放比例

- [ ] **Step 1: 写模式文案测试**

断言默认模式为关键词，完整标题显示为“精确完整标题”，页面说明不混淆两种模式。

- [ ] **Step 2: 增加全量预览**

预览显示组合数、实际结果数、总页数、预计 50 条批次数，不创建下载 intent。

- [ ] **Step 3: 增加缩放**

仅对 WebContentsView 设置 0.5–2.0 缩放；`Ctrl +/-/0` 快捷键与工具栏同步。

- [ ] **Step 4: 统一验证码状态**

任务卡、浏览器状态和继续按钮使用同一 AccessState 字段。

- [ ] **Step 5: 运行测试**

Run:

```text
pnpm --dir apps/desktop test
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop build
```

Expected: PASS。

---

### Task 8: 离线全量回归与 0.2.2 构建

**Files:**
- Modify: 版本元数据、锁文件、构建脚本和发布文档。
- Test: 全部 Python、Vitest、Electron 烟测和便携包验证。

**Interfaces:**
- Consumes: Task 1–7 的完成实现。
- Produces: 可执行离线回归和 `0.2.2` 便携包，但尚不能宣布真实全量通过。

- [ ] **Step 1: 统一版本**

应用、后端、协议展示、构建元数据全部改为 `0.2.2`。

- [ ] **Step 2: 运行完整回归**

Run:

```text
pnpm test
pnpm test:python:coverage
pnpm test:node:coverage
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop build
pnpm --dir apps/desktop smoke:stage2
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
pnpm --dir apps/desktop smoke:stage5
pnpm --dir apps/desktop smoke:stage6
pnpm build:portable
pnpm package:source
pnpm verify:portable
```

Expected: 全部 PASS，SQLite schema v6，`integrity_check=ok`。

- [ ] **Step 3: 验证内置 DOC 转换器**

在隔离目录中禁用系统 antiword、LibreOffice 和 Word COM，确认内置转换器仍可处理真实 DOC。

- [ ] **Step 4: 保存阶段报告**

写 `docs/0.2.2_PHASE_G_TEST_REPORT.md`，不得写真实站点全量通过。

---

### Task 9: 真实“碳中和”全量扫描与分层下载验收

**Files:**
- Create: `scripts/run_0_2_2_carbon_neutral_acceptance.py`
- Create: `outputs/0.2.2碳中和全量真实验收/`
- Create: `docs/0.2.2_PHASE_H_TEST_REPORT.md`

**Interfaces:**
- Consumes: 0.2.2 便携包、用户已登录的 Electron Session。
- Produces: 全量检索扫描、固定分层下载样本、批量样本、
  数据处理和一致性证据。
- Acceptance: `docs/0.2.2_REAL_SITE_SAMPLING_ACCEPTANCE.md`。

- [ ] **Step 1: 记录门禁**

核对版本、SHA-256、协议、schema v6、selector profile、机构登录和浏览器租约；任一失败停止。

- [ ] **Step 2: 只读全量预检**

查询固定“碳中和”，中央+地方，全部年份，关键词模式，每页 100，扫描到尾页。只读预检不得点击 Word。

- [ ] **Step 3: 执行 I-S 固定分层逐篇样本**

正式全范围扫描和 SearchSession 校验必须通过。Word 下载使用预先固定、
记录 SHA-256 的 25 条地方库补充样本：2008–2020 每年 1 条，
2021–2024 每年按华东、华中/中原、西部三组各 1 条。已有超额完成项保留，
不重复下载；未抽中项标记
`OUT_OF_SCOPE_BY_SAMPLING_ACCEPTANCE`。

- [ ] **Step 4: 执行单篇数据处理**

生成每条 TXT、CSV、XLSX、任务报告和证据；每条记录正文来源、转换器、完整性和 SHA-256。

- [ ] **Step 5: 执行 I-B 分层批量样本**

使用独立的“碳中和”真实任务和当前构建，至少执行一个新的小批量任务；
结合既有 I-3 至 I-6 覆盖中央、地方、中央+地方、单条批次、少于 50 条和
正好 50 条批次。网站原生格式固定为 BIFF `.xls`，XLSX/HTML-XLS 从原生
XLS 本地派生并明确标记；Word ZIP 及目录身份必须真实校验。

- [ ] **Step 6: 执行批量数据处理**

验证 ZIP 内全部 DOC 可提取，批次文件、SQLite、TXT、CSV、XLSX、报告和哈希一致。

- [ ] **Step 7: 核验抽样不变量**

核验全量扫描与分页证据、固定样本 SHA-256、计划外下载为 0、
已完成批次不重下、源文件与任务项一一对应、SQLite integrity 通过、
进程无残留；同一稳定 ID 的 Word SHA-256 可交叉比对。

- [ ] **Step 8: 汇总问题**

任何真实失败必须保留证据；系统性问题修复后重试受影响样本，不重复已完成
下载。最终报告必须说明全量执行的是检索与身份验证，文档下载执行的是固定
分层样本，并列出所有未触发或环境限制项。

---

### Task 10: 0.2.2 发布收尾

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/0.2.2_FEATURE_ACCEPTANCE_MATRIX.md`
- Modify: 使用手册、故障排查、数据恢复和复现文档。
- Create: `outputs/0.2.2真实全量验收报告_2026-11-02.md`

**Interfaces:**
- Consumes: Task 8 构建和 Task 9 真实证据。
- Produces: 正式 0.2.2 发布包和最终结论。

- [ ] **Step 1: 更新验收矩阵**

只按真实证据更新状态；未触发的真实验证项保持未触发。

- [ ] **Step 2: 生成便携包和 SHA-256**

重新构建并复算 `pnpm verify:portable`。

- [ ] **Step 3: 检查发布包隐私和安全**

确认不包含账号、密码、Cookie、真实私密响应、日志测试数据和开发缓存。

- [ ] **Step 4: 写发布报告**

报告必须区分夹具、离线、真实单篇、真实批量和未触发项。

- [ ] **Step 5: 最终停机**

完成 0.2.2 交付后停止，不进入 0.3.0。

