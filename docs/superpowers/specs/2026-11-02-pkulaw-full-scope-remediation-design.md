# 北大法宝全量正式场景可靠性整改设计

**日期：** 2026-11-02  
**目标版本：** `0.2.2`  
**目标：** 不再用窄场景替代验收，以真实关键词“碳中和”的正式全范围单篇、批量、数据处理和来源治理链路作为最终验收主线。

## 背景与证据

2026-11-02 的真实站点测试暴露了以下已复现问题：

1. 翻页点击在结果页重绘后可能失效；受控提交 `#right_form` 可继续到下一页。
2. 表单提交后 URL 变为 `/law/search/RecordSearch`，现有选择器 profile 将其误判为未知页面。
3. 真实滑块由任务适配器识别，但 BrowserHost 只检查 `#captcha-overlay`，两处状态不一致。
4. 单篇扫描进度只按搜索组合计算，长任务长期停在 10%。
5. 网站目录弹窗只有原生 Excel；真实文件固定为 BIFF `.xls`。
6. UI 提供 XLSX/HTML-XLS，但没有区分网站原生格式和本地转换格式。
7. 旧版 `.doc` 在发布环境中缺少可靠转换器；批量数据处理 0/3 成功。
8. 详情页条款位于 `.tiao-wrap .kuan-content`，容器的 `innerText` 会遗漏正文。
9. `partial` 数据处理中 `processed=0`、`pending=3`，但进度仍为 100%。
10. “完整标题”是单篇默认模式，普通关键词误选后会白扫全部结果再返回 `NO_EXACT_MATCH`。

正式全量验收关键词固定为“碳中和”。允许使用真实下载额度；用户本人完成登录、滑块和验证码；不得绕过机构额度，不保存账号密码或导出 Cookie。

## 设计目标

- 所有真实页面状态由同一 access-state 模块产生。
- 分页导航是一个可审计事务，支持点击失败后的受控表单回退。
- 下载格式契约区分网站原生格式和本地派生格式。
- 单篇和批量使用同一旧版 DOC 正文转换链。
- 详情正文必须完整、可判定，不以“有文字”代替“全文完整”。
- 原件来源、正文来源、提取工具、完整性和核验状态相互独立。
- 任务同时展示执行进度和成功/失败/待核验数量。
- 正常运行时仍不依赖 Agent、Codex 或在线大模型。

## 非目标

- 不改变政府官网回退边界。
- 不把宽泛关键词自动回退政府官网。
- 不绕过登录、滑块、验证码、校园权限或机构额度。
- 不把转换得到的 XLSX/HTML-XLS 称为网站原生文件。
- 不删除已有失败证据或历史任务。

## 核心组件

### 1. AccessState

新增 `engine/pkulaw_engine/sources/access_state.py`。

```python
@dataclass(frozen=True)
class AccessState:
    url: str
    captcha_visible: bool
    captcha_selector: str
    captcha_box: dict[str, float]
    login_visible: bool
    authenticated: bool
    quota_visible: bool
    page_error: bool
    checked_at: str
```

`read_access_state(tab, selectors)` 是浏览器状态和任务适配器共享的唯一入口。验证码按所有配置选择器联合检测，并记录实际命中的 selector、宽高、display、visibility 和 opacity。只有真实可见节点才报告验证码。

### 2. ResultPageNavigator

新增 `engine/pkulaw_engine/sources/page_navigation.py`。

职责：

- 识别 `/law/lar` 和 `/law/search/RecordSearch` 两种结果路由；
- 正常点击“下一页”；
- 在限定时间内未推进时执行一次 `#right_form` 受控提交；
- 设置 `OldPageIndex`、`Pager.PageIndex`、`PageIndex`；
- 等待页码、行号、结果签名和 SearchSession 连续稳定；
- 禁止重复点击、重复提交和跨检索消费。

### 3. CatalogFormatContract

新增 `engine/pkulaw_engine/catalog_formats.py`。

```python
CatalogOutputFormat = Literal["native-xls", "derived-xlsx", "compat-html-xls"]

@dataclass(frozen=True)
class CatalogFormatContract:
    requested: CatalogOutputFormat
    native: bool
    source_suffixes: tuple[str, ...]
    final_suffix: str
```

网站原生目录固定请求 `catalogExcel`，源文件契约固定为 `.xls` 和 BIFF 签名。XLSX 与 HTML-XLS 为派生格式；最终报告必须写明 `native=false` 和转换来源。

### 4. LegacyDocumentText

扩展 `engine/pkulaw_engine/processors/documents.py`，优先使用发布包内置的离线 DOC 转换器。运行时不要求用户安装 Python、Word 或 pywin32。

