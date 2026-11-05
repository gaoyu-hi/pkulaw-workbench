# 法宝工作台 Apple 芯片 macOS 交接说明

## 当前状态

- Windows 0.2.4 已完成跨平台基础改造。
- 远端分支：`feature/macos-arm64-port`
- 目标机器：Apple 芯片 Mac，`arm64`
- 目标产物：`法宝工作台.app` 和
  `PkulawWorkbench-<version>-macOS-arm64.dmg`
- 当前分支没有生成或验证过 macOS 成品。

## 绝对不要迁移的内容

以下内容来自 Windows，不能复制到 Mac：

- `法宝工作台.exe`；
- `pkulaw-engine.exe`；
- `启动法宝工作台.cmd`；
- `node_modules`；
- `.packaging-venv`；
- `build`；
- `release`；
- Windows Electron Profile、Cookie、Session；
- 含 Windows `D:\...` 绝对路径的 `数据` 目录。

只通过 Git 迁移源码。Mac 上重新安装依赖、编译后端和生成应用。

## 准备 Mac

先确认是 Apple 芯片：

```bash
uname -m
```

必须输出：

```text
arm64
```

安装 Xcode Command Line Tools：

```bash
xcode-select --install
```

安装 Node.js 24，然后启用项目 lockfile 使用的 pnpm：

```bash
corepack enable
corepack prepare pnpm@11.25.0 --activate
```

确认 Python 为 3.14 arm64：

```bash
python3 --version
python3 -c "import platform; print(platform.machine())"
```

第二条命令应输出 `arm64`。如果不是 Python 3.14，安装官方 python.org
Python 3.14 或 `python@3.14`，再通过 `PKULAW_PYTHON` 指定其绝对路径。

## 获取源码

```bash
git clone --branch feature/macos-arm64-port https://github.com/gaoyu-hi/pkulaw-workbench.git
cd pkulaw-workbench
git status --short --branch
```

工作树必须干净，分支必须是：

```text
feature/macos-arm64-port
```

安装依赖：

```bash
pnpm install --frozen-lockfile
python3 -m venv .packaging-venv
source .packaging-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r engine/requirements-build.txt
```

## 首次验证

```bash
node --test scripts/tests/*.test.mjs
pnpm --dir apps/desktop typecheck
pnpm test
pnpm --dir apps/desktop smoke:stage6
```

如果 Python 解释器不是 `python3`：

```bash
export PKULAW_PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
```

然后重新运行测试。

## 构建 Apple 芯片版本

没有 Apple Developer 证书、只做开发测试时：

```bash
CSC_IDENTITY_AUTO_DISCOVERY=false pnpm build:mac
```

已有 Developer ID 证书时：

```bash
pnpm build:mac
```

预期产物：

```text
release/macos/PkulawWorkbench-0.2.4-macOS-arm64.dmg
release/macos/PkulawWorkbench-0.2.4-macOS-arm64.dmg.sha256
release/macos/version.json
```

同时在 `release/macos` 中会生成 Electron Builder 的未打包 `.app`。

## 必须完成的实机验收

1. 打开 `.app`，确认后端状态为“应用内置”；
2. 确认 `版本信息.json`、应用版本和后端版本都是同一版本；
3. 运行 SQLite 诊断，确认 schema v6 和 `integrity_check=ok`；
4. 打开内置浏览器，验证网页缩放和左右/上下滚动；
5. 验证机构登录、滑块人工处理、三次稳定检测和自动恢复；
6. 运行 Stage 6 夹具的逐篇和批量任务；
7. 用至少一份真实旧版 `.doc` 验证 `textutil` 转换；
8. 强制退出后重启，确认 SQLite 任务和断点仍在；
9. 退出应用后确认没有 Electron 或 `pkulaw-engine` 残留；
10. 复算 `.dmg` 的 SHA-256。

## 签名与公证

未签名的测试 DMG 可以用于开发验证，但不能直接作为正式团队版本。

有 Apple Developer Program 权限时：

