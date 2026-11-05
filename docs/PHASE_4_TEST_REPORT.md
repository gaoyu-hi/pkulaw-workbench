# 阶段 4 测试记录

**日期：** 2026-10-31  
**阶段：** 批量采集迁移  
**结果：** 通过

## 交付内容

- 复用现有 `PkulawAdapter` 的检索、子库、筛选和分页；
- 每批最多 50 条；
- 网站原生批量下载按钮、格式弹窗和确认按钮适配；
- 目录文件下载；
- Word ZIP 下载；
- XLSX 校验；
- BIFF XLS 校验；
- HTML-XLS 校验；
- ZIP CRC 校验；
- ZIP 文件数量校验；
- 零字节文件校验；
- 标准化文件名重复校验；
- ZIP 路径穿越校验；
- ZIP 成员到目录政策和稳定政策 ID 的一对一映射；
- SQLite schema v3；
- `task_batches` 批次表；
- 每批状态、进度、目录路径、ZIP 路径、报告路径和错误；
- 批次级停止、恢复和失败重试；
- 已验收批次不因其他批次失败被覆盖；
- 批量任务界面。

## SQLite v3

新增 `task_batches`：

- 稳定 `batch_key`；
- 关键词和子库；
- 页码及起止序号；
- 选中政策快照；
- 目录格式和全文格式；
- 批次状态；
- 目录、ZIP 和验收报告路径；
- 批次错误。

旧 v1/v2 数据库会自动迁移至 v3。

## 自动化测试

### Python

```text
Ran 20 tests
OK
```

阶段 4 新增覆盖：

- XLSX 目录逐条标题和政策 ID 匹配；
- BIFF XLS 目录读取；
- HTML-XLS 表格读取；
- 目录标题不一致时拒绝；
- 有效 ZIP 和身份映射；
- ZIP CRC 损坏时拒绝；
- ZIP 零字节成员拒绝；
- ZIP 标准化重名成员拒绝；
- ZIP `../` 路径穿越拒绝；
- SQLite 批次状态往返与失败批重置；
- schema v3 初始化和旧库迁移。

### TypeScript / Electron

```text
Test Files  2 passed (2)
Tests       3 passed (3)
```

- 协议与任务存储集成测试；
- 机构配置测试；
- main/preload/renderer 类型检查；
- Electron 生产构建。

## Electron 批量烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

阶段 2、阶段 3、阶段 4 烟测全部通过。

阶段 4 实测：

| 检查项 | 结果 |
| --- | --- |
| 本地机构登录 | 通过 |
| 每页 50 条批量检索 | 通过 |
| 60 条结果拆为 50+10 两批 | 通过 |
| 第一批目录和 ZIP 下载/验收 | 通过 |
| 第二批首次 ZIP 为损坏文件 | 正确识别 |
| 第二批失败后第一批保持完成 | 通过 |
| 只重置失败批次 | 通过 |
| 重试后两批全部完成 | 通过 |
| XLSX 目录结构、标题和 ID 匹配 | 通过 |
| HTML-XLS 目录识别 | 通过 |
| ZIP CRC、数量、非空、重名和路径安全 | 通过 |
| ZIP 成员映射到目录政策 | 通过 |
| 验收报告写入正式目录 | 通过 |
| 退出后无 Electron/Python 残留 | 通过 |

烟测关键值：

```text
ok = true
batchCount = 2
batchSizes = [50, 10]
failureIsolatedBeforeRetry = true
completedBatchesBeforeRetry = 1
failedBatchesBeforeRetry = 1
retryCompletedAll = true
xlsxValidated = true
wordZipCrcValidated = true
zipIdentityMappingValidated = true
htmlXlsValidated = true
```

## 数据库和文件检查

```text
database schema = 3
PRAGMA integrity_check = ok
tasks = 2
task_batches = 3
batch status = completed: 3
task_items = 61
completed task_items = 61
XLSX files = 2
HTML-XLS files = 1
ZIP files = 3
batch reports = 3
```

正式批量文件位于：

```text
apps/desktop/runtime/smoke-stage4/batch-files/
```

## 安全行为

- 页面缺少序号或政策 ID 时在下载前停止；
- 勾选框必须同时匹配序号、标题和政策 ID；
- 单批超过 50 条时拒绝提交；
- 目录未通过标题/ID 匹配时不推进批次；
- ZIP 校验失败时目录和 ZIP 不进入正式完成状态；
- 失败批不影响之前已完成批；
- 重试只重置失败批；
- ZIP 路径穿越和标准化重名会被拒绝；
- 正常运行不调用 Agent 或在线模型。

## 已知限制

- 当前验证使用本地 HTML/HTTP 和有效文件夹具；
- 真实北大法宝批量弹窗与文件命名仍需在线小规模验证；
- XLS 与 HTML-XLS 已在文件校验层覆盖，但批量烟测主流程使用 XLSX；
- 尚未生成最终 TXT、统一总目录和来源报告；
- 尚未制作安装包和内置 Python 发布包。

## 结论

阶段 4 验收项全部通过，可以进入阶段 5“正文、目录与来源治理”。

