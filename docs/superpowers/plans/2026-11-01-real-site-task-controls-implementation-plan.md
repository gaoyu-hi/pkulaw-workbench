# 北大法宝 Electron 采集器 0.2.0 详细实施计划

**依据：** `docs/superpowers/specs/2026-11-01-real-site-task-controls-design.md`  
**计划日期：** 2026-11-01  
**目标版本：** 0.2.0  
**状态：** 任务 9 的离线全量回归、便携构建、发布候选和真实站点第一页只读验收已通过；真实 Word/多页/批量验收等待另行同意  
**实施原则：** 分阶段门禁、失败关闭、不依赖 Agent、不在真实站点进行未授权大批量下载

## 1. 实施范围

本计划覆盖：

- 真实北大法宝 V6 浏览器目标和页面适配；
- 自动暂停、手动暂停、继续、停止和重启恢复；
- 任务多选、归档、恢复、彻底删除和产物回收站；
- 全部年份、指定年份、年份区间；
- 扫描到尾页、指定上限和分页上限决策；
- SQLite v5 迁移；
- 界面、协议、测试、文档和 0.2.0 发布包。

本计划不包含：

- 改写阶段 5 的政府官网回退边界；
- 使用 Agent 或大模型运行采集；
- 调用北大法宝内部 API绕过授权；
- 真实站点大批量下载；
- 商业代码签名。

## 2. 基线和工作区保护

### 2.1 基线

以当前阶段 6 的 `0.1.0` 源码和发布包为基线。

当前项目目录不是 Git 仓库，因此实施前先建立可恢复快照：

```text
work/pkulaw_workbench/build/baseline-0.1.0/
```

快照至少包含：

- `apps/desktop/src`；
- `apps/desktop/tests`；
- `engine/pkulaw_engine`；
- `engine/tests`；
- SQLite schema；
- `package.json`、`pnpm-lock.yaml`；
- Python 依赖锁；
- 构建与验证脚本。

不覆盖用户已有的任务数据目录和输出目录。

### 2.2 统一命令

每个阶段至少运行：

```powershell
pnpm test
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop build
```

涉及 Electron 行为的阶段还要运行对应阶段烟测。

## 3. 目标文件结构

新增或调整：

```text
engine/pkulaw_engine/
├─ controls/
│  └─ task_control.py
├─ selections/
│  ├─ years.py
│  └─ pagination.py
├─ sources/
│  ├─ profiles/
│  │  ├─ fixture.json
│  │  └─ pkulaw-v6.json
│  ├─ selector_profile.py
│  └─ live_page_diagnostics.py
├─ tasks/
│  ├─ archive.py
│  └─ cleanup.py
└─ ...

apps/desktop/src/main/
├─ task-file-cleanup.ts
├─ browser-diagnostics.ts
└─ ...

apps/desktop/src/renderer/src/
├─ TaskControlBar.tsx
├─ TaskSelectionBar.tsx
├─ RecycleBinPanel.tsx
├─ YearRangeControl.tsx
├─ PageLimitControl.tsx
└─ ...
```

不要求最终文件路径完全一致，但必须保持同类职责分离，不能继续把状态机、删除事务和选择器逻辑堆入现有单文件。

## 4. 阶段 A：浏览器绑定和真实 V6 适配

### A1. 浏览器目标指纹

涉及文件：

- `apps/desktop/src/main/browser-host.ts`
- `apps/desktop/src/shared/protocol.ts`
- `engine/pkulaw_engine/browser_bridge.py`
- `apps/desktop/src/main/ipc.ts`

任务：

1. 仅在 `dom-ready` 后读取当前 WebContents URL。
2. 连接请求增加 `targetUrl`、`sessionKey` 和当前 Electron 网页视图标识。
3. Python 选择标签页时排除应用自身 `file://` 渲染页。
4. 目标选择不再限定入口域名，跨域登录跳转后保持标签页句柄。
5. 页面状态刷新在成功连接后执行；页面加载回调不得先调用依赖连接的状态同步。
6. BrowserHost 记录 `targetBound=true` 和实际绑定 URL。
7. 前端分别显示：
   - Python 后端；
   - 网页视图；
   - Python 绑定；
   - 登录状态；
   - 选择器适配。

