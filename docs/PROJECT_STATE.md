# 项目状态

**当前日期：** 2026-11-02  
**当前阶段：** 0.2.2 阶段 I“真实站点全场景验收”进行中，正式碳中和任务因结果窗口截断主动停止  
**下一动作：** 先修复结果窗口完整性校验/按年份拆分、关键词同名多政策 ID 身份和断点重试，再继续 I-7 与 I-8  
**正式应用目录：** `apps/desktop`  
**Python 后端目录：** `engine`  
**技术探针目录：** `spikes/electron_drission`

## 0.2.2 阶段 I 中间状态（2026-11-02）

已完成并保留证据：

- I-0 至 I-6 的真实只读预检、单篇精确标题、窄关键词、单条批次、
  三种目录格式、地方法规小批次和中央+地方双库场景已有真实结果；
- 修复真实长详情正文误判和空 `WebContentsView` 白块；
- 修复真实结果页 `/law/search/RecordSearch` 缺少 `#rightContent`
  时的结果根识别；
- 修复结果页切地方库时因过早认定页面就绪而找不到搜索框；
- 修复内置网页初始 `3×3` 视口、导航后视觉缩放回到 50% 和屏幕外
  `.drag_text` 误报验证码；
- 人工验证码消失后清除旧暂停原因；
- Python 317 项通过、8 项按阶段 A 设计跳过；
- Vitest 68 项通过、1 项跳过；
- TypeScript 类型检查、Electron 生产构建和阶段 2、3、4、5、6 烟测通过。

正式 I-7“碳中和”逐篇任务
`2af9d793-b39e-471f-afcf-5d22f53a4e3b` 已主动停止：

- 预览为 2 个组合、19,061 条结果、39 页、每页 100；
- 中央库 1,837 条读满 19 页；
- 地方库页面显示 17,224 条，但分页器只给 20 页，每页 100 条时最多
  只能读取 2,000 条；
- 汇总只得到 3,837 个唯一行，却被当前实现视为扫描完成；
- 为避免把截断结果当作正式全量，在已创建 3,828 个任务项、完成 3 个
  下载后停止；
- SQLite `integrity_check=ok`，应用退出后无 Electron/Python 残留；
- 证据见
  `outputs/0.2.2碳中和全量真实验收/I7_正式碳中和逐篇/I7_result_window_blocker.json`
  和
  `outputs/0.2.2碳中和全量真实验收/I7_正式碳中和逐篇/I7_white_block_diagnostic.json`。

关键词全量任务还暴露：两个不同稳定政策 ID 使用相同标题时，当前按标题
分组并要求人工选择；关键词模式应按政策 ID 保留两条，精确标题模式才应
进行同名消歧。

阶段 I 当前重建的便携包：

- `outputs/北大法宝采集器_Windows便携版_0.2.2_20261102.zip`；
- SHA-256
  `e14f14fb522bf704994e52c671919d00df5a9801564aff38062c5dd8951c0983`；
- 源码包 SHA-256 记录在对应 `.sha256` 外部文件中；
- 便携包隔离启动和数据恢复验证通过。

## 已确认

- 保留 Python/DrissionPage 后端；
- 浏览器改为 Electron 内置 `WebContentsView`；
- 不使用外部 Edge；
- 不使用用户维护的固定 9333 端口；
- Electron 启动器负责在进程启动前注入随机本机调试端口；
- DrissionPage 通过该端口控制内置网页；
- Electron session 负责下载和新窗口；
- Python 负责采集策略、校验、断点和数据加工；
- 正常采集不依赖 Agent；
- 宽泛关键词不自动逐条回退政府官网；
- 只有精确标题模式可以进入政府官网补全；
- 最终保留 Word、TXT、CSV/Excel 和来源证据。

## 阶段 0 结果

已通过：

- DrissionPage 连接 Electron 内部 Chromium；
- 精确发现 `WebContentsView`；
- 页面标题和 DOM 读取；
- JavaScript 修改 DOM；
- Electron `will-download` 接管下载；
- Python 读取已完成的下载文件；
- 应用创建的弹出网页作为新的 `WebContentsView` 被发现；
- 持久 session Cookie 读取；
- 应用结束时不留下探针进程。

详细证据见 `PHASE_0_TEST_REPORT.md`。

## 阶段 1 结果

已完成：

- Electron + React + TypeScript 正式应用骨架；
- preload 白名单 API；
- Python JSON 行协议；
- 协议版本握手；
- 请求、响应、事件和结构化错误；
- 重复请求幂等处理；
- SQLite `tasks`、`task_items`、`task_events` 初始模型；
- 主进程自动启动、状态转发和优雅停止 Python；
- 桌面与 Python JSONL 日志；
- Python 单元测试；
- Electron/Python 集成测试；
- TypeScript 类型检查；
- Electron 生产构建；
- React 页面烟测和截图；
- 应用退出后无 Python/Electron 残留进程。

详细结果见 `PHASE_1_TEST_REPORT.md`。

## 阶段 2 结果

已完成：

- 主窗口可见且可交互的 `WebContentsView`；
- 启动器动态随机回环调试端口；
- Python/DrissionPage 自动连接和页面状态同步；
- 机构配置 JSON 存储；
- 机构独立持久 Session；
- 登录和滑块状态；
- 人工继续；
- Electron 下载接管与 Python 通知；
- 受控子视图；
- 页面崩溃自动恢复；
- Python 重启和浏览器重新连接；
- 本地测试机构服务；
- 浏览器测试界面。

详细结果见 `PHASE_2_TEST_REPORT.md`。

## 阶段 3 结果

已完成：

- 可替换来源适配器；
- 北大法宝页面适配器和外置选择器；
- 中央/地方/分别检索；
- 年份、省份、时效性和分页；
- 详情元数据；
- 单篇 Word 下载与 DOCX 校验；
- 标题规范化；
- 同名候选和用户选择；
- SQLite 任务项状态；
- 逐条断点、停止恢复和失败重试；
- 机构额度硬停止；
- 滑块人工等待；
- 逐篇任务界面。

详细结果见 `PHASE_3_TEST_REPORT.md`。

## 阶段 4 结果

已完成：

- 复用来源适配器进行批量检索和分页；
- 每批最多 50 条；
- 网站原生目录和 Word ZIP 下载；
- XLSX、BIFF XLS 和 HTML-XLS 校验；
- ZIP CRC、数量、空文件、重名和路径穿越校验；
- ZIP 到稳定政策 ID 的映射；
- SQLite schema v3 和 `task_batches`；
- 批次断点、停止恢复和失败批重试；
- 已验收批次隔离保护；
- 批量任务界面。

详细结果见 `PHASE_4_TEST_REPORT.md`。

## 阶段 5 结果

已完成：

- 逐篇 Word 和批量 ZIP 成员转逐条 UTF-8 TXT；
- DOCX 正文抽取；
- 旧版 DOC 校验及 antiword、LibreOffice、Microsoft Word 提取链；
- Unicode 规范化、空行清理和正文最短长度检查；
- 统一总目录 CSV 和 XLSX；
- 来源网址、检索时间、抓取方式、来源类型和核验状态；
- Word、TXT、官方 HTML 和官方截图 SHA-256；
- SQLite schema v4；
- `policy_documents`、`processing_runs`、`task_artifacts`；
- 北大法宝原件；
- 政府官网原生附件；
- 政府官网正文整理；
- 待核验；
- 精确标题模式下仅对 `NO_EXACT_MATCH` 启用政府官网补全；
- 宽泛关键词、登录、额度、验证码、同名歧义和来源异常不触发补全；
- 政府官网 HTML 与 PNG 截图证据；
- 官方补全期间临时显示 Electron 内置浏览器，终态自动收起；
- HTML 任务验收报告；
- SQLite、CSV、XLSX、Word、TXT、证据和哈希一致性复核；
- 阶段 5 数据处理界面；
- 本地 HTML/HTTP 和有效 Word/ZIP 夹具覆盖成功、缺失、异常来源和正文提取异常。

详细结果见 `PHASE_5_TEST_REPORT.md`。

## 阶段 6 结果

已完成：

- 统一任务中心；
- 任务模式、状态和关键词筛选；
- 逐条状态、错误原因、阶段和候选详情；
- 同名候选选择；
- 开始、停止、人工继续和失败重试；
- 批量断点查看；
- 任务输出目录和最新任务产物打开；
- 数据与报告页标题搜索、来源筛选和状态筛选；
- Word、TXT、来源证据、CSV、XLSX 和报告的受控打开；
- 设置与诊断页；
- 启动预检、SQLite 完整性、依赖、日志、路径、网络和显示缩放诊断；
- Python 崩溃后一次自动恢复；
- 浏览器按后端 PID 增量重连，防止并发重连选错页面；
- 断网、页面崩溃、后端崩溃和异常下载错误路径；
- 中文、空格、长路径和非法输出路径处理；
- Electron/打包后端标准输入输出的 UTF-8 统一；
- PyInstaller Python 后端；
- Electron 免安装发布包；
- 随机回环端口的一键启动器；
- Windows ZIP、SHA-256 和独立源码/构建脚本包；
- 依赖锁定、安装卸载、使用、故障排查、数据恢复和复现文档；
- 发布包直接启动和实际一键启动器两轮隔离验证；
- 高 DPI（125%）、首次启动、中文/空格长路径、断网和崩溃恢复测试。

详细结果见 `PHASE_6_TEST_REPORT.md`。

## 0.2.0 任务 1 结果

已完成：

- WebContentsView 在 HTTP/HTTPS `dom-ready` 后建立 Python 绑定；
- 连接参数包含实际目标 URL、Session、网页视图 ID 和一次性目标标记；
- Python 不再只按入口 URL 域名筛选标签页；
- Python 排除应用自身 `file://` 渲染页；
- 目标标记通过 `window.name` 写入，网页跨域跳转后仍保留在当前网页目标；
- 已绑定标签页保存在 BrowserBridge 中，后续跨域导航不重新按入口域名寻找；
- 精确 URL 作为目标标记丢失时的固定回退；
- 存在多个无法确认的外部页面时不猜测目标；
- 页面加载事件只有在 Python 已绑定后才刷新网页状态；
- BrowserHost 新增 `viewLoaded`、`targetBound` 和 `targetUrl`；
- 浏览器界面分别显示后端连接、网页加载、Python 绑定和机构认证；
- 保留并按后端 PID 防止重复并发重连；
- reconnect 前重新写入目标标记，覆盖 Python 重启后的重连场景；
- 阶段 2 增加慢加载和跨主机跳转夹具；
- 0.1.0 源码基线已保存在 `build/baseline-0.1.0/`。

验证：

- 本地夹具正常绑定；
- 慢加载页面绑定；
- 从 `127.0.0.1` 跨到 `localhost` 后仍绑定正确目标；
- WebContentsView 重建；
- Python 重启后重新绑定；
- renderer 崩溃恢复；
- 应用页面 `file://` 不会被选中；
- 存在多个外部目标时不猜测；
- 阶段 2 和阶段 2–6 全量 Electron 烟测通过；
- 应用退出后无 Electron/Python 残留。

