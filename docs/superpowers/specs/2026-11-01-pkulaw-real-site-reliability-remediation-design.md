# 北大法宝真实站点可靠性与批量下载修复设计

**日期：** 2026-11-01  
**目标版本：** `0.2.1`  
**状态：** 已确认（2026-11-01）  
**适用范围：** Electron + React 主应用、Python/DrissionPage 后端、北大法宝 V6 站点适配器、SQLite 任务断点、批量目录/ZIP 和数据产物

## 1. 背景

`0.2.0` 已完成本地夹具、Electron 烟测、便携包和真实站点只读验收，但后续真实操作暴露了以下阻断：

1. 北大法宝真实 Word 下载返回有效 OLE `.doc`，单篇任务却固定使用只接受 `.docx` 的校验器。
2. 提交新检索后，程序会短暂接受上一查询的旧页码、旧总页数和旧结果行。
3. 该旧 DOM 问题导致 `maxPages=1` 被绕过，并触发 2 个计划外 Word 下载。
4. 设置隐藏 `Pager.PageSize` 不会改变真实页面中的“条/页”控件。
5. 真实结果默认按效力位阶分组，必须切换“不分组”后才会出现完整分页。
6. 当前 V6 profile 的年份、省份和时效性真实筛选项尚未实现；省份和时效性可能被静默忽略。
7. 批量任务已建立单条批次，但下载前重新检索时，页面重渲染短暂缺少 `libraryRoot`，导致任务在目录和 ZIP 下载前失败。
8. 同类型任务并发、快速双击启动、下载事件归属和跨阶段崩溃恢复缺少完整自动化保护。

## 2. 目标

- 保证任何任务只处理能够证明属于当前检索请求的结果。
- 让单篇和批量任务支持真实 `.doc` 与 `.docx`。
- 让年份、省份、时效性、每页条数和“不分组”在真实 V6 页面可操作、可回读、可失败关闭。
- 保证同一时间只有一个浏览器型任务控制内置网页。
- 保证每个下载事件能够与明确的下载意图关联。
- 允许短暂 DOM 重绘，但不能永久缺失或猜测页面身份。
- 对暂停、继续、停止、应用退出和 Python 重启保持现有安全边界。
- 在团队部署前补齐关键故障注入、资源限制和输出安全测试。

## 3. 非目标

- 不迁移到 Playwright，不使用外部浏览器或固定调试端口。
- 不绕过登录、滑块、验证码或机构额度。
- 不新增真实站点大批量下载。
- 不改变“宽泛关键词不自动回退政府官网”的边界。
- 不改变“仅逐篇精确标题且 `NO_EXACT_MATCH` 时才允许显式政府官网补全”的边界。
- 不新增云服务、账号密码存储或 Cookie 导出。
- 不要求 SQLite schema 从 v5 升级；新的运行时状态优先保存到现有 checkpoint、payload 和 JSON 字段。

## 4. 核心设计决策

### 4.1 采用站点事务模型

检索、筛选、分页、选择批次和下载都视为有身份的事务，而不是互相独立的选择器操作。

```text
页面租约
  -> 检索事务
    -> 筛选事务
      -> 结果快照
        -> 批次选择
          -> 目录下载事务
          -> ZIP 下载事务
            -> 原子发布
```

任一层无法证明输入身份时停止，不向下传递当前 DOM。

### 4.2 采用单浏览器租约

所有会控制内置网页的任务必须先从 Python 引擎获取租约：

- 逐篇采集；
- 批量采集；
- 需要浏览器访问政府官网的数据处理任务。

租约所有权包含：

```text
leaseId
taskId
mode
createdAt
updatedAt
```

任意时刻最多一个所有者。任务成功、失败、停止、异常或应用退出时释放租约。

### 4.3 采用可验证的 SearchSession

一次新检索只有同时满足页面身份和稳定条件后，才生成 `SearchSession`。

```ts
type SearchSession = {
  searchId: string
  query: string
  matchMode: "keyword" | "title"
  library: "central" | "local"
  year: string
  province: string
  timeliness: string
  requestedPageSize: number
  actualPageSize: number
  page: number
  totalPages: number
  resultCount: number
  rowCount: number
  groupingMode: "grouped" | "ungrouped"
  resultSignature: string
  observedAt: string
}
```

`searchId` 在每次提交新搜索时生成，并写入结果 checkpoint。任务处理、批次选择和恢复都校验当前页面属于同一 `SearchSession`。

