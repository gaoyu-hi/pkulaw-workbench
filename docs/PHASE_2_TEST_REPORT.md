# 阶段 2 测试记录

**日期：** 2026-10-31  
**阶段：** 内置浏览器、登录和下载基础设施  
**结果：** 通过

## 交付内容

- 主窗口内可见、可交互的 `WebContentsView`；
- Electron 启动器在进程启动前注入随机回环调试端口；
- Python/DrissionPage 自动连接指定内置网页；
- 机构配置本地持久化；
- 每个机构独立且持久的 Electron Session；
- 登录状态、滑块状态和人工继续基础；
- Electron `will-download` 下载接管；
- 任务专属下载临时目录、进度、完成和失败事件；
- 下载事件通过 JSON 行通知回传 Python；
- 新窗口拦截和最多三个受控子视图；
- 内置网页渲染进程崩溃检测和自动重建；
- Python 停止、重启和网页重新连接；
- 本地测试机构 HTTP 服务；
- 浏览器测试界面和诊断信息。

## 自动化测试

### Python

```text
Ran 4 tests in 0.709s
OK
```

覆盖原有协议、SQLite 和错误路径，继续通过。

### TypeScript / Electron 基础设施

```text
Test Files  2 passed (2)
Tests       3 passed (3)
```

新增覆盖：

- 机构配置保存和重新加载；
- 每个机构生成独立 Session key；
- 拒绝非 HTTP/HTTPS 入口；
- Python 服务握手、任务请求和优雅退出继续通过。

### 类型检查和构建

- TypeScript 主进程与 preload 类型检查：通过；
- TypeScript renderer 类型检查：通过；
- Electron main/preload/renderer 生产构建：通过。

## Electron 端到端烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

结果：通过。

验证结果：

| 检查项 | 结果 |
| --- | --- |
| 主窗口内显示 `WebContentsView` | 通过 |
| 随机回环调试端点 | 通过 |
| Python/DrissionPage 连接内置网页 | 通过 |
| 本地机构登录 | 通过 |
| 滑块进入等待人工状态 | 通过 |
| 用户明确继续后重新核验 | 通过 |
| Electron 接管下载 | 通过 |
| 下载进度与完成事件回传 Python | 通过 |
| 下载文件内容校验 | 通过 |
| Session 重载后保持认证 | 通过 |
| `WebContentsView` 重建后保持认证 | 通过 |
| Python 停止并重启后重新连接 | 通过 |
| 内置网页进程强制崩溃后自动恢复 | 通过 |
| 新窗口转为受控子视图 | 通过 |
| DrissionPage 发现受控子视图 | 通过 |
| 应用退出后无 Electron/Python 残留 | 通过 |

烟测报告关键值：

```text
ok = true
browser.authenticated = true
download.state = completed
download.contentVerified = true
sessionPersistedAfterReload = true
sessionPersistedAfterViewRecreation = true
browserReconnectedAfterPythonRestart = true
rendererCrashRecovered = true
childViewDetected = true
```

## 证据文件

- `apps/desktop/runtime/smoke/smoke-report.json`
- `apps/desktop/runtime/smoke/smoke.png`
- `apps/desktop/runtime/smoke/logs/desktop.jsonl`
- `apps/desktop/runtime/smoke/logs/engine.jsonl`
- `apps/desktop/runtime/smoke/downloads/staging/`

## 安全边界

- 外部网页不启用 Node；
- 内置网页启用 sandbox、contextIsolation 和 webSecurity；
- 远程网页没有 preload；
- 所有权限请求默认拒绝；
- 下载路径由主进程生成，文件名经过清理；
- 新窗口只能在应用内成为受控 `WebContentsView`；
- 子视图最多三个；
- 页面目标过滤为当前机构 HTTP/HTTPS Origin；
- 调试端口只绑定 `127.0.0.1`，由启动器动态分配；
- 不包含真实账号密码；
- 不导出 Cookie；
- 测试全程不访问真实北大法宝。

## 已知限制

- 本地夹具只模拟登录和滑块，真实机构页面选择器属于阶段 3；
- 当前人工继续由明确按钮触发，真实滑块仍要求用户在原网页完成；
- Python 重启目前由界面操作触发，不是无人值守自动重启；
- 下载只完成基础路径、进度和内容验证，政策文件类型校验属于后续阶段；
- 尚未迁移任何逐篇或批量采集业务；
- 尚未制作安装包和内置 Python 发布包。

## 结论

阶段 2 验收项全部通过，可以进入阶段 3“逐篇采集迁移”。