详细结果见 `0.2.0_TASK_1_TEST_REPORT.md`。

## 0.2.0 任务 2 结果

已完成：

- 将本地夹具选择器迁移为独立 `fixture` profile；
- 建立独立 `pkulaw-v6` profile；
- V6 首批只写入已确认的 `#txtSearch`、`#btnSearch` 和 `#accurate`；
- 根据页面标记识别 profile，不按任务模式、端口或开发阶段猜测；
- 关键控件按“稳定 ID → CSS/属性/按钮文字 → 有限表单结构”解析；
- 每次诊断返回 profile ID、版本、解析策略、命中规则和缺失控件；
- 关键输入框或检索按钮缺失时返回 `PAGE_SELECTOR_MISMATCH`，不提交检索；
- `browser.selector-diagnostics` 协议、BrowserHost 状态和浏览器/诊断界面已接入；
- 单篇、批量和任务中心启动按钮要求目标已绑定、已认证且选择器诊断通过；
- PyInstaller 后端包已包含 `sources/profiles`；
- 本地阶段 2 增加稳定、兜底、多候选、缺输入和缺按钮五类诊断夹具；
- 修复任务停止后停留在详情页时，恢复阶段无法重新识别夹具 profile 的回归；
- 未知页面先检查登录、滑块和额度状态，再回机构入口重新识别，仍失败时失败关闭。

验证：

- `fixture` 和 `pkulaw-v6` 的 profile ID、版本和已知选择器相互独立；
- 多候选时优先稳定 ID；
- 稳定 ID 失效时使用固定 CSS 兜底；
- 缺失输入框或检索按钮时诊断不通过，提交前停止；
- 详情页恢复会重新识别 `fixture` profile 并返回检索页；
- Python、桌面单测、类型检查和阶段 2–6 Electron 烟测全部通过；
- 应用退出后无 Electron/Python 残留；
- 本任务未访问真实北大法宝，未执行真实搜索或下载。

详细结果见 `0.2.0_TASK_2_TEST_REPORT.md`。

## 0.2.0 任务 3 结果

已完成：

- 在 Electron 内置浏览器、已认证且 Python 已绑定的真实清华校机构会话中完成只读检查；
- 只用一条精确标题“中华人民共和国国防动员法(2026修订)”执行一次检索；
- 确认第一页只有 1 条标题完全一致结果；
- 读取并核对结果序号、标题、政策 ID、链接、机关、文号、公布日期、施行日期和时效性；
- 只打开该一条唯一详情页；
- 核对 `.fulltext-wrap > .content > h2.title` 标题、`#divFullText.fulltext` 正文和 `.fields` 六项元数据；
- 确认详情 Word 入口为 `#downloadword`，但没有点击；
- 读取地方法规高级检索页面，确认省份分类使用 `IssueDepartment`，未提交高级检索；
- V6 profile 升级为 `pkulaw-v6-2026-11-01-2`；
- 补齐子库、年份、省份、时效性、不分组、每页条数、结果卡、分页、详情和访问状态选择器；
- 新增搜索、结果、分页、详情和访问状态的诊断分组；
- 新增当前页类型识别和按页面类型的关键项检查；
- 新增 `browser.page-audit` 只读审计协议；
- 新增脱敏 HTML 防线，删除脚本、样式、隐藏凭据、账号、用户 ID、IP、联系方式和机构标识；
- 新增 V6 搜索、结果、多页、详情和缺失检索按钮的本地夹具；
- 浏览器页和设置与诊断页可展开查看关键控件、解析策略和缺失原因；
- 阶段 2 烟测新增 V6 搜索页、真实第一页结构、多页、详情和缺检索按钮审计。

真实只读验证：

- 真实精确标题检索提交次数：1；
- 第一页结果行：1；
- 唯一详情页：1；
- Word 点击次数：0；
- 下载开始/完成次数：0；
- 当前会话未出现登录、滑块或额度异常；
- 真实结果为单页，因此现场没有分页控件；多页由脱敏夹具验证。

验证：

- Python 43 项测试通过；
- Vitest 3 个文件、5 项测试通过；
- TypeScript node/web 类型检查通过；
- 阶段 2–6 Electron 全量烟测通过；
- V6 结果第一页、多页分页、详情元数据、Word 入口存在性和缺检索按钮门禁通过；
- 应用退出后无测试 Electron/Python 残留。

证据：

- `outputs/0.2.0任务3_真实只读证据_2026-11-01/真实只读适配证据.json`
- `outputs/0.2.0任务3_真实只读证据_2026-11-01/真实搜索首页截图_2026-11-01.png`
- `outputs/0.2.0任务3_真实只读证据_2026-11-01/真实详情页只读截图_2026-11-01.png`
- `outputs/0.2.0任务3_真实只读证据_2026-11-01/搜索页脱敏快照.html`
- `outputs/0.2.0任务3_真实只读证据_2026-11-01/结果第一页脱敏快照.html`
- `outputs/0.2.0任务3_真实只读证据_2026-11-01/详情页脱敏快照.html`

详细结果见 `0.2.0_TASK_3_TEST_REPORT.md`。

## 0.2.0 任务 4 结果

已完成：

- 新增公共 `TaskControl`；
- 支持 `pause_requested`、`resume_requested`、`stop_requested`；
- 支持 `wait_if_paused()`、`check_stop()`、`manual_wait()` 和候选版本等待；
- 新增任务状态 `pausing`、`paused`、`stopping`；
- 手动暂停只在安全原子步骤结束后生效；
- 单篇任务在检索、翻页、详情读取、Word 下载和返回结果页前检查暂停/停止边界；
- Word 下载、DOCX 校验、文件落盘、SHA-256、任务项完成和 checkpoint 写入完成后，才进入暂停；
- 手动继续前重新核验浏览器绑定、登录、滑块和选择器 profile；
- 自动暂停继续保留 `single.continue`，与手动 `single.resume` 分离；
- 重复暂停、继续、停止操作保持幂等；
- 暂停期间不进入下一网站操作；
- 停止在当前安全步骤后退出，并保留已完成文件、任务项和 checkpoint；
- 同名候选等待继续使用自动等待，不能被普通“继续”错误放行；
- Engine 启动时把残留活动任务标记为 `stopped`，错误码 `APPLICATION_INTERRUPTED`；
- 单篇界面和任务中心显示暂停原因、当前阶段和断点；
- 自动暂停与手动暂停采用不同状态和文案；
- 任务 4 完成时批量和数据处理仍保留旧行为；该接入已在任务 5 完成。

阶段 3 验证：

- 自动登录暂停出现并可继续；
- 自动滑块暂停继续通过；
- 手动暂停在完成 3 条后生效；
- 暂停后等待期间完成数不增长；
- 重复暂停不产生新的状态更新；
- 继续后 `scannedPages` 不变，没有从头重新检索；
- Word 延迟下载测试确认下载和校验完成后才暂停；
- 停止前完成 2 条，恢复后完成 12 条；
- 页面重定向、额度硬停止、失败重试和同名版本消歧保持通过。

验证：

- Python 50 项测试通过；
- Vitest 3 个文件、5 项测试通过；
- TypeScript node/web 类型检查通过；
- 阶段 2、3 Electron 烟测通过；
- Python 进程异常终止并重启后，活动任务自动转为 `APPLICATION_INTERRUPTED`；
- 应用退出后无测试 Electron/Python 残留。

详细结果见 `0.2.0_TASK_4_TEST_REPORT.md`。

## 0.2.0 任务 5 结果

已完成：

- 批量 `BatchTaskManager` 使用公共 `TaskControl`；
- 数据处理 `DataProcessingManager` 使用公共 `TaskControl`；
- 新增 `batch.pause`、`batch.resume`、`data.pause`、`data.resume` 协议及 Electron IPC/preload 接口；
- 批量任务在预检、检索、页读取、选批、目录下载/校验、ZIP 下载/CRC 校验和批次提交边界响应控制；
- 数据处理在当前政策 Word/TXT/证据/SQLite 写入完成后的政策边界响应暂停和停止；
- 手动暂停进入 `pausing`，完成当前安全步骤后才进入 `paused`；
- 暂停/恢复/停止信号与 SQLite 状态更新在公共控制器锁内完成，避免 worker 先写 `paused` 后被管理线程晚写的 `pausing` 覆盖；
- 暂停期间批量任务不发起新的网站请求，数据处理不写入新的政策文件；
- 批量和数据处理恢复前重新核验网页绑定、登录、滑块和选择器识别；
- 批量 checkpoint 保存关键词、子库、页码、批次 key/序号、行范围、目录/ZIP 文件和当前动作；
- 数据处理 checkpoint 保存当前 policy key、当前动作和已完成数量；
- 停止在当前安全步骤结束后退出，已验收批次、政策文件、TXT、证据和 checkpoint 保留；
- 批量任务从停止状态恢复时合并旧 checkpoint，不覆盖已完成批次；
- 批量页面中的暂停、继续、停止、重试和自动暂停提示，与单篇、数据处理使用同一套状态文案和安全边界语义；
- 任务中心补齐批量暂停/继续/停止操作映射，并显示批次断点；
- 应用启动时同时恢复异常中断的单篇/批量任务和数据处理运行；
- 保留阶段 5 来源治理、政府官网回退和 SHA-256 边界不变。

验证：

- Python 52 项测试通过；
- Vitest 3 个文件、5 项测试通过；
- TypeScript node/web 类型检查通过；
- 阶段 2 浏览器、下载、绑定和恢复烟测通过；
- 阶段 3 单篇检索、分页、下载、停止/暂停/恢复、额度和滑块烟测通过；
- 阶段 4 批量自动登录/滑块暂停、目录校验后暂停、ZIP/CRC 校验和批次提交后暂停、暂停幂等、停止后恢复且不重复批次均通过；
- 阶段 5 单篇政府官网补全、批量 ZIP 数据处理、逐政策手动暂停、暂停期间文件不增长、恢复从断点继续、停止保留完整 checkpoint 和数据文件均通过；
- 应用退出后无 Electron/Python 残留。

详细结果见 `0.2.0_TASK_5_TEST_REPORT.md`。

## 0.2.0 任务 6 结果

已完成：

- SQLite schema 从 v4 升级到 v5，新数据库直接为 v5；
- `tasks.archived_at` 和 `tasks.archive_reason` 自动迁移；
- 普通任务列表默认隐藏归档任务，回收站可单独查看；
- 任务中心支持单选、多选和全选当前筛选结果；
- 支持批量归档、恢复和永久删除；
- 活动任务和活动数据处理运行拒绝归档/删除；
- 清理预览只列出数据库登记的来源文件、政策 Word/TXT/证据、批次目录/ZIP/报告和任务产物；
- 清理预览标记不存在、共享、路径穿越、输出根目录和受保护路径；
- Electron 使用 `shell.trashItem` 将登记文件移入 Windows 回收站；
- 父级输出根、磁盘根、用户主目录、共享路径和未登记文件不会被直接递归删除；
- 文件清理失败时保留 SQLite 记录，可在预览中重试；
- 删除记录始终使用 SQLite 外键级联清理任务项、批次、事件、处理运行、政策文档和产物登记；
- 支持“仅删除记录、保留输出文件”和“移入 Windows 回收站后删除记录”两种动作；
- 阶段 2 增加真实 preload/IPC 生命周期烟测，阶段 5 回归保持通过。

