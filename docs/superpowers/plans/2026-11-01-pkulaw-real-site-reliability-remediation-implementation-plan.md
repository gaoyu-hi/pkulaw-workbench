# 北大法宝真实站点可靠性与批量下载修复实施计划

**日期：** 2026-11-01  
**目标版本：** `0.2.1`  
**设计规格：** `docs/superpowers/specs/2026-11-01-pkulaw-real-site-reliability-remediation-design.md`  
**执行方式：** 分阶段、测试先行、每阶段单独验收  
**真实站点原则：** 允许使用下载额度，但不执行无边界批量下载，不绕过登录、滑块、验证码或机构额度

**执行状态：** 阶段 A、B、C、D、E、F、G、H、I、J 已完成（2026-11-01）；
阶段 K 待用户明确授权后开始

## 执行总则

1. 每个阶段先添加能够复现问题的测试，再修改实现。
2. 每个阶段完成后运行该阶段测试和相关回归测试。
3. 未完成阶段 A–E 的离线验证前，不重新执行真实批量下载。
4. 真实站点测试只使用经过只读预检的关键词、标题和页码。
5. 每个真实场景单独记录任务 ID、下载次数、额度变化、文件哈希和结果。
6. 任一真实场景出现登录、滑块、验证码、额度、来源异常或页面结构异常，立即停止整个场景。
7. 已知失败未修复时不得继续扩大下载范围。
8. 所有文档、日志和证据继续遵守不保存密码、不导出 Cookie、脱敏 HTML 和截图要求。

---

## 阶段 A：建立基线、覆盖率与故障夹具

### 目标

先在 `0.2.0` 代码上稳定复现真实问题，形成后续修复的测试基线。

### 工作内容

- 增加 Python `coverage.py` 配置；
- 增加 Vitest `@vitest/coverage-v8` 配置；
- 为关键模块设定分支覆盖率目标：
  - `sources/pkulaw.py`
  - `sources/selector_profile.py`
  - `policy.py`
  - `single_task.py`
  - `batch_task.py`
  - `validators/archive.py`
  - `validators/catalog.py`
  - `apps/desktop/src/main/download-manager.ts`
- 关键模块分支覆盖率不得低于 85%；
- 新增和修改的关键逻辑不得降低阶段 A 记录的覆盖率基线；
- 建立 `docs/0.2.1_FEATURE_ACCEPTANCE_MATRIX.md`，逐项关联单元测试、集成测试、烟测和真实验收；
- 新增以下夹具：

| 文件 | 模拟内容 |
|---|---|
| `stale-page5.html` | 新检索提交后旧第 5 页继续存在 |
| `same-url-slow-search.html` | URL 不变，新结果延迟替换 |
| `same-query-rerun.html` | 相同查询和相同结果重新搜索 |
| `grouped-results.html` | 默认按效力位阶分组 |
| `custom-page-size.html` | 自定义“条/页”下拉和回读 |
| `filter-tree.html` | 年份、省份、时效性树形筛选 |
| `selector-flicker.html` | 控件短暂消失后恢复 |
| `selector-missing.html` | 必需控件持续缺失 |
| `download-out-of-order.html` | 无关下载先完成 |
| `batch-modal-real-shape.html` | 真实批量格式弹层 |
| `zip-resource-limit.zip` | 由测试生成：超大成员、高压缩比和过多成员 |
| `excel-injection.xlsx` | 由测试生成：CSV/XLSX 公式注入字符 |

### 新增测试

- 旧页 `5/5` 不得成为新查询结果；
- 相同查询重跑不能因签名相同而误判为无更新；
- `libraryRoot` 短暂缺失后恢复；
- `libraryRoot` 持续缺失；
- 两个逐篇任务并发启动；
- 两个批量任务并发启动；
- 快速双击启动；
- 下载事件乱序；
- 真实 `.doc` 和 `.docx`；
- 自定义页大小未生效；
- 分组到不分组的切换；
- 年份、省份、时效性筛选；
- CSV 公式注入；
- ZIP 大小、压缩比和成员数限制。

### 验证

```text
pnpm test
```

### 完成条件

- 新测试在旧实现中能稳定失败；
- 每个真实问题都有对应用例；
- 测试失败信息能够明确指出契约或页面状态错误；
- 形成覆盖率基线报告。

**阶段 A 结果：已完成。** 正常测试保持绿色；已知缺陷基线和覆盖率门槛按预期为红色。详见 `docs/0.2.1_PHASE_A_TEST_REPORT.md`。

