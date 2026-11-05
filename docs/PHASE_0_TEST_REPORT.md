# 阶段 0 技术探针测试记录

**日期：** 2026-10-31  
**结果：** 通过

## 环境

- Electron：45.2.1
- Chromium：156.0.8078.75
- Electron Node：24.22.1
- Python：3.14.7
- DrissionPage：4.1.1.4

## 关键发现

Electron 的随机调试端口必须在 Electron 主进程启动前，通过启动参数注入：

```text
--remote-debugging-port=<random-free-port>
--remote-debugging-address=127.0.0.1
```

在应用已经初始化后再调用 `app.commandLine.appendSwitch` 的探针版本虽然打开了端口，但只暴露 Chromium 默认新标签页，无法发现应用页面。改为外置启动器预先分配端口后，应用页面完整进入 DevTools 目标列表。

## 验证结果

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| DrissionPage 连接 Electron Chromium | 通过 | `runtime/python_result.json` |
| 精确发现 `WebContentsView` | 通过 | `tabUrl` 与 fixture URL 完全一致 |
| 读取标题和正文 | 通过 | heading/status 字段 |
| JavaScript 修改 DOM | 通过 | `changed-by-drissionpage` |
| Electron 接管普通下载 | 通过 | `DOWNLOAD_STARTED`、`DOWNLOAD_DONE` |
| Python 读取下载路径和内容 | 通过 | `ELECTRON_DRISSION_SPIKE_OK` |
| 应用内新窗口转为 `WebContentsView` | 通过 | `/child` 目标被发现 |
| 持久 session Cookie | 通过 | `sessionCookie=persisted` |
| 探针自动退出 | 通过 | 启动器正常结束 |

最终成功记录：

```text
steps = attached, target_found, dom_read, dom_changed,
        download_clicked, child_window_checked
downloadState = completed
childWindowDetected = true
sessionCookie = persisted
SPIKE_PYTHON_PASSED = true
```

## 安全边界

- 调试端口只绑定 `127.0.0.1`；
- 端口为随机空闲端口；
- 应用退出时终止 Python 子进程；
- 远程网页不启用 Node；
- 应用使用 sandbox、contextIsolation 和 nodeIntegration=false；
- 探针只是技术验证，不作为正式应用安全实现；
- 正式应用仍需加入目标白名单、下载路径控制和调试端点生命周期管理。

## 结论

V3 的关键技术路线可行，可以进入阶段 1，建立正式应用骨架。

