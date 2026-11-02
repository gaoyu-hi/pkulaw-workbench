# 阶段 5 测试记录

**日期：** 2026-10-31  
**阶段：** 正文、目录与来源治理  
**结果：** 通过

## 交付内容

- 从逐篇 Word 生成逐条 UTF-8 TXT；
- 从批量 ZIP 成员生成逐条 UTF-8 TXT；
- 生成统一总目录 CSV；
- 生成统一总目录 XLSX；
- 生成 HTML 任务验收报告；
- 保存来源网址、检索时间、抓取方式、来源类型、核验状态和说明；
- 保存来源 Word、TXT、官方 HTML、官方截图及 SHA-256；
- 使用 `python-docx` 提取 DOCX；
- 旧版 DOC 使用 antiword、LibreOffice 或本机 Microsoft Word 提取；
- 对旧版 DOC 检查 OLE 文件头，避免把 HTML 错页当作 Word；
- 对全文、空行和控制字符进行稳定规范化；
- 数据处理界面显示逐条结果、来源类型、TXT、证据和下载产物；
- 官方补全期间临时显示 Electron 内置浏览器，完成、停止或失败后自动收起。

## SQLite v4

新增：

- `policy_documents`：逐条政策、来源元数据、Word/TXT/证据路径、哈希和状态；
- `processing_runs`：数据处理运行、进度、选项、摘要和错误；
- `task_artifacts`：CSV、XLSX 和任务报告的路径与 SHA-256。

旧 v1/v2/v3 数据库自动迁移到 v4。

## 来源类型

| 类型 | 判定 |
| --- | --- |
| `北大法宝原件` | 逐篇下载 Word 或批量 ZIP 中的有效 Word |
| `政府官网原生附件` | 官方详情页提供的有效 DOCX/DOC |
| `政府官网正文整理` | 官方详情页无原生 Word，由已验证正文生成 DOCX |
| `待核验` | 来源缺失、正文提取失败、官方证据失败、来源异常或同名歧义 |

政府官网正文整理件不会写入“北大法宝原件”或“政府官网原生附件”。

## 政府官网补全边界

只有以下条件同时满足才允许自动补全：

1. 数据处理选项显式开启 `governmentFallback`；
2. 源任务为逐篇任务；
3. 源任务输入模式为 `matchMode=title`；
4. 当前条目的错误码为 `NO_EXACT_MATCH`。

以下情况不触发自动补全：

- 宽泛关键词任务；
- 登录失效；
- 机构或政府官网额度耗尽；
- 滑块、验证码或其他人工验证；
- 北大法宝同名多版本；
- 政府官网出现多个精确同标题官方来源；
- 政府官网只有转载、近似标题、正文过短或来源域名不可信；
- 正文下载或正文提取失败。

系统只接受 `gov.cn` 或 `*.gov.cn`；本地夹具必须在 `127.0.0.1` 且由夹具显式标记为官方来源。

## 输出目录

```text
<任务输出>/processed/
├─ 01_来源Word/
│  ├─ 北大法宝/
│  └─ 政府官网/
├─ 02_正文TXT/
├─ 03_政府官网正文整理/
├─ 04_来源证据/
├─ 05_待核验/
├─ 06_总目录.csv
├─ 06_总目录.xlsx
└─ 07_任务报告.html
```

总目录包含 Word、TXT、来源证据、官方 HTML、官方截图及对应 SHA-256。

## 自动化测试

### Python

```text
Ran 28 tests
OK
```

阶段 5 新增覆盖：

- 政府官网补全边界；
- 精确官方同标题候选选择；
- 多个官方同标题来源进入歧义拒绝；
- 非官方域名拒绝；
- ZIP 成员原子复制和嵌套父目录创建；
- DOCX 正文有效提取；
- 损坏 DOCX 拒绝；
- 旧版 DOC 的 OLE 文件头校验；
- CSV/XLSX 行数；
- Word SHA256、TXT SHA256、官方 HTML、截图证据字段；
- HTML 任务验收报告；
- SQLite v4 和政策文档往返。