测试：

- 本地夹具普通入口；
- 本地夹具跨端口/跨主机名重定向；
- 页面慢加载；
- WebContentsView 重建；
- Python 重启后重新绑定；
- 应用渲染页不得被误绑定。

退出条件：

- WebContentsView 已显示登录页面后，连接状态不出现“页面目标 0”；
- 跨域登录后 Python 仍可读取当前页面 DOM；
- 阶段 2 浏览器烟测通过。

### A2. 选择器配置拆分

涉及文件：

- `engine/pkulaw_engine/sources/selectors.json`
- 新增 `sources/profiles/fixture.json`
- 新增 `sources/profiles/pkulaw-v6.json`
- `engine/pkulaw_engine/sources/selector_profile.py`
- `engine/pkulaw_engine/sources/pkulaw.py`
- `engine/tests/test_policy.py`
- 新增选择器测试

任务：

1. 将现有夹具选择器原样迁移到 fixture profile。
2. 创建真实 V6 profile。
3. 真实 profile 最小已知值为：
   - searchInput: `#txtSearch`
   - searchSubmit: `#btnSearch`
   - exactMode: `#accurate`
4. 根据页面标记识别 profile，禁止猜测后静默执行。
5. 每个关键选择器支持固定兜底规则。
6. 任一关键选择器缺失时抛出 `PAGE_SELECTOR_MISMATCH`，不进入下载。
7. 夹具测试不得使用真实 profile；真实 profile 不得依赖夹具 ID。

退出条件：

- 夹具测试全部通过；
- 真实 profile 在本地保存的脱敏 DOM 上通过解析；
- 页面缺少关键控件时明确失败。

### A3. 真实页面 DOM 捕获和适配

涉及文件：

- 新增脱敏 HTML 夹具；
- `engine/pkulaw_engine/sources/pkulaw.py`；
- `engine/tests/`；
- 诊断界面。

前置条件：

- 用户在 Electron 内完成学校登录；
- 当前标签已成功绑定；
- 只读操作，不触发 Word 下载。

任务：

1. 捕获登录后的搜索首页、第一页结果、详情页和分页 DOM。
2. 保存前删除 Cookie、账号名、用户 ID、机构额度文本和个人信息。
3. 识别并记录：
   - 中央/地方法规入口；
   - 年份、省份、时效性筛选控件；
   - 不分组状态；
   - 每页条数；
   - 结果序号、标题、政策 ID、链接；
   - 总页数；
   - 详情标题、机关、日期、文号和时效性；
   - Word 下载入口；
   - 同名候选版本；
   - 额度、登录和验证码提示。
4. 为每个不稳定元素增加固定 DOM 结构兜底。
5. 使用脱敏 HTML 编写离线适配测试。

真实站点门禁：

1. 选择一条明确标题；
2. 只检索并读取第一页；
3. 不下载 Word；
4. 保存截图、选择器诊断和任务事件；
5. 通过后才允许进行一条 Word 下载验证。

退出条件：

- 适配诊断所有关键项通过；
- 真实页面能稳定解析搜索结果；
- 没有发生任何真实批量下载。

## 5. 阶段 B：统一任务控制状态机

### B1. 公共控制器

涉及文件：

- 新增 `engine/pkulaw_engine/controls/task_control.py`
- `single_task.py`
- `batch_task.py`
- `processing_task.py`
- `application.py`
- `storage.py`
- `protocol.ts`

任务：

1. 用统一控制器实现：
   - `pause_requested`
   - `resume_requested`
   - `stop_requested`
   - `wait_if_paused()`
   - `check_stop()`
   - `manual_wait()`
2. 增加状态：
   - `pausing`
   - `paused`
   - `stopping`
3. 暂停和停止只允许在原子步骤边界生效。
4. 写入 SQLite checkpoint：
   - 当前关键词；
   - 当前子库；
   - 当前年份；
   - 当前页；
   - 当前政策 ID；
   - 当前批次 ID；
   - 当前动作。