验证：

- Python 58 项测试通过；
- Vitest 4 个文件、10 项测试通过；
- TypeScript node/web 类型检查通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 阶段 6 响应式回归通过：125% 缩放、866px 内部宽度无横向溢出；
- 阶段 2 实际调用 Windows 回收站：3 个登记文件移入，输出根目录和同目录未登记文件保留；
- SQLite v4→v5 和新建 v5 数据库迁移通过；
- 归档后普通列表不可见、回收站可见，恢复后重新可见；
- 任务删除后任务项、批次、处理运行、政策文档和产物记录级联清除；
- 清理失败时记录保留；
- 应用退出后无 Electron/Python 残留。

详细结果见 `0.2.0_TASK_6_TEST_REPORT.md`。

## 0.2.0 任务 7 结果

已完成：

- 新增共享 `YearSelection` Python/TypeScript 类型；
- 支持全部年份、指定年份和年份区间；
- 年份可选范围为 2000 至当前年份；
- 区间按升序展开为 `起始年份 → 结束年份`；
- 单篇和批量共用“关键词 × 子库 × 年份”组合生成；
- checkpoint 保存年份模式、起止年份、当前年份、年份索引、组合索引和组合总数；
- 每年保存待执行、运行中、无结果、完成、暂停或失败状态；
- 某一年无结果时继续下一年；
- 手工暂停保留当前年份、页码和组合位置，恢复后不从头重新检索；
- 批量继续时从批次中保存的年份恢复当前页，不跨年度误勾选；
- 年份文件名加入年度标识，避免不同年度相同行号覆盖批次文件；
- 同一年度及跨年度结果按稳定政策 ID 去重；
- 年份筛选不存在或指定年份选项缺失时返回 `YEAR_FILTER_UNAVAILABLE` 并安全停止；
- 缺少年份筛选页面中的“全部年份”检索仍可正常执行；
- 旧任务字符串 `year` 自动解释为全部年份或单一年份；
- 单篇和批量表单新增共享年份三模式控件；
- 单篇和批量任务页显示年度执行统计；
- 保持任务 5 暂停/恢复/停止语义和任务 6 归档/删除边界不变。

验证：

- Python 62 项测试通过；
- Vitest 4 个文件、10 项测试通过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 2018–2021 区间升序展开；
- 2000 和当前年份边界、起始年份反序、非四位年份和越界年份拒绝；
- 单篇 2019 无结果后继续 2020、2021，最终 4 条政策；
- 单篇年份断点保留当前年份和页码，恢复后完成 4 条；
- 缺少年份筛选页面返回 `YEAR_FILTER_UNAVAILABLE`；
- 批量“中央 + 地方”双库、2020–2021 区间完成；
- 中央 2020/2021 各 2 条，地方两年均无结果，最终去重 4 条；
- 应用退出后无 Electron/Python 残留。

详细结果见 `0.2.0_TASK_7_TEST_REPORT.md`。

## 0.2.0 任务 8 结果

已完成：

- 新增共享 `PageSelection`，支持 `last` 和 `limit`；
- 新任务默认扫描到尾页，指定上限最小为 1；
- 旧数字 `maxPages` 自动解释为 `mode=limit`；
- 单篇和批量任务均按“关键词 × 子库 × 年份”分别读取真实总页数；
- checkpoint 保存当前组合、总页数、当前页、已扫描页、未扫描页和各组合总页数；
- 指定上限达到后进入 `waiting_manual`，错误码为 `PAGE_LIMIT_REACHED`；
- 用户可选择继续到尾页或停止；继续决定持久化为 `pageMode=last`，重启后不会退回旧上限；
- 翻页未前进、页码重复、总页数下降、总页数无效、页码越界和相邻页结果签名相同均安全暂停；
- DrissionPage 在页面刷新期间出现短暂上下文丢失时等待稳定后再读取，不直接把任务判为失败；
- 批量任务扫描前保存结构估算，首批结果后保存投影，扫描完成后保存实际值；
- 首批投影达到 20 页、500 条政策或 5 个 50 条批次时进入 `BATCH_SCALE_CONFIRMATION_REQUIRED`；
- 规模确认前不创建或下载批次；
- 单篇、批量和任务中心均提供“继续到尾页”“确认规模并继续”和停止入口；
- 协议和 `engine.ready` 新增 `tasks.pagination.v1` 能力标记。

验证：

- Python 70 项测试通过；
- Vitest 4 个文件、10 项测试通过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 默认尾页夹具扫描 2 页；
- 上限 1 页夹具正确保存已扫描 1 页、未扫描 1 页、总页数 2 页；
- 继续到尾页后扫描完成，停止后所保留年度、页码和组合断点可恢复；
- 两个年度组合分别记录各自 2 页总页数；
- 501 条规模夹具触发首次投影 `11 页 / 501 条 / 11 个预计批次`，确认后实际扫描 11 页且未创建批次；
- 应用退出后无本项目关联的 Electron/Python 残留进程。

详细结果见 `0.2.0_TASK_8_TEST_REPORT.md`。

## 0.2.0 任务 9 结果

已完成：

- 根项目、Electron 应用、Python 引擎和发布元数据统一为 `0.2.0`；
- SQLite v1–v4 升级到 v5 前使用 SQLite 在线备份 API 保存 `.pre-v5-*.bak`；
- Windows ACL 只读目录、非目录输出路径、中文/空格/长路径验证；
- Python 单元、协议、存储和迁移测试；
- Vitest、TypeScript 类型检查和 Electron 生产构建；
- 阶段 2、3、4、5、6 Electron 烟测；
- SQLite schema v5 和 `integrity_check=ok`；
- Word、TXT、官方 HTML、官方截图、来源 JSON 和总目录/报告的真实 SHA-256 复核；
- 数据目录和日志目录 IPC 打开入口；
- 高 DPI 125%、断网、Python 崩溃、浏览器重连和中文协议往返；
- 重新构建 0.2.0 PyInstaller Python 后端和 Electron 生产版本；
- 生成 Windows 自包含便携 ZIP、版本信息、依赖锁定和部署文档；
- 在长中文/空格隔离目录验证便携包直接启动；
- 验证一键启动器、随机回环端口、bundled Python 和中文协议；
- 验证缺少内置 Python 后端时失败关闭并输出明确原因；
- 验证强制退出、重启恢复、SQLite integrity 和进程清理；
- 校验便携 ZIP SHA-256 sidecar、发布版本、schema v5、开发依赖和开发产物排除情况。
- 在用户明确同意下完成中国地质大学（武汉）真实站点精确标题第一页只读验收；
- 真实入口下后端、网页、Python 绑定、机构认证和选择器诊断全部通过；
- 精确标题“中华人民共和国国防动员法(2026修订)”命中 1 条；
- 读取稳定政策 ID、机关、文号、公布日期、施行日期、时效性和效力位阶；
- 未打开详情页、未点击 Word、未产生下载事件、未导出 Cookie。

验证：

- Python 70 项测试通过；
- Vitest 4 个文件、11 项测试通过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5、6 烟测全部通过；
- 便携验证报告 `ok=true`；
- 应用与 bundled Python 版本均为 `0.2.0`；
- SQLite schema 为 v5，integrity 为 `ok`；
- 隔离环境直接启动、一键启动器和强制退出恢复均通过；
- 测试和便携验证结束后无 Electron/Python/法宝工作台残留进程。
- 真实只读验收证据 HTML、截图和 SHA-256 一致；
- 真实只读验收后应用正常关闭，Python 退出码 0，无残留进程。
- 用户后续授权后完成 1 个计划内真实 Word 下载传输、5 页真实尾页只读扫描、分页上限门禁和批量规模门禁；
- 真实分页上限冷启动第一次读取到旧页 `5/5`，绕过门限并触发 2 个计划外 `.doc` 下载后立即停止；
- 同一查询预热到第 1 页后，`maxPages=1` 正确进入 `PAGE_LIMIT_REACHED`；
- 真实批量规模门禁正确投影 957 页、19,136 条和 383 个 50 条批次，确认前 0 批次、0 任务项、0 下载；
- 单条真实批次建立后，由于页面重渲染期间选择器瞬时缺少 `libraryRoot`，原生目录和 Word ZIP 均在下载前失败；
- 3 个计划内或计划外下载均为有效 OLE `.doc`，但单篇校验器只接受 `.docx`，因此真实 Word 应用终态未通过。

发布候选：

- `outputs/北大法宝采集器_Windows便携版_0.2.0_20261101.zip`
- SHA-256：`fdc6429fba8834ea2e907a607d7f9a8e550874740dbe0b0295b08344ba357ee0`
- `outputs/北大法宝采集器_Windows便携版_0.2.0_20261101.zip.sha256`
- `outputs/北大法宝采集器_源码与构建脚本_20261101.zip`
- 源码 ZIP 的最终 SHA-256 见同名 `.sha256`。

详细结果见：

- `0.2.0_TASK_9_TEST_REPORT.md`
- `outputs/0.2.0发布验收报告_2026-11-01.md`
- `outputs/0.2.0任务9_真实只读证据_2026-11-01/真实站点只读验收记录.md`
- `outputs/0.2.0真实下载与批量验收记录_2026-11-01.md`
- `outputs/0.2.0任务9_真实流程验收_2026-11-01/真实流程验收数据库证据.json`

## 0.2.1 阶段 A 结果

已完成：

- 建立 Python `coverage.py` 与 Vitest V8 覆盖率入口；
- 建立 `docs/0.2.1_FEATURE_ACCEPTANCE_MATRIX.md`；
- 增加 10 个真实形状 V6 故障夹具；
- 阶段 2 Electron 烟测实际加载并审计 15 个 V6 HTML 夹具；
- 正常回归保持通过：Python 80 项（9 个已知缺陷用例跳过）、Vitest 11 项（1 个已知缺陷用例跳过）、类型检查通过；
- 已知缺陷红基线单独运行并验证预期失败；
- Python 关键模块覆盖率基线为 29.55%；
- Electron DownloadManager 覆盖率基线为 0%；
- 新发现分页“下一页”祖先 `li.disabled` 未被 `nextAvailable` 正确识别。

验证与证据：

- `docs/0.2.1_PHASE_A_TEST_REPORT.md`
- `outputs/0.2.1阶段A红基线/phase-a-known-gap-baseline.txt`
- `engine/coverage.json`
- `apps/desktop/coverage/coverage-summary.json`
- `apps/desktop/runtime/smoke-stage2/smoke-report.json`

## 0.2.1 阶段 B 结果

已完成：