---

## 阶段 B：统一 Word 格式校验

### 目标

让单篇任务正确处理站点真实返回的 OLE `.doc` 和 OOXML `.docx`。

### 实现

- 在 `engine/pkulaw_engine/policy.py` 增加统一入口：

```text
validate_downloaded_word(path)
```

- 根据实际扩展名分派：
  - `.docx` 校验 ZIP、必要文件、CRC；
  - `.doc` 校验 OLE 复合文档签名；
- `single_task.py` 改用统一入口；
- 未知扩展名或无效文件返回 `INVALID_WORD_FILE`；
- 校验失败时保存实际路径、扩展名、文件大小、SHA-256 和隔离状态；
- 保持批次 ZIP 中的 `.doc/.docx` 最终身份映射不变。

### 测试

- OLE `.doc` 通过；
- DOCX 通过；
- 空文件拒绝；
- 伪 OLE 文件拒绝；
- 伪 DOCX 文件拒绝；
- 未知扩展名拒绝；
- 真实已下载 3 个 `.doc` 验证均通过统一入口。

### 回归

```text
python -m unittest engine/tests/test_policy.py -v
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage5
```

### 完成条件

- 单篇 `.doc` 可以完成正式文件移动和 SQLite 登记；
- `.docx` 现有行为不回归；
- 失败样本仍被保留用于诊断。

**阶段 B 结果：已完成。** OLE `.doc`、OOXML `.docx`、无效文件和隔离证据均已通过自动化验证。详见 `docs/0.2.1_PHASE_B_TEST_REPORT.md`。

---

## 阶段 C：全局浏览器租约与启动幂等

### 目标

保证同一时间只有一个任务操作内置浏览器，并消除双击或重复请求造成的并发启动。

### 实现

- 新增 `engine/pkulaw_engine/browser_lease.py`；
- 获取租约时携带 `taskId` 和 `mode`；
- 逐篇、批量和浏览器型数据处理均使用租约；
- 应用层在启动任务前原子获取租约；
- 任务线程 `finally` 释放租约；
- 应用重启时不恢复旧租约；
- 同任务重复启动返回现有任务详情；
- 不同任务并发启动返回 `BROWSER_LEASE_BUSY`；
- UI 显示“内置浏览器正由哪个任务使用”；
- 启动按钮增加请求级幂等键，避免快速双击创建两个任务。

### 测试

- 单篇 A 运行时启动单篇 B 被拒绝；
- 批量 A 运行时启动批量 B 被拒绝；
- 逐篇和批量互斥；
- 浏览器型数据处理和采集互斥；
- 快速双击只创建一个任务；
- 任务失败、停止、完成和异常都能释放租约；
- Python 崩溃后重启不会残留有效租约。

### 回归

```text
pnpm test
pnpm --dir apps/desktop smoke:stage2
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
```

### 完成条件

- 并发测试全部通过；
- BrowserHost 和任务中心能够显示租约状态；
- 不再存在两个采集任务同时驱动同一标签页的路径。

**阶段 C 结果：已完成。** 全局租约、线程退出清理、同任务启动幂等、请求级 `operationId`、BrowserHost 状态和采集界面忙碌提示均已接入；阶段 2、3、4 Electron 烟测及阶段 5 数据处理回归通过。详见 `docs/0.2.1_PHASE_C_TEST_REPORT.md`。

---

## 阶段 D：检索事务和旧 DOM 防护

### 目标

消除旧页码、旧总页数和旧结果行污染新检索。

### 实现

- 在 `PkulawAdapter` 增加 SearchTransaction；
- 检索前记录旧页面快照和一次性 DOM token；
- 提交后等待：
  - 搜索条件区域显示本次关键词；
  - 正确检索字段和子库；
  - 一次性 DOM token 消失或结果容器被替换；
  - 页码为 1；
  - 页面加载状态结束；
  - 连续两次 SearchSession 一致；
- 返回 `SearchSession` 而不是裸结果对象；
- 单篇和批量 checkpoint 写入 `searchIdentity`；
- 旧任务恢复时如果缺少 SearchSession，执行一次受控重新检索；
- 错误使用：
  - `SEARCH_TRANSITION_UNCONFIRMED`
  - `RESULTS_STALE`

### 测试