### TypeScript / Electron

```text
Test Files  2 passed (2)
Tests       3 passed (3)
```

- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- preload 白名单契约通过。

## Electron 全量烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

阶段 2、3、4、5 全部通过。

阶段 5 烟测结果：

```text
governedDocuments = 5
processedDocuments = 3
pendingDocuments = 2
sourceTypes = [
  北大法宝原件,
  政府官网原生附件,
  政府官网正文整理
]
originalWordValidated = true
txtValidated = true
officialHtmlEvidenceValidated = true
officialScreenshotEvidenceValidated = true
masterCsvXlsxReportValidated = true
batchTxtValidated = true
```

逐篇结果：

| 夹具 | 预期结果 | 实测 |
| --- | --- | --- |
| 阶段三测试政策 | 北大法宝原件，完成 | 通过 |
| 正文提取异常政策 | 待核验 | 通过 |
| 政府官网补全政策 | 政府官网原生附件，完成 | 通过 |
| 政府官网正文整理政策 | 政府官网正文整理，完成 | 通过 |
| 政府官网来源异常政策 | 待核验 | 通过 |

批量结果：

| 夹具 | 预期结果 | 实测 |
| --- | --- | --- |
| 批量测试政策01 | 从有效 Word ZIP 转 TXT，类型为北大法宝原件 | 通过 |

## 独立一致性审计

烟测后直接读取 SQLite、CSV、XLSX 和文件进行复核：

```text
PRAGMA integrity_check = ok
逐篇 SQLite = 5
逐篇 CSV = 5
逐篇 XLSX = 5
processed = 3
pending_review = 2
批量 SQLite = 1
批量 CSV = 1
批量 XLSX = 1
processed = 1
```

逐条验证：

- Word 文件存在且 SHA-256 与 SQLite 一致；
- TXT 文件存在且 SHA-256 与 SQLite payload 和证据 JSON 一致；
- 来源证据 JSON 存在且可解析；
- 证据中的来源类型、Word 哈希和 TXT 哈希与 SQLite 一致；
- 政府官网 HTML 与 PNG 均存在、非空且 SHA-256 一致；
- CSV 行数、XLSX 数据行数和 SQLite 政策数一致；
- 任务报告文件存在；
- 应用退出后没有本项目 Electron 或 Python 残留进程。

## UI 烟测

截图：

```text
outputs/阶段5_UI烟测截图_2026-10-31.png
```

界面验证了：

- 数据处理状态；
- 已完成正文计数；
- 待核验计数；
- 政府官网来源计数；
- 逐条来源类型；
- TXT 文件名；
- 总目录 CSV/XLSX 和任务验收报告产物；
- 产物 SHA-256 摘要。

## 安全行为

- 不保存账号密码；
- 不导出 Cookie；
- 不识别、外送或绕过验证码；
- 不尝试绕过登录或机构额度；
- 日志、登录、额度、验证码和同名歧义不会触发来源回退；
- 宽泛关键词任务只枚举北大法宝结果；
- 官方页面截图失败时该条进入待核验；
- 不把政府 HTML 整理件标记为原生附件；
- 不访问真实北大法宝执行批量下载；
- 正常流程不调用 Agent 或大模型。

## 已知限制

- 当前成功路径使用本地 HTML/HTTP、有效 DOCX、XLSX 和 ZIP 夹具；
- 真实北大法宝和政府官网页面仍需在校园网络环境做小规模验收；
- 本地烟测成功件为 DOCX；旧版 DOC 已实现校验和转换链，但最终正文提取依赖目标机具备 antiword、LibreOffice 或 Microsoft Word；
- 官方截图依赖 Electron 内置浏览器保持可绘制，程序会在需要时临时显示并自动收起；
- 尚未制作安装包、独立 Python 运行时和团队部署文档。

## 结论

阶段 5 验收项全部通过，可以进入阶段 6“界面、稳定性与团队交付”。