### 4.4 采用页面类型和操作类型共同决定门禁

选择器是否可用不能只由一组全局必需控件决定。门禁应同时包含：

```text
profile
pageKind: search | results | detail | login | unknown
operation: search | read-results | select-batch | download-catalog | download-zip | read-detail
```

例如：

- 结果页批次选择不要求顶部库导航始终可见；
- 提交搜索必须要求搜索框、搜索按钮和检索类型；
- 详情下载必须要求详情下载入口；
- 批量下载必须要求批量按钮、格式弹层和确认按钮。

## 5. 组件设计

### 5.1 BrowserLeaseManager

新增 Python 内部模块：

```text
engine/pkulaw_engine/browser_lease.py
```

职责：

- 原子获取和释放浏览器租约；
- 检查同一任务重复启动；
- 检查逐篇、批量和浏览器型数据处理之间的互斥；
- 向 BrowserHost 暴露当前租约状态；
- 在异常退出时清理内存租约。

接口：

```text
acquire(taskId, mode) -> leaseId
release(leaseId) -> void
state() -> { available, leaseId, taskId, mode, createdAt }
```

应用启动恢复活动任务为 `APPLICATION_INTERRUPTED` 时，不恢复旧租约；任务重新开始时创建新租约。

### 5.2 SearchTransaction

在 `PkulawAdapter` 内增加检索事务逻辑。

提交前保存：

```text
previousUrl
previousPage
previousResultCount
previousResultIds
previousPageSize
previousGroupingMode
previousResultContainerToken
```

在提交关键词前，还要确认当前子库已经切换到目标子库，并且隐藏字段和当前库导航均与请求一致。

提交前给当前结果容器写入一次性 DOM token。新搜索完成后必须观察到：

- 旧 token 消失或结果容器被替换；
- 搜索框值等于请求关键词；
- 检索条件区域包含本次关键词和检索字段；
- `Keywords`、`Library`、`SearchKeywordType` 和筛选值与请求一致；
- 页码为 1；
- 页面已离开加载状态；
- 连续两个采样点得到相同 `SearchSession`。

相同关键词重复搜索时，不能仅依赖结果签名变化；必须依赖一次性 DOM token 的消失或容器替换来确认新请求已经完成。

超时后返回结构化错误，不返回旧结果。

### 5.3 FilterController

真实 V6 筛选器使用左侧树形链接，不是隐藏字段。

筛选流程：

1. 基础检索先完成并取得 `SearchSession`；
2. 在本次结果页定位年份、省份或时效性筛选树；
3. 按可见文本精确匹配目标项；
4. 点击后等待结果身份稳定；
5. 回读筛选状态或检索条件；
6. 不匹配则停止。

年份的选择范围仍为：

```text
all
single(startYear=endYear)
range(startYear..endYear)
```

每个年份组合都要独立取得 SearchSession。

### 5.4 ResultViewNormalizer

新增结果页规范化步骤：

- 定位 `input[name="group"][value="2"]`；
- 未选中时点击“不分组”；
- 等待 `ul.pagination` 和结果列表稳定；
- 校验当前页总行数与 `resultCount`、`pageSize`、`totalPages` 自洽；
- 记录 `groupingMode=ungrouped`。

若请求了每页条数：

- 操作 `.articleSelect`；
- 从 `#recordPage dd` 按文本精确选择；
- 等待页码重置为 1；
- 回读可见值、隐藏值和实际行数；
- 可见值和隐藏值必须等于请求值；
- 非尾页行数必须等于实际 page size，尾页行数必须等于剩余结果数；
- 不一致时返回 `PAGE_SIZE_NOT_APPLIED`。

### 5.5 SelectorStabilityProbe

新增通用稳定性检查：

```text
diagnose_for_operation(timeout=5s, interval=250ms, stableSamples=2)
```

行为：

- 短暂缺失后恢复：继续；
- 连续稳定后再次消失：重新开始计数；
- 在超时内未连续稳定：返回 `PAGE_SELECTOR_UNSTABLE`；
- 页面可识别但必需控件永久缺失：返回 `PAGE_SELECTOR_MISMATCH`。

该检查替代 `_ensure_search_page()` 中“一次诊断失败立即抛错”的逻辑。

### 5.6 DownloadIntentManager

每次点击下载前创建下载意图：

```text
intentId
taskId
kind: word | batch-catalog | batch-zip
expectedSuffixes
expectedSourceHost
createdAt
```