- 旧页为第 5 页，新查询延迟 3 秒；
- 新查询期间旧页仍可读取；
- 新查询最终替换旧容器；
- 相同关键词重复搜索；
- 检索超时；
- 检索条件区域不更新；
- 旧页码 `5/5` 不得触发“已到尾页”；
- 旧结果不得创建任务项；
- 旧结果不得触发详情或下载。

### 回归

```text
pnpm test
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
```

### 完成条件

- 分页上限冷启动测试稳定返回 `PAGE_LIMIT_REACHED`；
- 任何旧 DOM 场景均不会点击详情或下载；
- SearchSession 能经 checkpoint 恢复。

**阶段 D 结果：已完成。** `SearchSession`、旧容器一次性标记、检索条件/子库/检索字段回读、连续稳定采样、checkpoint 身份、旧任务受控重新检索和前序结果消费门禁均已接入；阶段 2、3、4 Electron 烟测通过。详见 `docs/0.2.1_PHASE_D_TEST_REPORT.md`。

---

## 阶段 E：真实筛选器、每页条数与不分组

### 目标

让真实 V6 页面上的筛选、页大小和分页器全部可操作、可回读。

### 实现

- 更新 `pkulaw-v6.json`，版本升级为 `pkulaw-v6-2026-11-01-3`；
- 增加页大小触发区、选项和当前值诊断；
- 增加年份、省份、时效性树形选项解析；
- 增加不分组控件和分页控件诊断；
- 基础查询完成后按顺序应用筛选；
- 每次筛选后等待 SearchSession 更新；
- 每页条数操作真实 `.articleSelect`；
- 验证可见页大小、隐藏页大小和当前行数；
- 自动切换到“不分组”；
- 选项缺失返回：
  - `PAGE_SIZE_NOT_APPLIED`
  - `FILTER_NOT_APPLIED`
  - `RESULT_GROUPING_UNAVAILABLE`

### 测试

- 年份单年；
- 年份区间逐组合；
- 省份；
- 时效性；
- 请求 10/20/50/100 条；
- 页大小实际值不一致；
- 默认分组；
- 成功切换不分组；
- 无法切换不分组；
- 分页器 `页数 1/N`；
- 尾页 14 条等不规则行数。

### 回归

```text
pnpm test
pnpm --dir apps/desktop smoke:stage2
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
```

### 完成条件

- 所有真实控件在离线 V6 夹具中可操作；
- 请求参数与实际页面状态不一致时不会继续；
- 5 页结果可以逐页到尾页。

**阶段 E 结果：已完成。** V6 profile 已升级到 `pkulaw-v6-2026-11-01-3`；真实筛选树门禁、可见页大小回读、实际行数校验和“不分组”稳定切换均已接入，阶段 2、3、4 Electron 烟测通过。详见 `docs/0.2.1_PHASE_E_TEST_REPORT.md`。

---

## 阶段 F：选择器稳定性与下载意图

### 目标

区分短暂重绘和真实结构变化，并确保下载事件属于正确任务。

### 实现

- 新增 `diagnose_for_operation()`；
- 5 秒内每 250 毫秒采样；
- 连续两次稳定才通过；
- 短暂缺失后恢复自动继续；
- 持续缺失返回 `PAGE_SELECTOR_UNSTABLE` 或 `PAGE_SELECTOR_MISMATCH`；
- 新增 `engine/pkulaw_engine/download_intent.py`；
- 每次下载创建 intent；
- started/completed 必须属于同一 `downloadId`；
- 文件扩展名和来源必须匹配 intent；
- 超预期下载次数硬停止；
- 无关下载只记录告警；
- `DownloadManager` 增加 Windows 保留文件名、尾随点空格和安全长度处理。

### 测试

- 控件瞬时缺失后恢复；
- 控件反复抖动；
- 控件永久缺失；
- 无关下载先完成；
- 预期下载后完成；
- 交叉下载；
- failed 后另一个 completed；
- 下载超时；
- `CON`、`NUL`、尾随句点和空格文件名；
- 下载目录不存在或不可写。

### 回归

```text
pnpm test
pnpm --dir apps/desktop smoke:stage2
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
```

### 完成条件

- 真实页面短时重绘不再导致 `libraryRoot` 误报；
- 每个下载事件都能回溯到 task 和 action；
- 没有不受控下载。

**阶段 F 结果：已完成。** 操作级选择器稳定探针、Word/目录/ZIP 下载 intent、乱序事件隔离、Windows 安全文件名和结构化下载目录错误均已接入；阶段 2、3、4 Electron 烟测通过。详见 `docs/0.2.1_PHASE_F_TEST_REPORT.md`。

