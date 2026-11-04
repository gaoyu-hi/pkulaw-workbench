# 阶段 1 测试记录

**日期：** 2026-10-31  
**阶段：** 正式应用骨架  
**结果：** 通过

## 交付内容

- Electron + React + TypeScript 主应用；
- React 总览、任务中心和诊断页面骨架；
- 受限 preload API；
- Electron 主进程与 Python JSON 行协议；
- 协议版本握手；
- 双向请求、事件和错误消息；
- 重复请求幂等缓存；
- SQLite 任务、任务项和任务事件初始模型；
- 桌面与 Python 统一 JSONL 日志目录；
- Python 子进程启动、状态转发和优雅退出；
- 构建、类型检查、单元测试和 Electron 烟测入口。

## 固定开发环境

- Electron：45.2.1
- Chromium：156.0.8078.75
- Electron Node：24.22.1
- Python：3.14.7
- React：19.3.1
- TypeScript：7.0.2
- Vite：8.3.5
- SQLite：3.50.4

## 自动化测试

### Python

执行：

```powershell
python -X utf8 -m unittest discover -s tests -v
```

结果：4 项通过。

覆盖：

- SQLite 初始化和 schema version；
- 任务创建、读取和列表；
- 非法任务模式与输入；
- 独立 Python 进程握手；
- 协议版本不匹配错误；
- 非法 JSON 错误；
- 请求、响应、事件顺序；
- 重复请求不重复创建任务；
- 未知方法错误；
- 优雅关闭；
- 数据库与日志文件落盘。

### Electron/Python 集成

执行：

```powershell
pnpm --dir apps/desktop test
```

结果：1 个测试文件、1 项集成测试通过。

覆盖：

- PythonService 启动；
- 协议握手；
- ping；
- 创建、查询和列出任务；
- 未知方法错误转换为客户端异常；
- 后端 stop；
- PID 清空；
- SQLite 文件生成。

### TypeScript

执行：

```powershell
pnpm --dir apps/desktop typecheck
```

结果：通过，无 TypeScript 错误。

## 构建

执行：

```powershell
pnpm --dir apps/desktop build
```

结果：通过。

生成：

- `out/main/index.js`
- `out/preload/index.js`
- `out/renderer/index.html`
- renderer CSS 和 JavaScript 资源

## Electron 烟测

执行：

```powershell
pnpm --dir apps/desktop smoke
```

结果：通过。

验证：

- Electron 窗口正常启动；
- React 页面正常渲染；
- Python PID 成功启动；
- 后端状态变为 `ready`；
- 协议握手成功；
- `system.ping` 返回正常；
- SQLite 自动创建；
- 通过界面任务模型创建一条演示记录；
- 截图成功；
- 应用退出后 Python 和 Electron 均无残留进程。

证据：

- `apps/desktop/runtime/smoke/smoke-report.json`
- `apps/desktop/runtime/smoke/smoke.png`
- `apps/desktop/runtime/smoke/logs/desktop.jsonl`
- `apps/desktop/runtime/smoke/logs/engine.jsonl`
- `apps/desktop/runtime/smoke/pkulaw.sqlite3`

烟测报告关键值：

```text
ok = true
backendState.status = ready
protocolVersion = 1.0.0
engineVersion = 0.1.0
taskCount = 1
download/network activity = none
```

## 数据库检查

```text
PRAGMA user_version = 1
tasks = 1
task_items = 0
task_events = 1
PRAGMA integrity_check = ok
```

演示任务只验证本地任务模型，没有任何北大法宝或网络请求。

## 安全边界

- `nodeIntegration=false`
- `contextIsolation=true`
- `sandbox=true`
- preload 只暴露白名单 API；
- 主进程校验任务模式、任务 ID 和列表上限；
- renderer 无法调用任意 Python 或 Node 代码；
- 默认拒绝权限请求；
- 阻止非当前应用的意外窗口导航；
- 未连接真实北大法宝；
- 未创建内置浏览器；
- 未创建网络调试端点；
- 无 Agent 或大模型调用。

## 已知限制

- Electron 目前仍是普通主窗口，`WebContentsView` 属于阶段 2；
- 尚未实现机构登录、Session、下载接管和新窗口；
- 任务只支持初始创建和查询，不执行采集；
- 团队安装包和内置 Python 打包尚未开始；
- 当前烟测使用开发机 `D:\Python\python.exe`。

## 结论

阶段 1 验收项全部通过，可以进入阶段 2“内置浏览器、登录和下载基础设施”。