BrowserBridge 只接受与意图匹配的下载事件：

- started 和 completed 属于同一 `downloadId`；
- 扩展名或实际文件类型符合意图；
- 来源属于预期站点；
- 单任务下载次数不超过预期上限。

不匹配的下载只记录警告，不交给任务消费。

### 5.7 BatchExecutionContext

批量扫描完成后，为每个检索组合保存：

```text
SearchSession
resultSignature
batchKeys
lastPage
```

处理批次时按检索组合分组：

- 当前页面和 SearchSession 完全匹配且页码可到达目标批次页时，复用；
- 当前页在目标页之前时向前翻页；
- 当前页在目标页之后或页面身份不匹配时，只执行一次带事务门禁的重新搜索；
- 不再为每个批次重复同一搜索。

开始批次下载前核验：

- 查询、子库、年份、筛选值；
- 当前页；
- 行起止序号；
- 政策 ID 和标题集合；
- 勾选数量与预期完全一致。

## 6. 数据流

### 6.1 单篇

```text
acquire browser lease
-> preflight
-> guarded search
-> filter verification
-> ungroup
-> page-size verification
-> collect results
-> prepare items
-> open detail
-> validate detail identity
-> create word download intent
-> click Word
-> match started/completed download
-> validate OLE DOC or DOCX
-> atomic move
-> persist file/hash/checkpoint
-> release browser lease
```

### 6.2 批量

```text
acquire browser lease
-> guarded search and scan
-> scale estimate and optional confirmation
-> prepare batches
-> group batches by SearchSession
-> select exact rows
-> create catalog intent
-> download and validate catalog
-> create ZIP intent
-> download ZIP
-> validate ZIP and member identities
-> materialize and hash final files
-> atomically register batch artifacts and status
-> repeat
-> release browser lease
```

### 6.3 暂停和停止

暂停仍只在安全原子步骤完成后生效：

- 检索事务完成后；
- 单个筛选器应用完成后；
- 当前页读取完成后；
- 当前详情读取完成后；
- 单篇 Word 下载和校验完成后；
- 批量目录或 ZIP 完整下载和校验完成后；
- SQLite 状态写入完成后。

停止请求可以发生在等待中断点，但不得截断已经开始且需要完整性的下载或文件发布。

## 7. 错误模型

| 错误码 | 含义 | 行为 |
|---|---|---|
| `INVALID_WORD_FILE` | Word 扩展名、签名或内部结构无效 | 当前条失败，保留隔离样本 |
| `SEARCH_TRANSITION_UNCONFIRMED` | 无法证明新搜索已替换旧页面 | 自动暂停，等待人工处理 |
| `RESULTS_STALE` | 当前结果来自旧查询 | 不处理任何行 |
| `PAGE_SIZE_NOT_APPLIED` | 请求页大小未生效 | 停止当前检索 |
| `RESULT_GROUPING_UNAVAILABLE` | 无法得到不分组结果 | 停止当前检索 |
| `FILTER_NOT_APPLIED` | 年份、省份或时效性未生效 | 停止当前检索 |
| `PAGE_SELECTOR_UNSTABLE` | 控件在等待窗口内不稳定 | 自动暂停 |
| `PAGE_SELECTOR_MISMATCH` | 必需控件持续缺失 | 安全停止 |
| `DOWNLOAD_EVENT_MISMATCH` | 下载与意图不匹配 | 忽略该事件并告警 |
| `DOWNLOAD_INTENT_EXHAUSTED` | 下载次数超过任务预期 | 硬停止 |
| `BROWSER_LEASE_BUSY` | 其他浏览器任务持有租约 | 拒绝启动 |
| `DUPLICATE_START_REQUEST` | 同一任务重复启动 | 返回当前任务详情 |

所有错误写入 `task_events`、桌面日志和任务项错误，并在 UI 中显示可操作建议。

## 8. 文件与模块修改