---

## 阶段 G：批量复用 SearchSession 与下载前身份核验

### 目标

消除批量任务为每个批次重复搜索，并在下载前证明选中行属于当前批次。

### 实现

- `_prepare_batches()` 保存每个检索组合的 SearchSession；
- 同一组合的批次按页码顺序处理；
- 当前页面身份匹配时复用；
- 当前页在目标页之前时向前翻页；
- 需要回到前页或页面身份不匹配时，只执行一次受控重新搜索；
- 下载前核对：
  - 查询；
  - 子库；
  - 年份；
  - 筛选条件；
  - 当前页；
  - 行号；
  - 政策 ID；
  - 标题；
  - 勾选数量；
- 目录未通过校验前不下载 ZIP；
- ZIP 未通过校验前不发布批次完成状态；
- 批量 progress 只按已完成批次推进。

### 测试

- 单条精确标题只搜索必要次数；
- 当前页可直接复用时不再搜索；
- 从第 3 页返回第 1 页时重新检索一次；
- 同一组合多个批次复用同一页面；
- 选中行 ID 被替换；
- 勾选数量多一条或少一条；
- 目录失败不启动 ZIP；
- ZIP 失败不登记完成；
- 重试不重复已完成批次；
- 暂停和停止保持现有安全边界。

### 回归

```text
pnpm test
pnpm --dir apps/desktop smoke:stage4
pnpm --dir apps/desktop smoke:stage5
```

### 完成条件

- 单条真实场景不再发生第二次搜索导致的 `libraryRoot` 竞态；
- 批次选择与目录/ZIP 内容一一对应；
- 失败批次隔离和精确恢复保持有效。

**阶段 G 结果：已完成。** 批量扫描上下文持久化、同页复用、向前翻页、单次重新搜索、批次行列/勾选身份核验、目录后 ZIP 前复核和重试保护均已接入；阶段 2、3、4 Electron 烟测通过。详见 `docs/0.2.1_PHASE_G_TEST_REPORT.md`。

---

## 阶段 H：输出安全、ZIP 资源限制与文件原子性

### 目标

防止不安全电子表格内容、压缩包资源攻击和半完成产物。

### 实现

- `processors/reporting.py` 对所有 CSV/XLSX 文本单元格做公式中和：
  - `=`
  - `+`
  - `-`
  - `@`
  - 前置制表符或回车；
- 保持显示原值，不改变业务文本；
- `validators/archive.py` 增加：
  - 最大压缩文件大小；
  - 最大总解压大小；
  - 最大单成员大小；
  - 最大成员数；
  - 最大压缩比；
  - 嵌套目录深度；
  - Windows 保留名和非法成员名；
- 目录与 ZIP 在完整校验后才进入正式目录；
- CSV、XLSX、TXT、来源证据和报告继续采用临时文件加原子替换；
- 数据发布完成后统一写 artifact 和 SHA-256；
- 失败时保留临时或隔离文件，不覆盖已完成版本。

### 测试

- 公式注入字符；
- 超长单元格；
- ZIP bomb；
- 高压缩比；
- 超过成员数；
- 超长目录；
- Windows 保留文件名；
- 大小写重复文件名；
- 中途失败时旧正式文件不变；
- 最终文件、SQLite 和 report 哈希一致。

### 完成条件

- Excel 打开时不执行数据内容中的公式；
- 资源超限 ZIP 被明确拒绝；
- 所有正式产物保持原子性。

**阶段 H 结果：已完成。** CSV/XLSX 公式中和、XLSX 超长文本处理、ZIP
资源与 Windows 成员名限制、批次目录发布清单、整目录原子发布、artifact
哈希核验和跨阶段故障注入均已接入；阶段 2、3、4、5 Electron 烟测通过。
详见 `docs/0.2.1_PHASE_H_TEST_REPORT.md`。

---

## 阶段 I：UI、诊断与错误恢复

### 目标

让用户看得懂当前正在验证什么、为什么暂停，以及如何恢复。

### 实现

- 任务显示新增阶段：
  - 验证新检索；
  - 应用筛选；
  - 验证页大小；
  - 切换不分组；
  - 选择批次；
  - 校验目录；
  - 校验 ZIP；
- 显示实际查询、子库、年份、页码、总页数、页大小和分组模式；
- 显示浏览器租约持有任务；
- 显示当前下载意图和已发生次数；
- 错误卡提供原因、已完成项和恢复建议；
- 任务中心与单篇/批量页使用同一状态文案；
- 不因短暂 `running → waiting_manual → running` 产生重复记录。