```python
@dataclass(frozen=True)
class DocumentExtractionResult:
    text: str
    artifact_method: str
    text_source_type: str
    converter_name: str
    converter_version: str
```

单篇和批量都调用同一入口。Word COM 只能作为有明确诊断的可选后备，不能是发布环境的唯一方案。

### 5. BodyCompleteness

新增 `engine/pkulaw_engine/body_quality.py`。

详情提取优先组合：

- 标题；
- 直接文本节点；
- `p.navzhang` 章节标题；
- `.tiao-wrap .kuan-content` 条文；
- 表格；
- 其他正文段落。

完整性结果：

- `complete`
- `suspicious`
- `failed`

验证至少包括：正文长度、章节数量、条文数量、连续编号、章节后是否有正文、是否在内容结束前截断。

### 6. SourceProvenance

SQLite 从 v5 升级到 v6，为 `policy_documents` 增加：

- `text_source_type`
- `extraction_method`
- `converter_name`
- `converter_version`
- `body_completeness`
- `review_status`

`source_type` 继续表示原件来源。正文提取失败不得把正式原件来源改写为“待核验”。

### 7. ProgressSnapshot

新增 `engine/pkulaw_engine/progress.py`。

```python
@dataclass(frozen=True)
class ProgressSnapshot:
    overall: float
    stage: str
    completed_units: int
    total_units: int
    succeeded: int
    failed: int
    pending_review: int
```

进度写入 checkpoint 的 `progressSnapshot`，协议透传，UI 同时显示进度和计数。terminal partial 不再默认显示 100%。

## 数据流

1. 用户提交正式“碳中和”任务。
2. 预检显示各组合结果数、页数和规模。
3. 每页导航通过 ResultPageNavigator 和 SearchSession 双重验证。
4. 遇到真实验证码时 AccessState 统一暂停；用户完成后重新采样并恢复。
5. 单篇下载创建 Word intent；批量下载先创建原生目录 intent，再在格式校验通过后创建 ZIP intent。
6. 旧版 DOC 由内置转换器提取正文。
7. 详情正文仅在通过完整性门禁后标记 `complete`。
8. SQLite、TXT、CSV、XLSX、证据和报告原子登记。

## 错误契约

- `CAPTCHA_VISIBLE`
- `CAPTCHA_CLEARED`
- `PAGE_TRANSITION_UNCONFIRMED`
- `RESULT_ROUTE_UNRECOGNIZED`
- `CATALOG_NATIVE_FORMAT_MISMATCH`
- `CATALOG_CONVERSION_FAILED`
- `DOC_CONVERTER_UNAVAILABLE`
- `DOC_TEXT_EXTRACTION_FAILED`
- `BODY_INCOMPLETE`
- `TASK_PROGRESS_INCONSISTENT`

错误卡必须包含阶段、断点、实际文件、预期契约、实际契约、已完成数量和恢复建议。

## UI 规则

- 单篇默认“关键词”；“完整标题”改名为“精确完整标题”。
- 全量任务启动前显示 0 下载预览：组合数、结果数、页数、预计批次数。
- 验证码未消失时禁用“继续”。
- 浏览器和任务卡使用同一验证码状态。
- 目录格式显示“网站原生 XLS”“本地转换 XLSX”“兼容 HTML-XLS”。
- 显示正文来源和完整性，不只显示“processed”。
- 内置网页支持 50%–200% 缩放，按机构 Session 保存。

## 测试策略

开发期使用离线页面、真实 `.doc` 证据样本和真实 `.xls` 证据样本。最终验收只有一个真实阶段，直接运行正式全范围“碳中和”场景。

全量真实验收必须分别建立并执行两个独立关键词任务：

- I-S“逐篇关键词”：关键词“碳中和”、关键词模式、中央+地方、全部年份、每页 100、扫描到尾页；完整执行 Word 下载和 TXT/CSV/XLSX/报告处理；
- I-B“批量关键词”：使用相同“碳中和”正式范围单独建立批量任务，按 50 条批次执行原生 XLS 和 Word ZIP，并单独执行数据处理；
- 两个任务必须有不同 taskId、独立任务记录、独立结果统计和独立产物核验；
- 不得用逐篇任务代替批量任务，也不得用批量任务代替逐篇任务；
- 可比对同一稳定政策 ID 的 Word SHA-256，但不得通过复用另一模式已下载文件来省略当前模式的真实下载。

其他真实验收包括：

- XLSX/HTML-XLS 只从原生 XLS 本地派生并明确标识；
- 所有产物、SQLite、证据和 SHA-256 一致；
- 应用关闭后无 Electron/Python 残留。