| 模块 | 修改 |
|---|---|
| `engine/pkulaw_engine/policy.py` | `.doc/.docx` 分派校验和统一 Word 验证 |
| `engine/pkulaw_engine/single_task.py` | 使用 SearchSession、Word 下载意图和统一校验 |
| `engine/pkulaw_engine/batch_task.py` | 复用 SearchSession、按组合处理批次、下载意图 |
| `engine/pkulaw_engine/sources/pkulaw.py` | 检索事务、筛选、页大小、不分组、稳定性 |
| `engine/pkulaw_engine/sources/selector_profile.py` | 页面/操作相关门禁与稳定探测 |
| `engine/pkulaw_engine/sources/profiles/pkulaw-v6.json` | 新真实控件、解析策略和 profile 版本 |
| `engine/pkulaw_engine/browser_bridge.py` | 下载意图匹配和状态 |
| `engine/pkulaw_engine/application.py` | 全局浏览器租约、同模式并发保护 |
| `apps/desktop/src/main/download-manager.ts` | 事件补充可核验字段、Windows 文件名安全 |
| `apps/desktop/src/renderer/src/*` | 新错误、门禁状态、实际页大小与筛选状态 |

新增：

```text
engine/pkulaw_engine/browser_lease.py
engine/pkulaw_engine/download_intent.py
```

## 9. 测试设计

### 9.1 新增本地夹具

| 夹具 | 目的 |
|---|---|
| `stale-page5.html` | 新查询期间保留上一轮 5/5 页 |
| `same-url-slow-search.html` | URL 不变，旧 DOM 延迟被替换 |
| `same-query-rerun.html` | 相同关键词和结果集重新搜索 |
| `grouped-results.html` | 默认按效力位阶分组 |
| `custom-page-size.html` | 自定义条/页下拉和回读 |
| `filter-tree.html` | 年份、省份、时效性树形筛选 |
| `selector-flicker.html` | 控件短暂消失后恢复 |
| `selector-missing.html` | 必需控件持续缺失 |
| `download-out-of-order.html` | 无关下载先完成 |
| `batch-modal-real-shape.html` | 真实格式弹层和确认按钮 |
| `zip-resource-limit.html` | 压缩包大小、压缩比和成员数限制 |
| `excel-injection.xlsx` | CSV/XLSX 公式注入字符 |

### 9.2 单元测试

- `.doc` OLE、`.docx` ZIP、损坏文件和未知扩展名；
- SearchSession 身份比较和一次性 DOM token；
- 页码、结果数量、结果签名和页大小一致性；
- 筛选值、页码和结果身份 transition；
- 下载 intent 与 started/completed 配对；
- 全局浏览器租约互斥和释放；
- CSV 单元格公式转义；
- ZIP 解压大小、压缩比、成员数和路径安全；
- Windows 保留文件名。

### 9.3 集成测试

- 两个逐篇任务并发启动被拒绝；
- 两个批量任务并发启动被拒绝；
- 快速双击只创建一个任务；
- 旧第 5 页不得进入结果处理；
- 相同查询重跑不会死锁；
- 结果页短暂缺少 `libraryRoot` 后恢复；
- 结果页永久缺少 `libraryRoot` 时安全停止；
- 分组结果自动切换不分组；
- 每页条数实际值与请求值一致；
- 单条批量只执行必要搜索并下载 1 个 XLSX + 1 个 ZIP；
- 下载事件乱序时不消费无关文件。

### 9.4 故障注入

- 搜索提交后 Python 退出；
- 搜索等待期间应用强制退出；
- 目录下载完成后、SQLite 登记前退出；
- ZIP 下载完成后、CRC 校验前退出；
- CRC 校验完成后、文件移动前退出；
- 文件移动完成后、SQLite 提交前退出；
- SQLite 提交后、事件发送前退出；
- 磁盘只读、空间不足、文件被占用；
- 浏览器崩溃、Python 崩溃、VPN 断开。

每个故障点恢复后必须满足：

- 不重复下载已完成且已登记的产物；
- 不将旧文件覆盖为新批次；
- 数据库、文件、CSV/XLSX 和报告状态一致；
- 未完成文件位于临时目录或被明确标记为失败；
- 停止任务保留可恢复 checkpoint。

### 9.5 真实站点复验

先完成离线测试和阶段 2–6 烟测。通过后不再只做最小单条验收，而是按真实业务场景覆盖不同任务模式、关键词和产物格式。

真实站点验收矩阵：