### 测试

- 快速双击启动；
- 连续暂停、停止；
- 租约忙碌；
- 搜索验证失败；
- 页大小失败；
- 筛选失败；
- 下载事件不匹配；
- 恢复后断点展示；
- 高 DPI、中文和长路径。

### 完成条件

- 用户无需读取日志即可判断失败阶段；
- 所有按钮状态和错误含义一致；
- 不出现“已连接但任务无响应”的状态。

**阶段 I 结果：已完成。** 统一状态文案、按钮策略、检索身份、工作流阶段、
浏览器租约、下载意图、暂停原因、错误恢复卡、去重时间线、数据来源与
SHA-256 筛选、设置诊断、长文本复制以及高 DPI 窄窗口布局均已接入；
阶段 2、3、4、5 Electron 烟测通过，阶段 6 诊断和窄窗口烟测通过。
详见 `docs/0.2.1_PHASE_I_TEST_REPORT.md`。

---

## 阶段 J：全量离线回归与发布构建

### 测试命令

```text
pnpm test
pnpm --dir apps/desktop test
pnpm --dir apps/desktop typecheck
pnpm --dir apps/desktop build
pnpm --dir apps/desktop smoke:stage2
pnpm --dir apps/desktop smoke:stage3
pnpm --dir apps/desktop smoke:stage4
pnpm --dir apps/desktop smoke:stage5
pnpm --dir apps/desktop smoke:stage6
pnpm build:portable
pnpm verify:portable
```

### 额外检查

- Python 与 Vitest 覆盖率报告；
- SQLite `integrity_check=ok`；
- schema v5；
- 中文、空格、长路径；
- 干净环境首次启动；
- 无 Python 环境依赖；
- 断网、强制退出和重启恢复；
- 关闭应用后无 Electron/Python 残留；
- 发布包不包含账号、Cookie、真实站点私密响应或测试数据。

### 完成条件

- 所有自动化测试通过；
- 关键模块分支覆盖率达到 85%；
- 便携包验证通过；
- 未通过项目明确列出，不能用夹具结果替代。

**阶段 J 结果：已完成。** 应用、后端和发布元数据已统一为 0.2.1；
Python 227 项测试、Vitest 38 项测试、类型检查、生产构建、
阶段 2–6 Electron 烟测、Python/Vitest 覆盖率、便携包构建、
干净隔离首次启动、断网/强制退出恢复、一键启动器和 SHA-256
复核全部通过。关键模块逐模块分支覆盖均不低于 85%。
阶段 J 自检发现并修复了覆盖钩子误加载和旧运行数据
进入便携 ZIP 的问题。真实站点项目未执行，`F-011` 与 `F-040`
继续保持 `RED`。
详见 `docs/0.2.1_PHASE_J_TEST_REPORT.md` 和
`outputs/0.2.1发布验收报告_2026-11-01.md`。

---

## 阶段 K：真实站点全场景下载验收

### 前置门禁

- 阶段 A–J 全部通过；
- 当前构建版本为 `0.2.1`；
- selector profile 为 `pkulaw-v6-2026-11-01-3`；
- 用户已同意允许使用下载额度；
- 浏览器已由用户完成机构登录；
- 当前无其他采集任务运行。

### K1：连接与只读预检

- 验证后端、网页、Python 绑定、机构登录和选择器；
- 验证当前页面实际每页条数；
- 验证“不分组”；
- 验证年份、省份和时效性筛选；
- 只记录页面状态，不下载。

### K2：单篇精确标题

- 选择已确认的完整中央法规标题；
- 执行完整标题单篇任务；
- 验证 `.doc` 或 `.docx`；
- 验证详情元数据、正式文件名、SHA-256 和 SQLite；
- 记录额度变化。

### K3：单篇关键词

- 先只读选一个首屏不超过 3 条结果的窄关键词；
- 如无合适结果，可调整关键词后重新预检；
- 执行单篇关键词任务；
- 验证每一条 Word 和任务项；
- 不使用宽泛关键词。

### K4：批量精确标题与 Word ZIP

- 使用一个精确标题建立单条批次；
- 下载并校验原生目录；
- 下载并校验 Word ZIP；
- 验证 ZIP CRC、成员、空文件、重复项、路径穿越和身份映射；
- 生成本地 TXT 后复核 SHA-256。

### K5：三种目录格式