5. 应用正常关闭时先设置停止信号，等待线程退出。
6. 应用异常重启后将残留活动状态转换为 `stopped`，错误码 `APPLICATION_INTERRUPTED`。

测试：

- 运行中暂停后不再发出网站请求；
- 当前 Word 下载和校验完成后才暂停；
- 当前 ZIP 下载和校验完成后才暂停；
- 继续后不重新开始关键词；
- 停止后线程退出；
- 应用关闭无残留进程；
- 重启后断点可读。

### B2. 单篇和批量接入

涉及文件：

- `single_task.py`
- `batch_task.py`
- `application.py`
- `apps/desktop/src/main/ipc.ts`
- `apps/desktop/src/preload/index.ts`
- `SingleCollectorPanel.tsx`
- `BatchCollectorPanel.tsx`
- `TaskCenterPanel.tsx`

接口：

```text
single.pause / single.resume / single.stop
batch.pause / batch.resume / batch.stop
data.pause / data.resume / data.stop
```

现有 `single.continue` 和 `batch.continue` 保留给自动暂停后的“已处理并继续”。

任务：

1. 暂停按钮先显示“正在暂停”。
2. 进入 `paused` 后显示“继续”。
3. 停止按钮先显示“正在停止”。
4. 禁用重复点击，防止产生多个控制请求。
5. 继续前重新验证浏览器绑定、登录和选择器。
6. UI 显示暂停原因、当前步骤和当前断点。
7. 统一单篇、批量、处理任务的状态文案。

测试：

- 自动登录暂停；
- 手动暂停；
- 自动暂停后继续；
- 手动暂停后继续；
- 停止；
- 进程重启恢复；
- 连续点击按钮不会重复操作。

退出条件：

- 暂停、继续、停止三套行为在 UI 和日志中明确可辨；
- 旧的停止断点测试继续通过。

## 6. 阶段 C：任务记录管理与删除

### C1. SQLite v5

涉及文件：

- `engine/pkulaw_engine/storage.py`
- `engine/tests/test_storage.py`

任务：

1. `SCHEMA_VERSION` 升级到 5。
2. 新增：

```sql
ALTER TABLE tasks ADD COLUMN archived_at TEXT;
ALTER TABLE tasks ADD COLUMN archive_reason TEXT;
```

3. `list_tasks` 默认排除 `archived_at IS NOT NULL`。
4. 增加 `include_archived` 参数。
5. 增加归档、恢复和永久删除事务。
6. 永久删除依靠既有外键级联。
7. 迁移测试覆盖 v1–v4。

### C2. 后端删除接口

接口：

```text
task.archive { taskIds }
task.restore { taskIds }
task.cleanup-preview { taskIds }
task.purge { taskIds }
```

任务：

1. 运行中任务拒绝归档/删除。
2. 批量操作在单个 SQLite 事务中完成。
3. cleanup-preview 返回：
   - 任务 ID；
   - 已知文件路径；
   - 文件是否存在；
   - 共享父目录是否受影响。
4. 只返回数据库登记的产物。
5. 不返回输出父目录本身。
6. 删除事件写入审计日志。

### C3. Windows 回收站

涉及文件：

- 新增 `apps/desktop/src/main/task-file-cleanup.ts`
- `ipc.ts`

任务：

1. Electron 调用 `shell.trashItem`。
2. 对重复路径去重。
3. 文件不存在视为处理成功。
4. 任一实际删除失败时计划中止。
5. 全部处理结束后才调用 `task.purge`。
6. 删除共享父目录、磁盘根、用户主目录一律拒绝。
7. 两阶段操作幂等，可重试。

测试：

- 临时目录的文件移入回收站；
- 共享父目录其他文件不受影响；
- 文件占用失败时任务保留；
- 部分失败后重试；
- 记录删除但文件保留；
- 归档、恢复、彻底删除。

### C4. 任务选择界面

涉及文件：

- `TaskCenterPanel.tsx`
- `SingleCollectorPanel.tsx`
- `BatchCollectorPanel.tsx`
- 新增 `RecycleBinPanel.tsx`
- 样式文件