| 场景 | 输入方式 | 子库 | 预期下载与产物 |
|---|---|---|---|
| 单篇精确标题 | 完整标题 | 中央法规 | 原生 Word、任务项元数据 |
| 单篇关键词 | 窄范围关键词 | 中央或地方 | 多条 Word 或受控停止记录 |
| 批量精确标题 | 一到两个完整标题 | 中央法规 | XLSX 目录、Word ZIP |
| 批量关键词 | 窄范围关键词 | 地方法规 | XLSX 目录、Word ZIP |
| 批量双库 | 分别能命中的中央与地方标题 | 中央 + 地方 | 每个组合独立批次和断点 |
| 目录格式兼容 | 单条精确标题 | 与单条批次相同 | 分别验证 XLSX、BIFF XLS、HTML-XLS |
| 数据治理 | 上述任务产物 | 不适用 | 单条 TXT、总目录 CSV、XLSX、任务报告 |
| 政府官网补全 | 北大法宝无精确结果的完整标题 | 精确标题模式 | 官方 HTML、截图、原生附件或正文整理标注 |

执行原则：

- 每个场景先只读确定标题、政策 ID、结果数和分页，再执行下载；
- 不在一个任务中混入多个宽泛关键词；
- 批量关键词必须使用可人工预估规模的窄范围关键词；
- 同一政策不因反复调试重复下载，失败重试也计入额度；
- 每次下载前后记录应用可见的额度信息；不可见时记录“未提供”；
- 出现额度、登录、滑块或验证码提示立即停止整个场景；
- 真实 `.doc` 与 `.docx` 以站点实际返回为准；站点未返回的格式不能用夹具结果冒充；
- 三种目录格式分别由真实站点生成，不得只用转换后的本地文件替代；
- 政府官网补全必须保持“精确标题且 `NO_EXACT_MATCH`”边界；
- 每个场景单独记录开始时间、下载次数、文件 SHA-256、任务状态和失败原因。

真实验收允许使用下载额度，但不执行无边界的“全部年份 + 宽泛关键词”尾页批量下载。单批次仍限制为 50 条，实际验收优先使用 1–10 条的小批次。

## 10. 实施阶段

### 阶段 A：测试基础设施和安全门禁

- 添加并发、租约、双击、下载 intent 和稳定性夹具；
- 在旧实现上运行，确认问题可以稳定复现；
- 不访问真实站点。

### 阶段 B：Word 和任务并发

- 修复 `.doc/.docx` 校验；
- 实现全局浏览器租约；
- 实现启动幂等；
- 运行相关单元测试和阶段 2/3 回归。

### 阶段 C：检索事务与真实控件

- 实现 SearchSession；
- 实现旧 DOM token；
- 实现年份、省份、时效性筛选；
- 实现每页条数和不分组；
- 扩充 V6 profile 并升级版本。

### 阶段 D：选择器稳定和批量执行

- 实现按页面/操作检查；
- 复用 SearchSession；
- 实现下载 intent 与批次身份核验；
- 扩展真实弹层夹具。

### 阶段 E：输出安全与恢复

- CSV/XLSX 公式转义；
- ZIP 资源上限；
- Windows 文件名；
- 跨阶段故障注入和原子发布。

### 阶段 F：全量回归和真实复验门禁

- Python、Vitest、类型检查、生产构建；
- 阶段 2–6 Electron 烟测；
- 便携包和干净目录验证；
- 小规模真实站点复验。

## 11. 版本与兼容

- 应用和引擎版本升级到 `0.2.1`。
- V6 selector profile 升级为 `pkulaw-v6-2026-11-01-3`。
- SQLite schema 保持 v5；SearchSession 和 lease 信息写入现有 JSON 字段。
- 旧任务中的字符串年份和数字 `maxPages` 继续兼容。
- 未带 SearchSession 的旧任务恢复时先执行受控重新检索，不直接沿用当前页面。

## 12. 验收标准

实现完成必须同时满足：

- 旧 DOM 不能创建任务项或触发任何下载；
- 真实 `.doc`、`.docx` 均通过文件格式校验；
- 每页条数、年份、省份和时效性均有回读证明；
- 真实结果完成“不分组”后分页可读取；
- 同一时间最多一个浏览器型任务；
- 下载事件与点击意图一一对应；
- 短时选择器重绘不导致任务失败，持续缺失则安全停止；
- 单条批量只生成一个 XLSX 和一个 ZIP；
- 批量 ZIP 通过 CRC、身份、安全路径和资源限制校验；
- CSV/XLSX 不含可执行公式；
- 所有故障注入后数据库、文件和报告一致；
- 应用退出后无 Electron/Python 残留；
- 全量测试和阶段 2–6 烟测通过；
- 真实站点结果与本地夹具结果分开记录，未执行项目不得标记为通过。