分别建立三个独立的小批次任务：

1. XLSX；
2. BIFF XLS；
3. HTML-XLS。

每个任务只使用一个或少量精确标题。三种格式都必须来自真实站点，不得使用格式转换替代。

### K6：批量关键词与地方法规

- 先用只读预检确定窄关键词、省份和年份；
- 目标为首屏 1–3 条；
- 如结果超过 3 条，重新选择关键词或收紧筛选；
- 执行地方法规小批次；
- 验证地区、批次选择、目录和 ZIP。

### K7：双库任务

- 准备一个中央法规精确标题和一个地方法规精确标题；
- 使用“中央 + 地方”组合；
- 验证每个组合独立 SearchSession、批次和断点；
- 验证同政策去重；
- 不扩大到宽泛关键词。

### K8：本地数据结构

- 对单篇和批量产物运行数据处理；
- 验证逐条 TXT；
- 验证总目录 CSV；
- 验证总目录 XLSX；
- 验证任务报告；
- 验证来源、抓取方式、时间、SHA-256 和证据；
- 验证 CSV/XLSX 公式注入防护。

### K9：政府官网精确补全

- 从待核验政策中选取一个北大法宝 `NO_EXACT_MATCH` 的精确标题；
- 如果找不到符合条件的样本，明确记录“本次未触发”，不得伪造；
- 验证官方来源识别、HTML、截图和附件/正文整理标签；
- 验证登录、额度、验证码和同名歧义不会触发回退。

### K10：分页与规模门禁

- 只读多页到尾页；
- 指定上限触发 `PAGE_LIMIT_REACHED` 后停止；
- 大规模查询触发 `BATCH_SCALE_CONFIRMATION_REQUIRED` 后停止；
- 不点击确认继续；
- 记录扫描数和下载数必须为 0（除明确单篇下载场景外）。

### 真实场景记录

每个场景必须记录：

- 场景 ID；
- 时间；
- 关键词或标题；
- 子库、年份、省份、时效性；
- 结果数和总页数；
- 实际页大小；
- 下载文件数；
- 目录格式；
- Word、ZIP、TXT、CSV、XLSX、HTML、PNG 的数量与 SHA-256；
- 任务状态；
- 额度提示；
- 失败原因；
- 截图和脱敏证据路径。

### 终止条件

任一场景出现以下情况时停止：

- 登录失效且无法由用户恢复；
- 滑块或验证码出现；
- 额度提示；
- SearchSession 身份无法确认；
- 页面结构持续不稳定；
- 下载事件不匹配；
- ZIP 校验失败；
- 数据库与文件状态不一致；
- 出现计划外下载。

修复后只能从失败场景重新开始，不重复已完成且已验证的下载。

---

## 阶段 L：0.2.1 发布与收尾

### 工作内容

- 更新版本元数据为 `0.2.1`；
- 更新依赖锁定；
- 生成 Windows 便携包；
- 生成源码和构建脚本包；
- 生成 SHA-256；
- 写阶段测试记录；
- 写真实站点场景验收报告；
- 更新 `PROJECT_STATE.md`；
- 更新使用手册和故障排查；
- 明确记录未通过项和无法验证的环境项。

### 发布条件

- 本地全量回归通过；
- 真实场景矩阵达到目标；
- 所有下载均能追溯到任务和政策；
- 所有正式产物均通过哈希和身份核验；
- 不存在计划外下载；
- 关闭应用后无残留进程；
- 报告不得将夹具结果写成真实站点结果。

---

## 建议执行顺序与停机点

```text
A 基线测试
-> B Word
-> C 租约与幂等
-> D SearchSession
-> E 筛选/页大小/不分组
-> F 稳定性/下载意图
-> G 批量复用
-> H 输出安全
-> I UI
-> J 离线全量回归
-> K 真实全场景验收
-> L 发布
```

在 J 完成前不进入 K。K 中按 K1 到 K10 顺序执行；任何失败先修复并回归，不能跳过失败场景继续消耗额度。

## 最终交付

- `0.2.1` Electron 应用；
- Windows 便携 ZIP；
- 团队部署目录；
- 依赖清单和锁文件；
- 一键启动器；
- 安装、卸载和使用手册；
- 故障排查和数据恢复说明；
- Python、Vitest、Electron 烟测记录；
- 覆盖率报告；
- 真实站点全场景验收报告；
- 关键文件的 SHA-256 清单；
- 更新后的 `PROJECT_STATE.md`。