任务：

1. 任务行复选框。
2. “全选当前筛选结果”。
3. 批量操作栏显示选中数量。
4. 单个任务菜单和批量菜单。
5. 删除确认界面明确：
   - 归档（可恢复）；
   - 保留产物永久删除；
   - 产物移入 Windows 回收站。
6. 回收站显示恢复和彻底删除。
7. 删除当前选中任务后自动选择下一条或清空详情。

退出条件：

- 多选、归档、恢复和两种彻底删除全部通过；
- 用户输出父目录不会被删除。

## 7. 阶段 D：年份与分页

### D1. 共享类型和旧参数兼容

新增 TS/Python 类型：

```ts
type YearSelection =
  | { mode: "all" }
  | { mode: "single"; year: number }
  | { mode: "range"; startYear: number; endYear: number };

type PageSelection =
  | { mode: "last" }
  | { mode: "limit"; maxPages: number };
```

兼容规则：

- 旧字符串 `year` → 全部年份或单一年份；
- 旧数字 `maxPages` → `mode=limit`；
- 新任务同时保存结构化选择和新字段。

### D2. 年份展开

涉及文件：

- 新增 `engine/pkulaw_engine/selections/years.py`
- `single_task.py`
- `batch_task.py`
- 两个采集表单
- 新增 `YearRangeControl.tsx`

任务：

1. 默认年份范围 2000 至当前年份。
2. 校验四位年份和起止顺序。
3. 区间按升序展开。
4. 每个“关键词 × 子库 × 年份”保存独立检查点。
5. 每一年记录：
   - 待执行；
   - 无结果；
   - 已完成；
   - 暂停；
   - 失败。
6. 最终政策按稳定 ID 去重。
7. 报告增加年度统计。

测试：

- 全部年份；
- 单一年份；
- 2000–2026；
- 起止反序；
- 非四位输入；
- 年份筛选缺失；
- 某年无结果继续；
- 某年额度耗尽停止整体；
- 多年度断点恢复。

### D3. 尾页和上限

涉及文件：

- 新增 `engine/pkulaw_engine/selections/pagination.py`
- `sources/pkulaw.py`
- `single_task.py`
- `batch_task.py`
- 新增 `PageLimitControl.tsx`

任务：

1. 默认 `pageMode=last`。
2. 每个检索组合读取自己的 `totalPages`。
3. `last` 扫描到该组合尾页。
4. `limit` 且总页数不超过上限时正常扫描。
5. `limit` 且总页数超过上限时：
   - 进入 `waiting_manual`；
   - 错误码 `PAGE_LIMIT_REACHED`；
   - 保存已扫描页数和未扫描页数；
   - UI 提供“继续到尾页”和“停止任务”。
6. 页面不前进、页码重复、结果签名不再变化时自动暂停。
7. 批量首屏显示预计页数、政策行数和批次数。

测试：

- 单页结果；
- 多页到尾页；
- 上限等于总页数；
- 上限小于总页数；
- 继续到尾页；
- 停止并保留断点；
- 页码不前进；
- 总页数变化；
- 多个关键词/子库/年份的组合。

退出条件：

- 2026–2010 的硬编码完全删除；
- 指定年份和区间可用；
- 页数不再默认 5；
- 尾页和上限行为通过夹具测试。

## 8. 阶段 E：错误体验和诊断

任务：

1. 浏览器状态拆分为四项：
   - 后端已连接；
   - 网页已加载；
   - Python 已绑定；
   - 登录与页面适配可用。
2. 任务页面显示具体错误：
   - 当前没有绑定网页；
   - 学校登录跳转中；
   - 搜索按钮未找到；
   - 年份筛选项不可用；
   - 分页未前进；
   - 下载入口未找到。
3. 适配诊断提供“复制诊断信息”，默认不包含 Cookie 和个人信息。
4. 任务启动和重试按钮受适配诊断门禁控制。
5. 失败任务不应被快速重复创建；连续失败时提示先修复连接或适配。

退出条件：