- 在 Mac Keychain 安装 Developer ID Application 证书；
- 设置 `CSC_LINK`、`CSC_KEY_PASSWORD`；
- 设置 Apple notarization 所需的 `APPLE_ID`、`APPLE_APP_SPECIFIC_PASSWORD`
  和 `APPLE_TEAM_ID`，或使用 App Store Connect API Key；
- 重新构建并公证；
- 运行 `spctl --assess --type execute --verbose` 验证 Gatekeeper；
- 运行 `codesign --verify --deep --strict --verbose=2` 验证签名。

没有证书时，在报告中明确写“未签名测试包”，不得描述为正式发布。

## 数据和回滚

- 首次 Mac 运行应创建新的 macOS 用户数据目录；
- 不从 Windows 复制 Electron Profile、Cookie 或 Session；
- 如仅需保留任务记录，应单独评估 SQLite 中的 Windows 输出路径，
  不要直接整目录复制；
- 构建失败时保留 `release/macos`、Python 构建日志和 Electron Builder 日志；
- 不删除或覆盖 Windows 交付目录。

## 完成标准

只有以下证据全部存在，才能报告“Mac 版本完成”：

- `uname -m` 为 `arm64`；
- 全量测试通过；
- DMG 和 SHA-256；
- 应用、后端和锁文件版本一致；
- 内置浏览器与 Stage 6 任务通过；
- 真实 `.doc` 的 `textutil` 结果；
- 强制退出恢复；
- SQLite v6 完整性；
- 签名/公证结果，或明确的“未签名测试包”；
- 未提交 `数据`、Cookie、Session 或构建缓存。

## 给另一个 Mac Agent 的完整提示词

将下面整段发给 Mac 上的 Agent：

```text
你正在 Apple 芯片 Mac 上继续“法宝工作台”的 macOS arm64 移植。仓库是
https://github.com/gaoyu-hi/pkulaw-workbench.git，工作分支是
feature/macos-arm64-port。请先读取：
- docs/superpowers/specs/2026-11-05-macos-arm64-port-design.md
- docs/superpowers/plans/2026-11-05-macos-arm64-port-implementation.md
- docs/MACOS_ARM64_AGENT_HANDOFF.md

目标：在当前分支完成 macOS arm64 编译、实机验收和 DMG；不要创建 Intel 或
Universal 版本，不要合并或推送 main。使用测试驱动开发，发现问题先写失败
测试再修复。

第一步确认 `uname -m` 必须是 arm64，并记录 macOS 版本、Xcode CLT、Node、
pnpm、Python、Electron 和 PyInstaller 版本。只在 Mac 上重新安装依赖和编译，
绝不复制 Windows 的 node_modules、.packaging-venv、release、.exe、.cmd、
Electron Profile、Cookie、Session 或含 Windows 绝对路径的数据目录。

按交接文档安装依赖，运行：
node --test scripts/tests/*.test.mjs
pnpm --dir apps/desktop typecheck
pnpm test
pnpm --dir apps/desktop smoke:stage6
pnpm build:mac

然后完成交接文档“必须完成的实机验收”的全部项目，包括内置浏览器、人工验证码
恢复、单篇和批量夹具、真实旧版 DOC 的 textutil 转换、强制退出恢复和 SQLite v6
完整性。不得把夹具通过冒充真实北大法宝站点通过，也不得自动破解验证码。

如果没有 Apple Developer 证书，生成并明确标记“未签名测试包”；如果有证书，
完成 Developer ID 签名、公证、codesign 和 spctl 验证。不要上传任何 Cookie、
Session、真实任务数据库或下载成果。

完成后提交到 feature/macos-arm64-port 分支并推送该分支，不要推送 main。最终报告
必须包含：commit SHA、DMG 本地路径、DMG SHA-256、版本号、全部测试结果、真实
DOC 转换证据、强制退出恢复证据、签名/公证状态、未解决问题。任何未通过的验收
项都必须明确列出，不能只写“应该可用”。
```