- 新增统一入口 `validate_downloaded_word(path)`；
- OLE `.doc`、OOXML `.docx` 和未知扩展名按实际格式分派；
- 单篇任务不再固定使用只接受 `.docx` 的校验器；
- 单篇 `.doc` 夹具完成正式文件移动和 SQLite 登记；
- 伪 DOCX 返回明确 `INVALID_WORD_FILE`；
- 校验失败文件移动到 `99_失败样本/Word校验失败`；
- 失败样本保存原路径、隔离路径、扩展名、大小、SHA-256 和原因；
- 3 个真实 `.doc` 证据样本加入可重复离线测试；
- 阶段 2、3、5 Electron 烟测通过；
- 全量 Python 82 项（8 个已知缺陷用例跳过）、Vitest 11 项（1 个跳过）和类型检查通过。

验证与证据：

- `docs/0.2.1_PHASE_B_TEST_REPORT.md`
- `engine/tests/fixtures/real-word-doc/`
- `apps/desktop/runtime/smoke-stage2/smoke-report.json`
- `apps/desktop/runtime/smoke-stage3/smoke-report.json`
- `apps/desktop/runtime/smoke-stage5/smoke-report.json`
- `engine/coverage.json`

## 0.2.1 阶段 C 结果

已完成：

- 新增进程级 `BrowserLeaseManager`；
- 任意时刻最多一个逐篇、批量或浏览器型数据处理任务持有内置浏览器；
- 租约包含 `leaseId`、`taskId`、`mode`、`createdAt` 和 `updatedAt`；
- 应用层在启动工作线程前原子取得租约；
- 单篇、批量和浏览器型数据处理共用同一租约；
- 纯本地数据处理不占用浏览器租约；
- 任务线程成功、失败、停止或异常结束时统一释放租约；
- 暂停和继续期间保持同一租约，不创建第二个所有者；
- 同一任务重复启动返回当前详情，不创建第二个工作线程；
- 不同任务并发启动返回 `BROWSER_LEASE_BUSY`；
- `task.create`、采集启动、采集恢复和浏览器型数据处理支持 `operationId` 幂等键；
- 同一 `operationId` 的并发创建只产生一个 SQLite 任务；
- 同一任务在完成、暂停、等待人工或运行中重复启动不会重新执行；
- Python 进程重启后租约只存在于新进程内存，不恢复旧租约；
- `browser.state`、`browser.lease` 和租约事件向 Electron 暴露当前持有信息；
- BrowserHost、逐篇采集、批量采集、任务中心、数据处理和设置诊断页面显示租约所有者与模式；
- 租约忙碌时，新建采集和需要浏览器的数据处理按钮禁用并显示准确原因；
- 前端增加同步双击锁，配合后端 `operationId` 防止重复任务与重复工作线程。

验证：

- Python 100 项测试通过（8 个其他阶段已知缺口用例跳过）；
- Vitest 5 个文件、14 项通过（1 个其他阶段已知缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测通过；
- 阶段 5 数据处理回归烟测通过；
- 阶段 3 烟测验证逐篇租约显示、同任务重复启动幂等、不同任务拒绝和双击创建幂等；
- 阶段 4 烟测验证批量租约显示和不同批量任务拒绝；
- 应用退出后无 Electron/Python 残留进程。

详细结果见 `docs/0.2.1_PHASE_C_TEST_REPORT.md`。

## 0.2.1 阶段 D 结果

已完成：

- 新增 `SearchRequestIdentity`、`SearchSession` 和 `SearchTransaction`；
- 每次提交新检索生成独立 `searchId`；
- 提交前记录旧 URL、页码、结果数、稳定结果身份、页大小、分组状态和一次性容器标记；
- 提交前确认目标子库已切换，并回读关键词、检索字段、子库和条件区域；
- 新检索必须移除旧结果容器中的一次性标记；
- 新结果必须页码为 1、离开加载状态，并连续两次采样得到一致 `SearchSession`；
- 相同 URL、相同关键词和相同结果集重新检索仍能确认新事务；
- 搜索框、条件区域、隐藏关键词、子库、检索字段、页码和加载状态任一不一致时失败关闭；
- 超时返回 `SEARCH_TRANSITION_UNCONFIRMED`，任务进入人工继续；
- 结果与请求不一致时返回 `RESULTS_STALE`，不消费当前 DOM；
- 单篇和批量的搜索结果只能通过当前 `SearchSession` 进入分页、任务项或批次；
- checkpoint 保存完整 `searchIdentity`、`searchId`、页码、总页数、实际页大小、分组模式和结果签名；
- 第一页检索完成后立即写入 checkpoint，再进入暂停/停止安全边界；
- 旧 checkpoint 缺少 `SearchSession` 时记录 `MISSING_SEARCH_SESSION`，并执行一次受控重新检索；
- 分页产生的每一页保持同一 `searchId`，但更新页码、结果数和结果签名；
- 旧 DOM 或检索事务失败时不会创建任务项、打开详情或触发下载；
- 浏览器页面连接短暂断开时刷新 DrissionPage 目标句柄一次，避免连续导航后的目标失联；
- 保留阶段 C 全局浏览器租约、启动幂等、任务控制、年份、分页上限、来源治理和批次下载语义。

验证：

- Python 110 项测试通过（8 个其他阶段已知缺口用例跳过）；
- Vitest 5 个文件、14 项通过（1 个其他阶段已知缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测通过；
- 阶段 3 验证单篇 `SearchSession` 写入 checkpoint、旧页不绕过 `maxPages=1`、暂停/停止恢复和 Word 下载；
- 阶段 4 验证批量检索身份、第 2 页实际页大小、目录/ZIP 下载和失败批次恢复；
- 应用退出后无 Electron/Python 残留进程。

详细结果见 `docs/0.2.1_PHASE_D_TEST_REPORT.md`。

## 0.2.1 阶段 E 结果

已完成：

- V6 selector profile 升级为 `pkulaw-v6-2026-11-01-3`；
- 年份、省份、时效性筛选根节点和候选节点选择器已补齐；
- 筛选文本按可见文本/`title`/`node_name` 精确匹配，并去除尾部的 `(数量)`；
- 每次筛选后生成新的稳定 SearchSession，旧观察结果不再直接使用；
- 每步只验证当前已应用筛选，最终统一要求年份、省份和时效性全部匹配；
- 筛选项缺失、文本不精确或应用后回读不一致时失败关闭；
- 年份缺失继续保留 `YEAR_FILTER_UNAVAILABLE`；
- 省份和时效性无法应用返回 `FILTER_NOT_APPLIED`；
- 可见 `.articleSelect`、`#recordPage dd` 和隐藏 `Pager.PageSize` 已接入；
- 每页条数支持 10、20、50、100，要求可见值、隐藏值和实际结果行数一致；
- 非尾页行数必须等于页大小，尾页行数必须等于剩余结果数；
- 页大小未生效返回 `PAGE_SIZE_NOT_APPLIED`；
- `input[name="group"][value="2"]`“不分组”已接入；
- 分组切换后重新等待分页器和结果列表稳定；
- 分组失败返回 `RESULT_GROUPING_UNAVAILABLE`；
- 基础检索后的顺序固定为“年份/省份/时效性筛选 -> 页大小 -> 不分组 -> 最终稳定 SearchSession”；
- SearchSession 新增实际年份、实际省份、实际时效性、实际页大小和分组模式；
- 单篇和批量任务必须通过带最终筛选门槛的 SearchSession 才能消费结果；
- checkpoint 保存实际筛选值、实际页大小和 `groupingMode=ungrouped`；
- 阶段 D 的一次性容器标记和连续稳定采样继续用于每次筛选、页大小和分组变化；
- 暂停、停止和恢复继续沿用现有安全原子边界；
- 保留阶段 C/D 的浏览器租约、启动幂等和来源治理语义。

验证：

- Python 118 项测试通过（8 个其他阶段已知缺口用例跳过）；
- Vitest 5 个文件、14 项通过（1 个其他阶段已知缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测通过；
- 阶段 2 验证 V6 profile 版本、筛选树、可见页大小和不分组控件诊断；
- 阶段 3 验证实际年份 `2024`、省份“全部省份”、时效性“现行有效”、每页 20 条和不分组；
- 阶段 4 验证批量实际页大小 50、第 2 页、全部筛选值和不分组；
- 应用退出后无 Electron/Python 残留进程。

详细结果见 `docs/0.2.1_PHASE_E_TEST_REPORT.md`。

## 0.2.1 阶段 F 结果

已完成：

- 新增操作级选择器稳定探针 `diagnose_for_operation()`；
- 默认等待 5 秒、250 毫秒采样、连续两次稳定才通过；
- 控件短暂缺失后恢复时自动继续；
- 控件稳定后再次消失时重新计数；
- 页面可识别但关键控件持续缺失返回 `PAGE_SELECTOR_MISMATCH`；
- 页面控件反复出现和消失、始终无法形成稳定样本返回 `PAGE_SELECTOR_UNSTABLE`；
- 搜索、读取结果、选择批次、目录/ZIP 触发与提交、详情读取和 Word 下载分别使用操作门禁；
- 结果页操作不再要求全局搜索控件同时存在；
- 结果页短暂缺少 `libraryRoot` 时不影响读取结果、选择批次或下载门禁；
- 新增 `engine/pkulaw_engine/download_intent.py`；
- 每次 Word、批量目录、批量 ZIP 和政府官网 Word 附件下载前创建独立 intent；
- intent 保存 `intentId`、`taskId`、`kind`、预期后缀、预期来源域名、最大次数和创建时间；
- started/completed/failed 必须属于同一下载 `downloadId`；
- 文件后缀和来源域名必须匹配 intent；
- 无关下载、跨主机下载和未匹配 started 的完成事件只记录告警，不交给任务；
- 同一 intent 的第二个匹配下载触发 `DOWNLOAD_INTENT_EXHAUSTED`；
- Word、目录和 ZIP 在下载完成后分别通过原格式校验，才登记为有效 intent；
- Electron DownloadManager 新增 Windows 保留名、尾随点/空格、非法字符和安全长度处理；
- 相同文件名通过独立下载目录隔离，不相互覆盖；
- 下载根目录不可创建或不可写时产生结构化失败事件；
- 单篇 Word、批量目录和批量 ZIP 的任务状态记录 intent ID 和 Download ID；
- 任务暂停、停止只在完整下载和校验之后发生；
- 保留阶段 C/D/E 的租约、启动幂等、SearchSession、筛选、页大小、不分组、来源治理、归档删除和批次下载语义。

验证：

- Python 132 项测试通过（8 个其他阶段已知缺口用例跳过）；
- Vitest 6 个文件、18 项通过（1 个其他阶段已知缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测通过；
- 单篇 Word 实际使用 `.doc/.docx` intent；
- 批量目录和 ZIP 使用不同 intent ID；
- 应用退出后无 Electron/Python 残留进程。

详细结果见 `docs/0.2.1_PHASE_F_TEST_REPORT.md`。

## 0.2.1 阶段 G 结果

已完成：