- 用户能从界面判断失败属于登录、浏览器绑定、页面适配、额度还是任务本身；
- 不再出现“已登录但页面目标为 0”却可以启动任务的状态。

## 9. 阶段 F：回归、真实站点门禁和发布

### F1. 自动化测试

必须通过：

```text
Python 单元/集成测试
Vitest
TypeScript node/web 类型检查
Electron production build
阶段 2/3/4/5/6 烟测
新增控制、删除、年份、分页烟测
SQLite v4 → v5 迁移
便携包直接启动
一键启动器
强制退出和重启恢复
无 Electron/Python 残留
```

新增夹具：

- 跨域登录跳转；
- 慢加载页面；
- 手动暂停；
- 自动暂停；
- 多年度结果；
- 尾页；
- 页数上限；
- 已归档任务；
- 任务产物回收站。

### F2. 真实站点验证

顺序不可跳过：

1. 用户登录学校入口；
2. 适配诊断通过；
3. 精确标题第一页只读检索；
4. 一条 Word 下载；
5. 一个明确的单年度、第一页小任务；
6. 经用户再次同意后，才验证小批量 ZIP；
7. 不执行尾页真实大批量下载。

真实站点记录必须与夹具测试分开：

- 测试时间；
- 机构；
- 页面 URL；
- 选择器 profile 版本；
- 检索条数；
- 下载条数；
- 额度变化；
- 是否人工处理滑块；
- 截图与日志路径。

### F3. 发布

1. 版本改为 `0.2.0`。
2. 更新 `PROJECT_STATE.md`。
3. 写 0.2.0 测试记录与验收报告。
4. 更新便携版和源码包。
5. 重新生成 SHA-256。
6. 验证发布包不含开发依赖和运行夹具。
7. 保留 0.1.0 发布包作为回退版本。

## 10. 推荐执行拆分

为控制上下文和执行风险，分成以下独立任务：

| 任务 | 内容 | 进入条件 |
| --- | --- | --- |
| 任务 1 | 浏览器目标绑定和连接状态 | 设计批准 |
| 任务 2 | fixture/V6 选择器拆分和只读诊断 | 任务 1 通过 |
| 任务 3 | 真实页面脱敏 DOM 和选择器补齐 | 任务 2 通过，用户登录确认 |
| 任务 4 | 公共控制状态机和单篇接入 | 任务 3 通过 |
| 任务 5 | 批量/数据处理接入和界面统一 | 任务 4 通过 |
| 任务 6 | SQLite v5、任务归档/删除/回收站 | 任务 4 通过 |
| 任务 7 | 年份区间和逐年份执行 | 任务 5 通过 |
| 任务 8 | 尾页、上限和规模确认 | 任务 7 通过 |
| 任务 9 | 全量回归、真实门禁和 0.2.0 发布 | 任务 1–8 通过 |

每个任务完成后：

1. 运行自身测试；
2. 更新 `PROJECT_STATE.md`；
3. 写对应测试记录；
4. 更新下一任务提示；
5. 等待用户确认后继续。

## 11. 回滚和兼容

- 数据库迁移前备份当前 `pkulaw.sqlite3`。
- 迁移只能前进；应用升级后不保证 0.1.0 能读取 v5。
- 发布前保留 v4 数据库副本。
- 真实站点适配失败时保持登录和任务数据，不回退到夹具配置执行真实任务。
- 删除操作先归档；永久删除必须二次确认。
- 0.2.0 发布验收失败时保留 0.1.0 便携包。

## 12. 全局完成标准

- 真实 V6 浏览器绑定、登录和适配诊断通过。
- 手动暂停、自动暂停、继续、停止和重启恢复通过。
- 任务多选、归档、恢复和回收站删除通过。
- 年份全部/单年/区间通过，默认 2000 至当前年份。
- 尾页和指定上限通过，页数不再默认 5。
- 指定上限触发时不漏抓、不假完成。
- SQLite v4 到 v5 迁移无损。
- 阶段 2–6 和历史回归全部通过。
- 真实站点只按批准范围执行，不多做批量下载。
- 正常运行不调用 Agent。

