# 法宝工作台 Apple 芯片 macOS 移植设计

**版本：** 2026-11-05

**目标平台：** macOS arm64，Apple M1/M2/M3/M4

**基础版本：** Windows 0.2.4

**实施边界：** Windows 上完成可验证的跨平台基础；macOS 实机编译、运行和签名由 Mac Agent 完成

## 背景

当前程序由 Electron/React 前端、TypeScript 主进程和 Python 业务后端组成。
业务逻辑和数据模型大部分跨平台，但启动器、后端可执行文件、进程结束、测试
解释器、旧版 DOC 转换和便携包构建脚本存在 Windows 专用假设。

目标不是复制一份长期分叉的 Mac 代码，而是在同一仓库中保留一套公共业务代码，
通过窄平台适配层承载操作系统差异。

## 已确认约束

- 首批只支持 Apple 芯片 Mac，目标架构为 `arm64`；
- 不承诺 Intel Mac 或 Universal App；
- Windows 版本必须继续可测试、可构建，不改变现有 0.2.4 行为；
- 不在 Windows 上声称生成或验证了 macOS 成品；
- macOS 开发测试包可以先不签名；
- 正式分发给团队前必须完成 Developer ID 签名和 Apple 公证；
- 不迁移 Windows Electron Cookie、Session、浏览器 Profile 或含 Windows
  绝对路径的现有 `数据` 目录；
- 账号密码、Cookie 和真实下载数据不得进入 Git 仓库；
- 所有平台差异必须集中在可测试的适配模块，不散落为大量
  `process.platform === ...` 条件。

## 方案选择

### 方案一：同一仓库、公共核心、平台适配层

Electron/React、Python 业务模块、SQLite schema、JSON 协议和任务模型共用；
Windows 与 macOS 各自实现启动、路径、后端文件名、回收站、旧版 DOC 转换和
打包适配。

优点是长期维护成本最低，Windows 修复可以继续被 Mac 复用。缺点是本次需要
先做一次平台边界整理。

### 方案二：独立 Mac 分支复制代码

短期出包快，但后续每个 Windows 功能都要移植两遍，容易再次产生两个不可合并
的版本，不采用。

### 方案三：重写 Mac 客户端

界面、任务协议、SQLite 和大量业务规则都已存在，重写没有收益，不采用。

**决定：采用方案一。**

## 架构

### 公共层

以下模块继续跨平台共用：

- `apps/desktop/src/renderer` 下的 React 界面；
- `apps/desktop/src/preload` 下的受限 IPC 桥；
- `apps/desktop/src/shared` 下的协议、布局和任务展示逻辑；
- `engine/pkulaw_engine` 下的任务、检索、下载意图、SQLite、数据处理和报告；
- stdin/stdout JSON 行协议；
- `数据` 目录内的 schema v6 数据库和产物目录结构。

### Electron 平台适配层

新增一个独立平台模块，负责：

- 打包后 Python 后端可执行文件名；
- 开发环境 Python 解释器选择；
- 后端目录和临时 staging 目录；
- 进程终止方式；
- 系统回收站显示名称；
- 平台能力诊断。

公共主进程只调用平台接口，不再直接拼接 `pkulaw-engine.exe`、
`D:\Python\python.exe` 或 Windows 专用提示。

Windows 适配必须保持：

- `pkulaw-engine.exe`；
- 开发环境默认 Python 路径；
- 现有 staging、进程终止和回收站行为；
- 现有 `.cmd` 启动器。

macOS 适配提供：

- 无扩展名 `pkulaw-engine`；
- 开发环境 `python3`；
- `SIGTERM` 优先、超时后 `SIGKILL`；
- macOS 用户数据目录；
- `.app` 内部只读资源与用户数据目录分离。

### Python 后端打包

保留同一份 PyInstaller spec，通过平台条件选择数据文件：

- Windows：继续包含 `vendor/antiword/win-x64`；
- macOS：不包含 Windows antiword 二进制，使用 macOS 系统 `textutil`
  作为旧版 `.doc` 的离线后备转换器；
- 文档正文质量、来源证据、转换器名称和版本仍写入 SQLite；
- macOS 转换器不可用时继续失败关闭，不得静默发布空文本。