- 新增 `engine/pkulaw_engine/batch_execution.py`；
- 每个“关键词 × 子库 × 年份”组合保存独立检索上下文；
- 上下文保存 `searchId`、每页 SearchSession、页面结果签名、batchKeys 和 lastPage；
- 同一组合当前页完全匹配时直接复用，不重复搜索；
- 当前页在目标页之前时只向前翻页；
- 当前页在目标页之后、SearchSession 变化或结果签名不匹配时只重新搜索一次；
- 扫描结束后的当前页面可直接用于第一个匹配批次；
- 不同检索组合不会复用错误页面；
- 重新搜索后页面签名仍必须与扫描上下文一致，否则返回 `BATCH_RESULTS_CHANGED`；
- 批次按数据库顺序依次处理，已完成批次继续跳过；
- 批次开始下载前核验查询、子库、年份、筛选值、页大小、分组、当前页、行起止范围、政策 ID、标题和勾选数量；
- 选择批次后再次验证行集合与勾选集合完全一致；
- 暂停边界恢复后再次核验当前批次页面和勾选；
- 目录下载完成后、ZIP 下载前再次核验 SearchSession、结果签名、页码和勾选集合；
- 目录未通过格式/行身份校验前不会创建 ZIP intent；
- ZIP 未通过 CRC、成员、路径和身份映射前不会发布批次完成；
- checkpoint 保存检索上下文、活动组合、当前页、结果签名、批次搜索/复用/翻页次数和最近批次身份核验；
- 单条精确标题批次扫描后直接复用页面，处理阶段额外搜索次数为 0；
- 保留阶段 C/D/E/F 的租约、启动幂等、SearchSession、筛选、页大小、不分组、下载 intent、来源治理和归档删除语义。

验证：

- Python 143 项测试通过（8 个其他阶段已知缺口用例跳过）；
- Vitest 6 个文件、18 项通过（1 个其他阶段已知缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测通过；
- 阶段 4 单条精确标题批次处理阶段额外搜索次数为 0，直接复用次数至少 1；
- 阶段 4 最终批次身份核验的预期行数和实际勾选数一致；
- 应用退出后无 Electron/Python 残留进程。

详细结果见 `docs/0.2.1_PHASE_G_TEST_REPORT.md`。

## 0.2.1 阶段 H 结果

已完成：

- CSV 对 `=`、`+`、`-`、`@` 及前置空白、tab、CR、LF 后的
  危险文本添加安全前缀；
- XLSX 保留公式样式文本的原值并强制按字符串存储；
- 超过 Excel 单元格长度上限的文本在 XLSX 中安全截断并标注
  `完整内容见CSV`，CSV 保留完整原值；
- ZIP 校验新增压缩包大小、总解压大小、单成员大小、成员数、
  压缩比、目录深度和成员名长度上限；
- ZIP 成员和目录项均拒绝 Windows 保留名、非法字符、尾随点/空格、
  大小写重复和路径穿越；
- 批次目录先在 `.publishing` 中完整生成、计算 SHA-256 和发布清单，
  再整目录原子发布；
- `验收报告.json` 保持业务报告格式，`_publish_manifest.json`
  单独保存文件清单和哈希；
- 已发布目录恢复时重新校验批次身份、文件大小和 SHA-256；
- 已登记批次文件缺失、半写入、哈希异常或 SQLite 路径与清单不一致时
  失败关闭，不自动重新下载或覆盖原正式文件；
- 发布目录完成后、SQLite 提交前异常退出时，可从有效发布清单补登记；
- SQLite 提交后事件发送失败不会回滚已完成的持久化批次；
- 数据处理 CSV/XLSX/报告在临时目录全部生成并验证后，再成组替换正式文件；
- `atomic_replace_many()` 支持发布后复核和异常回滚，避免报告失败时留下
  新旧产物混合；
- 数据处理发布后统一登记 3 个 artifact，并再次核验文件存在性和 SHA-256；
- 路径穿越、重复目标、已经存在但无法核验的正式目录和大小写重复发布名
  均有结构化拒绝；
- 保留阶段 C/D/E/F/G 的租约、启动幂等、SearchSession、筛选、页大小、
  不分组、下载意图、批次复用、来源治理和归档删除语义。

验证：

- Python 173 项测试通过；8 项阶段 A 红基线按设计单独执行；
- 开启 `PKULAW_PHASE_A_BASELINE=1` 后，阶段 A 契约测试 9 项全部通过；
- Vitest 6 个文件、18 项通过（1 项其他阶段缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 全部运行中 SQLite 数据库 `PRAGMA integrity_check` 返回 `ok`；
- 阶段 4 验证业务验收报告、发布清单、批次文件、文件哈希和重试恢复一致；
- 阶段 5 验证 CSV/XLSX/报告、正文 TXT、来源证据和 SHA-256 一致；
- 应用退出后未发现残留 Electron/Python 进程。

详细结果见 `docs/0.2.1_PHASE_H_TEST_REPORT.md`。

## 0.2.1 阶段 I 结果

已完成：

- 新增统一 `task-presentation` 呈现模型，集中维护任务状态、阶段、
  操作动作、暂停原因、错误恢复和 SHA-256 文案；
- 逐篇、批量、数据处理、任务中心和总览使用同一套状态与按钮规则；
- 检索身份卡显示实际查询、匹配方式、子库、年份、省份、时效性、
  页码、总页数、页大小、分组模式、结果数、行数、searchId 和结果签名摘要；
- 新增工作流阶段条，区分浏览器绑定、检索验证、筛选、页大小、不分组、
  详情/Word 或批次/目录/ZIP/原子发布/SQLite 登记；
- 浏览器租约卡显示持有任务、模式、leaseId、开始/更新时间和忙碌原因；
- 下载意图卡显示 intentId、downloadId、Word/目录/ZIP 类型、
  pending/started/completed/validated/failed、消费次数、后缀、来源和临时路径；
- 下载 started/completed 通过回调同步到任务 checkpoint，不再只在下载结束后更新；
- `DOWNLOAD_EVENT_MISMATCH` 通过 Python 事件转发到渲染层并显示运行时告警；
- 错误恢复卡显示错误码、原因、阶段、已完成数量、当前断点、
  可执行恢复建议和硬停止标记；
- 自动暂停与手动暂停使用不同文案；登录、滑块、同名候选、分页上限、
  批量规模确认、额度异常和结构异常分别显示；
- 分页上限和批量规模确认继续使用原有显式决策按钮；
- 新增 SQLite 任务时间线只读接口，详情返回最近 200 条任务事件；
- 时间线折叠连续同状态和 2 秒内短暂 `running -> waiting_manual -> running`
  抖动，避免重复阶段记录；
- 数据与报告页增加来源、待核验和 SHA-256 完整/缺失/不一致筛选；
- artifact、Word、TXT、证据和批次路径支持展开查看；
- 设置页显示应用、Python、后端、协议、Electron/Chromium/Node 版本，
  SQLite schema/revision/integrity，浏览器 View、目标绑定、Session、登录状态，
  数据/日志/下载临时/系统临时目录及可写检查；
- 最近失败任务在设置页显示同一错误恢复卡和下一步建议；
- 长错误和路径通过 Electron clipboard 接口复制；
- 主窗口最小宽度调整为 860，新增 900px 响应式布局；
- 866px 内部宽度、125% 缩放验证无横向溢出和按钮越界；
- 正常使用不依赖 Agent、Codex 或大模型。

验证：

- Python 175 项测试通过；8 项阶段 A 红基线按设计单独执行；
- Vitest 8 个文件、36 项通过（1 项其他阶段缺口用例跳过）；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 阶段 6 额外验证设置诊断、目录写入、剪贴板复制、125% 高 DPI
  和 866px 内部宽度窄窗口；
- 阶段 3 验证额度硬停止错误卡和 5 条去重时间线；
- 阶段 4 验证 Word ZIP 下载意图、1/1 消费次数、文件校验通过和 16 条时间线；
- 阶段 5 验证数据处理停止文案、SHA-256 筛选和逐条哈希状态；
- 全部运行中 SQLite 数据库 `PRAGMA integrity_check` 返回 `ok`；
- 应用退出后未发现残留 Electron/Python 进程。

详细结果见 `docs/0.2.1_PHASE_I_TEST_REPORT.md`。

## 0.2.1 阶段 J 结果

已完成：

- 应用、Python 后端、协议展示和发布元数据统一为 `0.2.1`；
- Python 227 项正式测试通过，8 项阶段 A 红基线按设计单独执行；
- Vitest 8 个文件、38 项测试通过，1 项已知缺口用例跳过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5、6 Electron 烟测逐项通过；
- Python 主进程、Electron 子进程和五个烟测覆盖数据合并成功；
- 关键模块逐模块分支覆盖均不低于 85%；
- 桌面下载管理器语句、函数、行覆盖 100%，分支覆盖 92.30%；
- SQLite schema v5 和 `PRAGMA integrity_check=ok`；
- Word、TXT、CSV、XLSX、官方 HTML、截图、任务报告、
  `_publish_manifest.json` 和 SHA-256 一致性回归通过；
- CSV/XLSX 公式注入、ZIP 资源上限、路径穿越、Windows 成员名、
  原子发布和故障注入回归通过；
- 生成 0.2.1 Windows 便携 ZIP、源码与构建脚本 ZIP、依赖锁和
  SHA-256 sidecar；
- 干净隔离长中文/空格路径首次启动通过；
- 无目标机 Python 环境依赖，内置 PyInstaller 后端正常；
- 一键启动器、缺少后端提示、日志/数据目录入口通过；
- 125% 高 DPI、866px 内部宽度、断网、Python/浏览器崩溃、
  强制退出和重启恢复通过；
- 发布验证结束后无 Electron/Python 残留。

发布构建同时修复：

- 覆盖采集 `sitecustomize.py` 改为仅在
  `COVERAGE_PROCESS_START` 存在时加载；
- 便携构建每次在 `release` 边界内重建完整 stage 目录，避免旧
  `数据`、SQLite 和日志污染新 ZIP；
- 源码包排除 `outputs`、运行目录、依赖缓存和临时覆盖数据。

阶段 J 便携 ZIP：

```text
outputs/北大法宝采集器_Windows便携版_0.2.1_20261101.zip
SHA-256: 421f228b9e16c8320ea8e29b21a11a79b9ded59d05e850c818e4841901185fb2
```

详细结果见 `docs/0.2.1_PHASE_J_TEST_REPORT.md`。

## 0.2.1 阶段 K 结果

已执行：

- 真实内置网页、机构 IP 识别、登录、目标绑定和 V6 selector profile；
- 真实年份、省份、时效性、页大小和不分组；
- 单篇精确标题、窄关键词和真实 OLE `.doc`；
- 单条批次的真实 BIFF `.xls` 目录与 Word ZIP；
- 真实地方法规和中央/地方双库组合；
- 真实 9/9 页尾页只读扫描；
- 第 2 页上限门禁和批量规模二次确认门禁；
- 真实政府官网 `NO_EXACT_MATCH` 精确补全；
- K8 数据处理、CSV/XLSX、TXT、来源证据、报告和 SHA-256；
- 全部 Python、Vitest、类型检查、生产构建和阶段 2–6 Electron 烟测。

真实通过：

- `.doc` SHA-256：
  `c9223bf3db5a2e6365933c2fe019096dec062e79bf673f462d5a64beec264809`；
