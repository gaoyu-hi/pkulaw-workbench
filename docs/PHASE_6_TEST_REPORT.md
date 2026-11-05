# 阶段 6 测试记录

**日期：** 2026-11-01  
**阶段：** 界面、稳定性与团队交付  
**结果：** 通过

## 交付内容

### 界面

- 统一任务中心；
- 任务模式、状态和关键词筛选；
- 逐条状态、错误原因、当前阶段和同名候选；
- 开始、停止、人工继续和失败重试；
- 批量断点查看；
- 任务输出目录和最新任务产物打开；
- 数据与报告页标题、来源和状态筛选；
- Word、TXT、来源证据、CSV、XLSX 和报告受控打开；
- 设置与诊断页；
- 启动预检和可重复运行诊断。

### 稳定性

- Python 后端进程异常退出后自动恢复一次；
- 浏览器按后端 PID 增量重连，避免两个恢复流程同时连接不同网页目标；
- 断网状态明确显示；
- 应用退出时关闭浏览器、测试夹具和 Python；
- 强制结束 Electron 主进程后，Python 通过标准输入 EOF 退出；
- 中文、空格和长路径；
- 非法输出路径在任务创建前拒绝；
- 中文任务名和参数在 Electron/Python 管道内往返不损坏；
- 打包后端显式使用 UTF-8 标准输入/输出。

### 团队交付

- PyInstaller 自包含 Python 后端；
- Electron 官方运行时组装的 Windows x64 免安装包；
- 随机回环端口的一键启动器；
- 便携数据目录；
- 完整 Python 依赖锁；
- 版本信息；
- 安装与卸载说明；
- 使用手册；
- 依赖说明；
- 故障排查；
- 数据备份与恢复；
- 复现与构建说明；
- 便携包 ZIP 和 SHA-256；
- 源码与构建脚本 ZIP 和 SHA-256。

## 关键实现

### 诊断

`system.diagnostics` 检查：

- 数据目录写入；
- SQLite integrity 和 schema；
- DrissionPage、python-docx、openpyxl、xlrd；
- 旧版 DOC 可选转换器；
- 日志；
- 路径长度；
- 磁盘空间；
- Python 版本、可执行文件和 frozen 状态。

桌面侧补充：

- Electron、Chromium、Node、平台和架构；
- 当前显示缩放；
- 机构入口网络；
- 打包模式和内置/外部 Python 模式。

### 受控文件打开

渲染进程不能直接打开任意本地路径。打开请求先发送到 Python，由 SQLite 校验该路径是否为：

- 已登记政策 Word；
- 已登记 TXT；
- 已登记来源证据；
- 已登记 CSV、XLSX 或 HTML 报告。

校验通过后才由 Electron `shell.openPath` 打开。任务输出目录也必须属于已有任务。

### 输出路径

- 默认目录来自应用数据目录；
- 支持系统目录选择器；
- 创建任务前执行实际写文件探测；
- 目标为文件、只读或无权限时返回可读错误；
- 中文、空格和长路径通过测试。

### 发布后端

- 入口使用绝对导入，避免 PyInstaller 对 `__main__` 相对导入失败；
- 收集 DrissionPage、python-docx、openpyxl、xlrd 及数据文件；
- 捆绑 Python 3.14.7；
- 启动时显式把 stdin/stdout/stderr 配置为 UTF-8；
- Electron 以打包后端模式直接启动 `pkulaw-engine.exe`。

### 一键启动器

`启动法宝工作台.cmd` 在 Electron 启动前：

1. 通过 PowerShell 申请一个空闲回环端口；
2. 设置 `PKULAW_DEBUG_PORT`；
3. 把数据目录设置为发布目录下的 `数据`；
4. 向 Electron 传入 `--remote-debugging-port` 和
   `--remote-debugging-address=127.0.0.1`。

用户不需要安装 Python、Node.js 或外部浏览器。

## 自动化测试

### Python

```text
Ran 30 tests
OK
```

阶段 6 新增覆盖：

- 诊断报告；
- SQLite 完整性；
- 数据目录写入；
- 受控文件必须来自数据库登记产物；
- 任务输出目录校验；
- 中文、空格和长路径输出目录；
- 文件占位目标被拒绝。

### TypeScript / Electron

```text
Test Files  3 passed (3)
Tests       5 passed (5)
```

- Node/web TypeScript 类型检查通过；
- Electron 生产构建通过；
- 窗口内任务中心实际渲染；
- 数据筛选和受控按钮。

## Electron 全量烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

