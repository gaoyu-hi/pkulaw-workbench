# 阶段 3 测试记录

**日期：** 2026-10-31  
**阶段：** 逐篇采集迁移  
**结果：** 通过

## 交付内容

- 可替换的 `SourceAdapter` 接口；
- 北大法宝页面适配器；
- 外置 JSON 页面选择器配置；
- 中央法规、地方法规和分别检索；
- 年份、省份、时效性和每页条数；
- 分页读取和逐页目标核验；
- 详情页标题、机关、文号、公布日期、施行日期、时效性和效力位阶；
- 单篇 Word 下载；
- DOCX ZIP 结构、必要内部文件和 CRC 校验；
- 标题规范化；
- 同名政策候选分组；
- 用户选择候选版本；
- SQLite schema v2；
- 任务项状态、载荷、错误和当前阶段；
- 逐条断点；
- 停止后恢复；
- 失败项重试；
- 机构额度硬停止；
- 滑块/人机验证等待人工继续；
- 逐篇采集任务界面。

## 数据模型

SQLite schema 从 v1 升级到 v2，保留旧任务并自动增加：

- `tasks.checkpoint_json`
- `tasks.started_at`
- `tasks.finished_at`
- `task_items.current_stage`

新增任务项操作：

- 创建或更新任务项；
- 查询任务项；
- 逐条状态和错误；
- 将失败项重置后重试；
- 完整任务和任务项详情。

## 自动化测试

### Python

```text
Ran 10 tests
OK
```

覆盖：

- 标题宽度、破折号和标点规范化；
- 同名政策合并为候选组；
- 候选评分；
- 安全文件名；
- 任务项和 checkpoint 更新；
- v1 到 v2 数据库迁移；
- 原有协议、SQLite 和错误路径。

### TypeScript / Electron

```text
Test Files  2 passed (2)
Tests       3 passed (3)
```

- Electron/Python 协议与任务存储集成测试继续通过；
- 机构配置和 Session key 测试继续通过；
- TypeScript main/preload/renderer 类型检查通过；
- Electron 生产构建通过。

## Electron 烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

阶段 2 回归烟测和阶段 3 烟测均通过。

阶段 3 实测：

| 检查项 | 结果 |
| --- | --- |
| 内置浏览器登录 | 通过 |
| 中央法规检索 | 通过 |
| 地方法规检索 | 通过 |
| 年份/省份/时效性筛选 | 通过 |
| 2 页分页 | 通过 |
| 12 条分页政策逐条 Word 下载 | 通过 |
| 下载 DOCX 结构与 CRC 校验 | 通过 |
| 停止后从已完成项恢复 | 通过 |
| 恢复后已完成项不重复下载 | 通过 |
| 首轮详情失败进入失败状态 | 通过 |
| 失败项重置后重试成功 | 通过 |
| 机构额度页面硬停止 | 通过 |
| 滑块页面进入人工等待 | 通过 |
| 3 个同名候选不自动选择 | 通过 |
| 用户选择 2024 中央版本 | 通过 |
| 所选 Word 正文与候选一致 | 通过 |
| 机关、文号、效力位阶写入任务项 | 通过 |
| 退出后无 Electron/Python 残留 | 通过 |

烟测报告关键值：

```text
ok = true
paginationPagesScanned = 2
paginationItems = 12
resumeAfterStop = true
filesBeforeStop = 2
filesAfterResume = 12
retryRecovered = true
quotaHardStopped = true
captchaManualWaitObserved = true
duplicateCandidateCount = 3
duplicateChosenPolicyId = cn-duplicate-2024
detailMetadataValidated = true
singleWordValidated = true
```

## 文件与数据库检查

```text
database schema = 2
PRAGMA integrity_check = ok
tasks = 6
task_items = 15
completed task_items = 15
Word files = 15
unique SHA-256 hashes = 15
```

Word 文件位于：

```text
apps/desktop/runtime/smoke-stage3/single-word/01_来源Word/北大法宝/
```

文件名包含来源层级、公布年份、政策标题和政策 ID。

## 安全行为

- 搜索、详情和分页每一步都重新确认页面状态；
- 不以“点击已执行”作为成功条件；
- 同名政策存在多个有效链接时暂停，不能自动取第一条；
- 详情标题和候选标题规范化后必须一致；
- 只有 Electron 下载完成事件和实际文件都存在时才标记成功；
- DOCX 必须通过结构和 CRC 校验；
- 机构额度错误直接停止任务；
- 滑块进入人工等待；
- 登录或页面异常不会被记录成“政策不存在”；
- 运行不使用 Agent 或在线模型。

## 已知限制

- 当前自动化验证使用本地 HTML/HTTP 夹具；
- 真实北大法宝页面仍需使用新适配器做小规模在线验证；
- 当前选择器配置以本地夹具为主，后续可为真实站点增加独立 selector profile；
- 仅迁移逐篇采集，批量 ZIP 尚未开始；
- TXT、总目录和来源报告属于后续阶段；
- 安装包和内置 Python 打包尚未开始。

## 结论

阶段 3 验收项全部通过，可以进入阶段 4“批量采集迁移”。