- 单条批次 BIFF XLS 和 Word ZIP；
- 9 页、180 条真实尾页扫描，第 9 页“下一页”不可用；
- 第 2 页上限进入 `PAGE_LIMIT_REACHED` 后安全停止；
- 18 页、348 行、7 批次投影触发规模确认，未确认前 0 下载；
- 苏州工业园区精确标题在北大法宝为 0 条，政府官网正文整理通过；
- 官方 HTML SHA-256：
  `21825934e96e15cde3bd6e1ec81dbba0da4aae277280ea8746a4e1d8c0e33b2c`；
- 官方截图 SHA-256：
  `26b91ebd83d73efb8a7054b5e19d8a9bb5eda698a68a7a555d4841eef8370e10`。

真实站点限制：

- 批量下载弹窗只提供 Excel/WPS 目录，`catalogExcel` 实际为 BIFF `.xls`；
- 未提供独立原生 XLSX 或 HTML-XLS 目录；
- K7 地方 Word ZIP 等待 65 秒后仍未创建下载页，任务以 `partial` 隔离该批次；
- 本轮真实单篇未返回原生 `.docx`；
- 登录失效、滑块/验证码和额度重新耗尽未自然触发。

本轮真实缺陷修复：

- 空精确标题结果页不再误用不存在的筛选、页大小和不分组控件；
- 尾页“下一页”同时检查父级 `li.disabled`；
- 360 政府官网结果支持 `data-mdurl` 解包，真实模式强制最终政府域名；
- 无独立精确控件的夹具 profile 使用检索字段门禁；
- 隐藏 `SearchKeywordType` 下拉框按选项值切换标题/全文；
- 结果页子库切换实际点击并连续核验可见库和隐藏 `Library` 字段；
- 批量下载子页清理和 Word COM 后台线程初始化/UTF-8 回退；
- 停止幂等测试补齐，`batch_task.py` 分支覆盖率恢复到 85% 以上。

最终自动化结果：

- Python 236 项通过，8 项阶段 A 已知缺口按设计跳过；
- Vitest 9 个文件通过、1 个跳过；41 项通过、1 项跳过；
- 阶段 A 原先跳过的 9 项契约测试通过；
- Python 全项目综合覆盖率 86.02%，关键模块逐模块分支覆盖均不低于 85%；
- `batch_task.py` 分支覆盖率 85.05%；
- 桌面下载管理器语句、函数、行覆盖 100%，分支覆盖 93.33%；
- TypeScript node/web 类型检查和 Electron 生产构建通过；
- 阶段 2、3、4、5、6 Electron 烟测全部通过；
- 应用退出后无 Electron/Python 残留。

详细结果见 `docs/0.2.1_PHASE_K_TEST_REPORT.md`。

## 当前阻塞与未做

- 真实 XLSX 目录：站点未提供独立原生选项；
- 真实 HTML-XLS 目录：站点未提供独立原生选项；
- K7 地方 Word ZIP：站点批量 ZIP 限额或冷却，未创建下载页；
- 真实原生 `.docx`：本轮未返回，政府官网整理 DOCX 不能替代；
- 登录失效、滑块/验证码和额度重新耗尽：K11 未自然触发；
- 发布 EXE 未进行商业代码签名，首次运行可能出现 Windows“未知发布者”提示；
- 未提供系统级安装器和自动卸载器；当前交付为无安装便携版。
- `F-011` 已按真实 9/9 页证据更新为 `GREEN`；
- `F-040` 继续保持 `RED（真实部分通过）`，不得宣称全部真实格式和全部 ZIP 场景通过。

## 当前交付入口

- 阶段 K 修复后便携包：
  `outputs/北大法宝采集器_Windows便携版_0.2.1_20261102.zip`
- SHA-256：
  `77ce214ef5c22ffeda29dc6eb0a61801cf7ec0b25375f7e6c26ff0a0991b11d1`
- 阶段 K 修复后源码与构建脚本：
  `outputs/北大法宝采集器_源码与构建脚本_20261102.zip`
- SHA-256：读取同名 `.sha256` sidecar
- 阶段 J 离线发布验收：`outputs/0.2.1发布验收报告_2026-11-01.md`
- 阶段 K 真实站点验收：
  `outputs/0.2.1真实站点验收报告_2026-11-01.md`
- 阶段 K 提示词：`outputs/0.2.1阶段K提示词_2026-11-01.md`
- 阶段 K 便携包隔离验证：
  `outputs/0.2.1_便携包首次启动报告_20261102.json`
- 0.1.0 与阶段 J `20261101` 便携包继续保留为回退版本。

## 0.2.2 阶段 A 结果

已完成全量正式场景整改的测试红基线：

- 新增 RecordSearch 结果页、失效下一页点击、可见 `.drag_text`、隐藏 `#captcha-overlay` 和完整 38 条正文 HTML 夹具；
- 新增真实 BIFF XLS 离线证据样本及来源哈希；
- 新增统一全量缺陷契约测试 `engine/tests/test_full_scope_contracts.py`；
- 14 项测试中，4 项夹具自检通过，10 项按预期失败；
- 10 项失败覆盖验证码继续门禁、统一 AccessState、RecordSearch 路由、表单回退、正文提取、内置 DOC、原生 XLS 契约、partial 进度和批量来源保留；
- 本阶段未访问真实北大法宝，未执行真实搜索或下载，未修改生产实现。

证据：

- `docs/0.2.2_TASK_1_RED_BASELINE.txt`
- `docs/0.2.2_TASK_1_LEDGER.md`
- `docs/0.2.2_PHASE_A_TEST_REPORT.md`
- `outputs/0.2.2阶段B提示词_2026-11-02.md`

下一动作：执行 0.2.2 阶段 B。

## 0.2.2 阶段 B 结果

已完成统一验证码状态与受控分页：

- 新增统一 AccessState，BrowserBridge 和 PkulawAdapter 共用一个验证码/登录/额度状态源；
- `.drag_text` 可见时正确识别，隐藏 `#captcha-overlay` 不误报；
- 验证码仍可见时继续请求返回 `CAPTCHA_VISIBLE`；
- 验证码消失后连续两次采样恢复，重复继续幂等；
- BrowserHost 暴露验证码 selector、尺寸和检测时间；
- 下一页点击失效时执行一次受控 `#right_form` 提交；
- 同时支持 `/law/lar` 和 `/law/search/RecordSearch` 结果页；
- 结果页不再因缺少 `#subLibraries` 失败；
- 翻页确认页码、行号、政策 ID、结果签名、查询、检索字段和子库；
- 正常点击、错误落地页、签名不变、跨检索和超时路径均 fail-closed；
- 单篇和批量的“已处理，继续”不再吞掉验证码未清除错误。

验证：

- 阶段 B 目标契约 10/10 通过；
- 既有 Python 236 项通过，8 项跳过；
- Vitest 41 项通过、1 项跳过；
- TypeScript node/web 类型检查通过；
- 阶段 2、3、4 Electron 烟测通过；
- 烟测结束无 Electron/Python 残留；
- 全量契约矩阵仍保留阶段 C/D/E/F 的 5 个预期红灯。

证据：

- `docs/0.2.2_PHASE_B_TEST_REPORT.md`
- `docs/0.2.2_PHASE_B_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_B_REGRESSION.txt`
- `docs/0.2.2_PHASE_B_FULL_SCOPE_MATRIX.txt`
- `docs/0.2.2_TASK_2_LEDGER.md`
- `outputs/0.2.2阶段C提示词_2026-11-02.md`

下一动作：执行 0.2.2 阶段 C。

## 0.2.2 阶段 C 结果

已完成网站原生 XLS 与派生目录格式拆分：

- 新增 `engine/pkulaw_engine/catalog_formats.py`；
- API 继续保留 `xls`、`xlsx`、`html-xls`，内部分别映射为
  `native-xls`、`derived-xlsx`、`compat-html-xls`；
- 网站原生目录契约固定为 `catalogExcel`、`.xls` 后缀和
  `D0CF11E0A1B11AE1` BIFF XLS 签名；
- 批量目录下载 intent 无论用户请求 XLSX 还是 HTML-XLS，均只接受
  网站返回的原生 `.xls`；
- 原生格式不符立即返回 `CATALOG_NATIVE_FORMAT_MISMATCH`，不再等待
  60 秒下载超时；
- 错误信息包含 intentId、downloadId、实际文件名、后缀、签名、路径
  和恢复建议，结构化详情同步写入 checkpoint 与批次错误；
- XLSX 只从已接收并核验的原生 XLS 本地转换；
- HTML-XLS 只作为本地兼容格式生成；
- 派生结果必须写入 `native=false`、`sourceFormat=xls`、
  `finalFormat`、`converted`、源文件和最终文件 SHA-256；
- XLSX 所有单元格按文本类型写入，`=`、`+`、`-`、`@` 以及前置空白
  不会变成可执行公式；
- 派生使用同目录临时文件，全部通过字段、行数和稳定 ID 身份校验后
  才原子替换目标；失败不覆盖既有正式文件；
- 原生 XLS 和派生目录完成校验后才创建 ZIP intent；
- 目录转换失败不启动 ZIP；
- 已完成批次重试继续通过发布清单恢复，不重复下载；
- 批量界面显示“网站原生 XLS”“本地转换 XLSX（源自网站 XLS）”
  和“兼容 HTML-XLS（本地生成）”，批次错误可展开查看结构化详情；
- 阶段 4 本地机构服务改为按所选政策动态生成真实 BIFF XLS，烟测不再
  用 XLSX 或 HTML-XLS 代替网站原生目录；
- 阶段 4 烟测分别验证原生 XLS、派生 XLSX 和兼容 HTML-XLS 的
  `native`、`sourceFormat`、`finalFormat`、`converted` 与 SHA-256
  元数据；
- 保留阶段 B AccessState、验证码继续门禁和受控分页语义；
- 未访问真实北大法宝，未执行真实下载。

验证：

- 阶段 C 目标测试 13/13 通过；
- Python 266 项通过，12 项已知后续阶段契约按设计跳过；
- Vitest 10 个文件、42 项测试通过，1 项已知缺口跳过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4 Electron 烟测全部通过；
- 阶段 4 原生 XLS、派生 XLSX、兼容 HTML-XLS 报告均
  `crcOk=true`、`identityMappingOk=true`；
- 强制运行全部待实现契约时仍有 4 项预期红灯：
  `.kuan-content` 完整正文、内置离线 DOC、来源数据库拆分和统一进度；
- 上述 4 项分别属于阶段 D、E、F，未在阶段 C 伪造通过；
- 全部测试结束后无 Electron/Python 残留进程。

证据：

- `docs/0.2.2_PHASE_C_TEST_REPORT.md`
- `docs/0.2.2_PHASE_C_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_C_REGRESSION.txt`
- `docs/0.2.2_PHASE_C_PENDING_MATRIX.txt`
- `docs/0.2.2_PHASE_C_BUILD.txt`
- `docs/0.2.2_PHASE_C_SMOKE.txt`
- `docs/0.2.2_PHASE_C_CATALOG_EVIDENCE.json`
- `docs/0.2.2_PHASE_C_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_C_FILE_MANIFEST.txt`
- `docs/0.2.2_TASK_3_LEDGER.md`