PyInstaller 输出目录名称保持 `pkulaw-engine`，Windows 输出根文件为
`pkulaw-engine.exe`，macOS 输出根文件为 `pkulaw-engine`。

### macOS 应用打包

macOS 构建单独使用 Apple 芯片目标，并生成：

- `法宝工作台.app`；
- `PkulawWorkbench-<version>-macOS-arm64.dmg`；
- `.dmg` SHA-256；
- 版本信息和依赖锁定文档。

Electron Builder 只用于 macOS 的 `.app/.dmg` 包装。Windows 继续使用现有
便携 ZIP builder，不强行统一成 Electron Builder。

macOS 用户数据不得写入 `.app` 内部。默认数据目录由 Electron
`app.getPath("userData")` 派生，测试和便携运行可通过 `PKULAW_DATA_DIR`
覆盖。

### 测试与构建脚本

公共 Node 脚本根据平台选择：

- Python 命令：Windows 使用 `PKULAW_PYTHON` 或现有默认值；macOS 使用
  `PKULAW_PYTHON` 或 `python3`；
- pnpm 可执行名：Windows 使用 `pnpm.cmd`，macOS 使用 `pnpm`；
- Electron dist 文件名：Windows 为 `electron.exe`，macOS 由 Builder 管理；
- 测试临时目录和进程探测使用平台接口。

新增 macOS 构建脚本不得在非 macOS 系统上伪造成功。Windows 执行
`pnpm build:mac` 时必须明确失败并提示“必须在 macOS arm64 构建机执行”。

### 行尾规则

新增 `.gitattributes`，文本文件在仓库中使用 LF，Windows 检出保持可用；
`.cmd`、`.bat`、`.ps1` 保持 CRLF。这样可避免 Windows 和 Mac 来回提交时
整份文件被误判为修改。

## Windows 阶段交付

Windows 阶段允许且必须完成：

- 平台适配模块及其单元测试；
- Electron 主进程平台边界整理；
- Python `textutil` 转换分支及单元测试；
- 跨平台测试命令解析；
- PyInstaller spec 平台条件；
- macOS Electron Builder 配置和构建脚本骨架；
- `.gitattributes`；
- macOS 交接文档和 Mac Agent 提示词；
- 完整 Windows 自动化、生产构建和 Stage 6 烟测。

Windows 阶段不得声称完成：

- macOS `.app/.dmg` 实机启动；
- Apple 芯片 Python 后端运行；
- 内置浏览器和北大法宝真实页面验收；
- macOS `textutil` 真实文档转换；
- Developer ID 签名、公证或 Gatekeeper 验证。

## Mac 阶段验收

Mac Agent 必须完成：

- 在 `uname -m` 返回 `arm64` 的机器上构建；
- 验证 `.app` 包内后端和业务文档；
- 首次启动、诊断、SQLite schema v6 和 integrity check；
- 内置浏览器、网页缩放、登录、验证码人工完成和自动恢复；
- 单篇与批量夹具任务；
- 至少一份真实 `.doc` 的 `textutil` 转换验证；
- npm/Electron/Python 开发依赖不泄漏到最终 `.app`；
- 强制退出和重启恢复；
- SHA-256；
- 有证书时执行签名、公证和 `spctl` 验证；
- 无证书时明确标记为测试包，不得作为正式团队发布。

## 错误处理

- 找不到 arm64 Python 后端时，应用显示平台明确错误，不退化到 Windows
  `.exe`；
- `textutil` 超时、非零退出或正文不足时，文档进入待核验或失败状态；
- 在非 macOS 系统执行 Mac 构建脚本立即失败；
- 数据目录不可写时在任务创建前失败；
- 签名或公证失败时停止发布，不生成“正式版”结论。

## 非目标

- Intel Mac；
- Universal2 应用；
- App Store 分发；
- 自动更新；
- 从 Windows 直接生成 macOS 成品；
- 迁移 Windows Cookie、Session 或含绝对路径的任务数据库；
- 首个 Mac 版本增加新的下载格式或法宝联想选项。

## 验收重点

- Windows 现有 0.2.4 回归不能因平台抽象而退化；
- 任何平台差异都不能继续散落在业务模块；
- 非 macOS 构建必须失败关闭；
- Mac 数据目录必须在 `.app` 外部且可恢复；
- 旧版 DOC 转换必须保留可审计来源，不得静默丢正文。