结果：

```text
阶段 2: passed
阶段 3: passed
阶段 4: passed
阶段 5: passed
阶段 6: passed
```

阶段 6 实测：

```text
diagnostics checks = 12
database integrity = ok
schema = 4
network = ok
scale factor = 1.25
longPathValidated = true
nonDirectoryOutputRejected = true
chineseProtocolRoundTrip = true
taskCenterRows >= 1
pythonCrashRecovered = true
browserRecoveredAfterCrash = true
offlineNetworkDetected = true
```

说明：

- 显示缩放测试为 125%；
- 诊断中唯一警告是旧版 `.doc` 可选转换器未安装，DOCX 不受影响；
- 登录、滑块、额度、来源治理和 SHA-256 行为继续由阶段 2～5 回归覆盖；
- 阶段 5 边界未弱化。

## 便携包隔离测试

执行：

```powershell
pnpm build:portable
pnpm verify:portable
```

验证目录故意包含：

- 中文；
- 空格；
- 重复长目录名；
- 首次启动数据目录。

验证时删除：

- `PKULAW_PYTHON`
- `PKULAW_ENGINE_DIR`
- `ELECTRON_RUN_AS_NODE`

结果：

```text
packaged = true
backendMode = bundled
backend protocol handshake = passed
first launch = passed
bundled dependencies = detected
Chinese protocol round trip = passed
task center rendering = passed
Python forced termination recovery = passed
browser reconnect = passed
offline network diagnosis = passed
direct EXE exit code = 0
residual processes = 0
```

## 一键启动器测试

实际执行发布包中的：

```text
启动法宝工作台.cmd
```

该启动器自行分配随机端口并启动发布版。

结果：

```text
launcher exit code = 0
packaged = true
backendMode = bundled
protocol = 1.0.0
network = ok
Chinese protocol round trip = passed
residual processes = 0
```

## 强制退出与重启恢复

测试步骤：

1. 在同一数据目录创建“强制退出恢复任务”；
2. 启动发布版，等待 Electron/Python 协议就绪；
3. 只强制终止 Electron 主进程；
4. 等待 Python 通过标准输入 EOF 退出；
5. 查询该数据目录，确认无 Electron/Python 残留；
6. 使用同一数据目录重新启动发布版并执行阶段 6 烟测；
7. 查询 SQLite。

结果：

```text
Electron force-killed = true
Python residual = 0
restart smoke = passed
task persisted = true
SQLite integrity = ok
```

## 交付制品

```text
北大法宝采集器_Windows便携版_0.1.0_20261101.zip
size = 179178430 bytes
SHA-256 = 4566bdd975466c5c265ca63f11ebd7bd167145248163b2069411c03e1664e26d

北大法宝采集器_源码与构建脚本_20261101.zip
SHA-256 见同目录 .sha256 文件
```

便携包内含：

- `法宝工作台.exe`
- `启动法宝工作台.cmd`
- `resources/app`
- `resources/engine/pkulaw-engine.exe`
- Python 运行时和完整依赖
- 版本信息
- 依赖版本与完整锁文件
- 用户、安装、复现、故障和数据恢复文档

## 安全行为

- 不保存密码；
- 不导出 Cookie；
- 不识别或外送验证码；
- 不绕过登录、滑块、验证码或额度；
- 宽泛关键词不自动回退政府官网；
- 只允许逐篇精确标题 `NO_EXACT_MATCH` 且用户显式选择后回退；
- 官方 HTML、截图和 SHA-256 校验未被弱化；
- 渲染进程不能打开任意本地文件；
- 调试端口只绑定 `127.0.0.1` 且随机分配；
- 应用退出后无项目进程残留。

## 已知限制

- 未在用户所在学校的真实校园网络完成北大法宝小规模采集；
- 未对真实学校账号、IP 登录页和 VPN 差异做完整人工验收；
- 未对真实政府官网检索结果做人工身份核验；
- 当前发布包未进行商业代码签名，Windows 可能提示“未知发布者”；
- 当前交付为免安装便携版，不包含系统级安装器和自动卸载器；
- 旧版 `.doc` 正文提取仍依赖 antiword、LibreOffice 或 Microsoft Word；
- 本地夹具测试不能替代真实站点验收结论。

## 结论

阶段 6 的界面、稳定性、自包含运行时、一键启动器、团队文档、隔离环境和全量回归均通过。

剩余工作不是阶段 6 代码开发，而是目标校园网络中的真实站点小规模验收、签名和正式发布审批。