下一动作：执行 0.2.2 阶段 D“内置旧版 DOC 转换与完整正文提取”。

## 0.2.2 阶段 D 结果

已完成内置旧版 DOC 转换与完整正文提取：

- 新增 `engine/pkulaw_engine/body_quality.py`；
- 详情正文按 DOM 顺序组合直接文本节点、普通段落、
  `p.navzhang` 章节标题、`.tiao-wrap .kuan-content` 条文和表格；
- 正文节点使用 `textContent`，不再依赖可能为空的容器 `innerText`；
- 长正文夹具实际提取 6 个章节、38 条连续条文、45 个文本段，
  正文长度 864；
- 完整性评估支持 `complete`、`suspicious`、`failed`；
- 缺条文、空章节、空条文、末尾截断、正文过短和预期数量不符均返回
  `suspicious`；
- 空正文返回 `failed`；
- 可疑或不完整正文在打开详情阶段返回 `BODY_INCOMPLETE`，不会创建
  Word 下载或污染后续数据处理；
- 单篇任务和批处理把 `BODY_INCOMPLETE`、`DOC_CONVERTER_UNAVAILABLE`、
  `DOC_TEXT_EXTRACTION_FAILED` 原样传给任务项，不再统一压缩为泛化错误；
- 新增内置 `antiword 0.37` Windows x64 转换器；
- 内置二进制 SHA-256：
  `1dde37af1bb049c03e71c9d20eb99a2a3e011fe9170306b3a154be5c8515788f`；
- 每次旧版 DOC 转换前校验二进制 SHA-256、UTF-8 资源和版本文本；
- 内置 antiword 优先，系统 antiword、LibreOffice 和 Word COM 只作为
  可选后备；
- 三份真实 OLE `.doc` 离线夹具在禁用系统转换器时均由内置
  `bundled-antiword` 提取完整中文正文；
- `DocumentExtractionResult` 返回转换方法、正文来源类型、转换器名称、
  版本和 SHA-256；
- `.docx` 继续由 `python-docx` 提取并返回同一结果类型；
- 损坏、缺失、哈希不符、版本不符和超时均失败关闭；
- 转换失败只登记待核验文档，不生成不完整 TXT、证据或完成产物；
- PyInstaller 后端包含 antiword、UTF-8 映射、GPL-2.0 许可证、
  对应源码、MSYS2 补丁、来源说明和机器可读清单；
- 阶段 2 V6 详情审计新增完整正文夹具，实际在 Electron 中得到
  6 章、38 条、`complete`；
- 修复 `.fulltext-wrap` 作为 V6 检测标记过宽的问题，改用真实长正文
  特有的 `.tiao-wrap .kuan-content`，避免本地普通详情页误识别；
- 设置与诊断页的后端诊断可以识别 `bundled-antiword`；
- 保留阶段 C 原生 XLS、派生 XLSX、HTML-XLS、ZIP 前目录校验语义；
- 未访问真实北大法宝，未执行真实搜索或下载。

验证：

- 阶段 D 目标测试 19/19 通过；
- Python 279 项通过，10 项跳过；
- Vitest 10 个文件、42 项通过，1 项已知缺口跳过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- PyInstaller 后端构建通过；
- 打包后端实际包含 antiword 0.37、GPL 许可证、对应源码和 UTF-8 映射；
- 使用打包资源目录再次离线提取三份真实 DOC，全部使用
  `bundled-antiword`；
- 阶段 2、3、4 Electron 烟测全部通过；
- 阶段 2 实际浏览器审计得到 `complete`、6 章、38 条；
- 强制运行全部待实现契约时，只剩阶段 E/F 两项预期红灯：
  来源数据库拆分与统一进度模型；
- 全部测试结束后无 Electron/Python 残留进程。

证据：

- `docs/0.2.2_PHASE_D_TEST_REPORT.md`
- `docs/0.2.2_PHASE_D_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_D_REGRESSION.txt`
- `docs/0.2.2_PHASE_D_PENDING_MATRIX.txt`
- `docs/0.2.2_PHASE_D_BUILD.txt`
- `docs/0.2.2_PHASE_D_PYINSTALLER.txt`
- `docs/0.2.2_PHASE_D_SMOKE.txt`
- `docs/0.2.2_PHASE_D_EVIDENCE.json`
- `docs/0.2.2_PHASE_D_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_D_FILE_MANIFEST.txt`
- `docs/0.2.2_TASK_4_LEDGER.md`

## 0.2.2 阶段 E 结果

已完成 SQLite v6 来源模型与报表：

- SQLite schema 从 v5 迁移到 v6；
- `policy_documents` 新增：
  - `text_source_type`；
  - `extraction_method`；
  - `converter_name`；
  - `converter_version`；
  - `body_completeness`；
  - `review_status`；
- `source_type` 继续只表示原件来源；
- 正文来源、提取方式、转换器、正文完整性和核验状态独立保存；
- v5 旧记录迁移后默认：
  - `body_completeness=unknown`；
  - `review_status=pending_review`；
- 单篇、批量和政府官网数据处理写入实际
  `DocumentExtractionResult` 元数据；
- 单篇转换失败保留 `source_type=北大法宝原件`；
- 批量 ZIP 已取得正式原件但 DOC 转换失败时保留
  `source_type=北大法宝ZIP`；
- 转换失败记录正文来源为空、`body_completeness=failed`、
  `review_status=pending_review`，不把原件来源改写为“待核验”；
- 可疑正文保存原始 Word 和 TXT 供核验，但状态保持
  `suspicious` / `pending_review`；
- CSV、XLSX 和 HTML 任务报告增加正文来源、提取方法、转换器名称和版本、
  正文完整性、核验状态及处理状态；
- DataPanel 增加原件来源、正文完整性、核验状态筛选，以及转换器、
  正文来源和失败原因详情；
- 保留公式注入防护、原子发布、SHA-256、阶段 C 原生 XLS、
  阶段 D 正文门禁、AccessState 和受控分页语义；
- 未实现阶段 F 的统一进度模型，未进入阶段 F。

验证：

- 阶段 E 目标测试 12/12 通过；
- Python 283 项通过，9 项跳过；
- Vitest 12 个文件、46 项通过，1 项已知缺口跳过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 新建数据库和 v5→v6 迁移数据库的 `PRAGMA integrity_check` 均为 `ok`；
- 强制运行全部待实现契约时，仅剩阶段 F 的
  `test_partial_processing_progress_is_not_complete` 预期红灯；
- 迁移保留任务、任务项、批次、处理运行、政策文档和 artifact；
- 证据包 13 个登记文件 SHA-256 全部复算一致；
- CSV/XLSX/HTML 报告字段、三类来源记录和 v5 安全默认值核验通过；
- 全部测试和烟测结束后，与工作区关联的 Electron/Python/Node
  残留进程数为 0。

证据：

- `docs/0.2.2_PHASE_E_TEST_REPORT.md`
- `docs/0.2.2_PHASE_E_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_E_REGRESSION.txt`
- `docs/0.2.2_PHASE_E_PENDING_MATRIX.txt`
- `docs/0.2.2_PHASE_E_BUILD.txt`
- `docs/0.2.2_PHASE_E_SMOKE.txt`
- `docs/0.2.2_PHASE_E_EVIDENCE_VERIFICATION.txt`
- `docs/0.2.2_PHASE_E_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_E_FILE_MANIFEST.txt`
- `docs/0.2.2_TASK_5_LEDGER.md`
- `outputs/0.2.2阶段E来源模型证据_2026-11-02/`
- `outputs/0.2.2阶段F提示词_2026-11-02.md`

## 0.2.2 阶段 F 结果

已完成统一进度模型、状态、错误与时间线：

- 新增 `engine/pkulaw_engine/progress.py`；
- 定义并持久化 `ProgressSnapshot`：
  - `overall`；
  - `stage`；
  - `completedUnits`；
  - `totalUnits`；
  - `succeeded`；
  - `failed`；
  - `pendingReview`；
- 单篇按预检 5%、扫描 25%、逐条下载 65%、收尾 5% 推进；
- 批量按扫描 20%、目录与校验 20%、ZIP 与校验 30%、
  原子发布和登记 SQLite 30% 推进；
- 数据处理按政策逐条推进；
- TaskRecord 和 ProcessingRunRecord 通过协议透传
  `progressSnapshot`；
- 旧 checkpoint 缺少快照时使用明确标注的安全回退；
- `partial` 终态固定低于 100%；
- 0/3 成功、3 条待核验显示 99% 工作进度、成功 0、待核验 3，
  不再显示 100% 成功；
- 相同阶段、动作、错误码、进度和计数在 2 秒内重复时，
  `task.updated` / `data.run.updated` 事件去重；
- 真实阶段、动作、错误码或计数变化保留；
- 数据处理运行事件写入任务时间线；
- 前端继续折叠短暂的 `running → waiting_manual → running`；
- 单篇、批量、数据处理和任务中心共用 `ProgressSnapshotCard`；
- 统一错误卡新增动作策略、实际文件、格式契约、intentId、
  downloadId、正文完整性、断点和完整错误 JSON；
- 下载次数耗尽、批次产物完整性失败等错误继续保持硬停止；
- 阶段 5 烟测发现的停止竞态已修复：
  停止、暂停和继续会重新读取最新处理运行，不能用旧摘要覆盖刚写入的
  政策断点；
- 新增可复现测试
  `test_stop_does_not_overwrite_newer_completed_checkpoint`。

验证：

- 阶段 F Python 目标测试 16/16 通过；
- 阶段 F 桌面目标测试 22/22 通过；
- Python 298 项通过，8 项跳过；
- Vitest 12 个文件、50 项通过，1 项已知缺口跳过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5 Electron 烟测全部通过；
- 强制运行全部 32 项契约，32/32 通过；
- 阶段 5 停止断点 `dataStopPreservedCheckpoint=true`；
- 数据页显示 `已处理 5/12 · 成功 5 · 停止 · 43%`；
- 证据包 SQLite `PRAGMA integrity_check=ok`；
- 证据包 9 个文件 SHA-256 全部复算一致；
- 全部测试和烟测结束后，与工作区关联的 Electron/Python/Node
  残留进程数为 0。

证据：

- `docs/0.2.2_PHASE_F_TEST_REPORT.md`
- `docs/0.2.2_PHASE_F_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_F_REGRESSION.txt`
- `docs/0.2.2_PHASE_F_PENDING_MATRIX.txt`
- `docs/0.2.2_PHASE_F_BUILD.txt`
- `docs/0.2.2_PHASE_F_SMOKE_FINAL.txt`
- `docs/0.2.2_PHASE_F_RESPONSIVE_SMOKE.txt`
- `docs/0.2.2_PHASE_F_EVIDENCE_VERIFICATION.txt`
- `docs/0.2.2_PHASE_F_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_F_FILE_MANIFEST.txt`
- `docs/0.2.2_TASK_6_LEDGER.md`
- `outputs/0.2.2阶段F统一进度证据_2026-11-02/`
- `outputs/0.2.2阶段G提示词_2026-11-02.md`

## 0.2.2 阶段 G 结果

已完成检索模式预检、网页缩放与文案统一：

- 新增 `engine/pkulaw_engine/collector_preview.py`；
- 单篇和批量默认匹配方式均为“关键词”；
- “完整标题”统一改为“精确完整标题”；
- 精确完整标题只允许逐篇任务；
- 应用入口和批量任务配置解析均拒绝
  `BATCH_EXACT_TITLE_UNSUPPORTED`，避免只靠 UI 隐藏；
- 单篇和批量增加启动前“预览（0 下载）”；
- 预览按“关键词 × 子库 × 年份”展开，显示：
  - 组合数；
  - 实际结果数；
  - 实际总页数；
  - 预计 50 条批次数；
- 预览使用临时全局浏览器租约，成功或失败均释放；
- 预览不创建任务、下载或下载意图，不打开详情；
- 任务创建时将摘要写入 checkpoint 的 `previewSnapshot`；
- 新增 `task.preview` 和 `task.preview.v1` 协议能力；
- 新增 `apps/desktop/src/shared/browser-zoom.ts`；
- BrowserHost 仅对已绑定 `WebContentsView` 设置 50%–200% 缩放；
- 缩放按机构 Session 持久化，并在视图重建、Python 重启和渲染恢复后复原；
- 支持 `Ctrl +`、`Ctrl -`、`Ctrl 0`，工具栏、快捷键和详情同步；
- 网页 125% 缩放时，Electron 应用外壳保持 100%；
- BrowserPanel、单篇、批量和任务中心共用 BrowserHost/AccessState
  验证码可见状态；
- 验证码仍可见时，“已处理，继续”按钮同步禁用；
- 保留阶段 C/D/E/F 的原生 XLS、正文门禁、SQLite v6、统一进度、
  错误卡、时间线和任务控制语义。

烟测过程中发现并修复：

- 1.25 倍网页缩放的快捷键原先按一位小数取整，`Ctrl +` 会从 125%
  跳到 140%；改为两位小数后为 135%；
- 批量控制和 Electron 夹具仍有旧的精确标题成功路径，已统一改为关键词；
- 阶段 4 多批 ZIP 夹具的烟测超时小于实际完整安全冷却时间，已只提高
  烟测等待上限，产品 65 秒批次冷却和原子安全边界不变；
- 规模确认后的旧断言误期待实际行数和批次数为 0，已改为校验实际
  501 条结果和 `ceil(501/50)=11` 个批次。

验证：

- 阶段 G 目标测试：
  - Python 23/23 通过；
  - Vitest 6 个文件、36/36 通过；
- Python 全量：308 项通过，8 项阶段 A 已知用例按设计跳过；
- Vitest 全量：62 项通过，1 项已知隔离用例跳过；
- 强制运行全部 32 项契约：32/32 通过；
- TypeScript node/web 类型检查通过；
- Electron 生产构建通过；
- 阶段 2、3、4、5、6 Electron 烟测全部通过；
- 批量预览示例：1 个组合、60 条结果、2 页、预计 2 个批次、下载 0；
- 预览前后任务数、最近下载和浏览器租约均无变化；
- 缩放 50%、100%、125%、150%、200% 全部可应用；
- 125% 在视图重建、Python 重启和渲染恢复后保持；
- 866px 内部宽度、125% 高 DPI 下，单篇和批量页面
  `scrollWidth=851`，无横向溢出，按钮均在窗口内；
- schema v6，五个烟测数据库的 `PRAGMA integrity_check=ok`；
- 证据包 25 个登记文件 SHA-256 全部复算一致；
- 全部测试和烟测结束后，相关 Electron/Python/Node 进程数为 0。

证据：

- `docs/0.2.2_PHASE_G_TEST_REPORT.md`
- `docs/0.2.2_PHASE_G_TARGET_TESTS.txt`
- `docs/0.2.2_PHASE_G_TARGET_TESTS_DESKTOP.txt`
- `docs/0.2.2_PHASE_G_REGRESSION.txt`
- `docs/0.2.2_PHASE_G_PENDING_MATRIX.txt`
- `docs/0.2.2_PHASE_G_BUILD.txt`
- `docs/0.2.2_PHASE_G_SMOKE.txt`
- `docs/0.2.2_PHASE_G_RESPONSIVE_SMOKE.txt`
- `docs/0.2.2_PHASE_G_SQLITE_INTEGRITY.txt`
- `docs/0.2.2_PHASE_G_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_G_EVIDENCE_VERIFICATION.txt`
- `docs/0.2.2_PHASE_G_FILE_MANIFEST.txt`
- `docs/0.2.2_PHASE_G_FILE_MANIFEST_VERIFICATION.txt`
- `docs/0.2.2_TASK_7_LEDGER.md`
- `outputs/0.2.2阶段G搜索模式与缩放证据_2026-11-02/`
- `outputs/0.2.2阶段H提示词_2026-11-02.md`

## 0.2.2 阶段 H 结果

已完成全量离线回归与 0.2.2 发布构建：

- 根项目、Electron 应用、Python 包、运行版本和发布元数据统一为
  `0.2.2`；
- 新增 Python 与 Vitest 发布元数据测试；
- Python 全量 310 项通过，8 项阶段 A 已知用例按设计跳过；
- Vitest 16 个文件通过、1 个跳过；64 项通过、1 项跳过；
- TypeScript node/web 类型检查通过；
- Electron main、preload 和 renderer 生产构建通过；
- 阶段 2、3、4、5、6 Electron 烟测分别通过；
- 数据库全部 schema v6，`PRAGMA integrity_check=ok`；
- 便携包使用内置 Python 和 antiword，不依赖目标机 Python 或 Word；
- 三份真实 OLE `.doc` 从发布包内 antiword 提取，正文哈希与阶段 D
  锁定证据一致；
- 便携包通过首次启动、一键启动、缺少内置后端提示、断网、
  强制退出恢复和无残留进程验证；
- 生成 Windows 便携 ZIP、源码包、团队部署目录、依赖锁和 SHA-256；
- 更新安装卸载、使用、复现构建、依赖、故障排查、数据恢复和发布文档；
- 生成离线发布验收报告并明确真实站点事项留给阶段 I。

覆盖率：

- Python 全项目综合覆盖 86.23%，语句覆盖 88.90%；
- 关键模块逐模块分支覆盖均不低于 85%；
- `batch_task.py` 首次为 83.93%，新增批量控制测试后提升到 85.27%；
- 桌面 download-manager 语句/行/函数 100%，分支 93.33%。

发布产物：

- `outputs/北大法宝采集器_Windows便携版_0.2.2_20261102.zip`；
- 便携 ZIP SHA-256
  `70eeeabbe98e8c95cfd495a7a8831e80e1478d9b7e46d5d31dd44a0127d57232`；
- `release/PkulawWorkbench-portable/`；
- `outputs/北大法宝采集器_源码与构建脚本_20261102.zip`；
- 两个 ZIP 均有独立 `.sha256` 文件；
- 最终源码包在全部发布记录生成后重新构建，打包脚本排除工作区中一次
  命令误留下的零字节 `$null` 文件；
- 最终源码包 SHA-256 记录在
  `outputs/0.2.2源码包验证_2026-11-02.json`、离线发布验收报告和不被
  源码包递归包含的 SHA-256 汇总文件中，避免自引用。
- 最终源码包条目清单只作为外部发布证据生成，不放入源码 ZIP 自身，
  避免包内清单描述包含自身的包。

关键验证：

- `pnpm install --frozen-lockfile` 通过；
- 发布记录完成后再次执行
  `pnpm install --frozen-lockfile` 和
  `pnpm --dir apps/desktop typecheck`，两项均通过；
- 打包 Python 环境的 `pip check` 无破损依赖；
- 隔离目录路径包含长中文、空格和深层目录；
- 125% 高 DPI、866px 内部宽度下单篇和批量页面无横向溢出；
- 当前 6 个有效 artifact、11 条政策文档的文件、CSV/XLSX、官方
  HTML/PNG 和 SHA-256 均已核验；
- 便携 ZIP 不含 SQLite、日志、运行数据、测试目录或开发缓存；
- 源码包包含源码、测试、脱敏夹具、锁文件和构建脚本，排除运行数据
  与构建缓存；
- 最终源码 ZIP 不包含 `outputs/`、`release/`、`build/`、`clean-room/`、
  `runtime/`、`node_modules/`、SQLite、日志、覆盖数据或开发缓存，也不含
  源码包最终哈希自身；
- 应用正常关闭、一键启动器退出和强制退出后无 Electron/Python 残留。

证据：

- `docs/0.2.2_PHASE_H_TEST_REPORT.md`
- `docs/0.2.2_PHASE_H_REGRESSION.txt`
- `docs/0.2.2_PHASE_H_PYTHON_COVERAGE.txt`
- `docs/0.2.2_PHASE_H_NODE_COVERAGE.txt`
- `docs/0.2.2_PHASE_H_COVERAGE_SUMMARY.json`
- `docs/0.2.2_PHASE_H_TYPECHECK.txt`
- `docs/0.2.2_PHASE_H_BUILD.txt`
- `docs/0.2.2_PHASE_H_SMOKE.txt`
- `docs/0.2.2_PHASE_H_SQLITE_INTEGRITY.txt`
- `docs/0.2.2_PHASE_H_ARTIFACT_VERIFICATION.json`
- `docs/0.2.2_PHASE_H_PACKAGED_DOC_EVIDENCE.json`
- `docs/0.2.2_PHASE_H_PORTABLE_BUILD.txt`
- `docs/0.2.2_PHASE_H_PORTABLE_VERIFY.txt`
- `docs/0.2.2_PHASE_H_PACKAGE_VERIFICATION.json`
- `docs/0.2.2_PHASE_H_VERSION_VERIFICATION.json`
- `docs/0.2.2_PHASE_H_FINAL_LOCKFILE_INSTALL.txt`
- `docs/0.2.2_PHASE_H_FINAL_TYPECHECK.txt`
- `docs/0.2.2_PHASE_H_FILE_MANIFEST.txt`
- `docs/0.2.2_PHASE_H_FILE_MANIFEST_VERIFICATION.txt`
- `docs/0.2.2_PHASE_H_RESIDUAL_PROCESSES.txt`
- `docs/0.2.2_PHASE_H_EVIDENCE_VERIFICATION.txt`
- `docs/0.2.2_TASK_8_LEDGER.md`
- `outputs/0.2.2阶段H发布证据_2026-11-02/`
- `outputs/0.2.2源码包验证_2026-11-02.json`
- `outputs/0.2.2离线发布验收报告_2026-11-02.md`
- `outputs/0.2.2阶段I提示词_2026-11-02.md`

真实站点仍在阶段 I 单独验收，不能用本阶段夹具和便携包结果替代。

下一动作：执行 0.2.2 阶段 I“真实站点全场景验收”。
本阶段完成后停止，不进入阶段 I。

