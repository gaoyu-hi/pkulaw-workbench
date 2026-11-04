# 统一任务与浏览器工作区验收记录

**日期：** 2026-11-03  
**版本：** 0.2.2  
**范围：** 总览机构配置、单按钮启动、同屏任务工作区、验证码自动恢复、
运行中任务设置、一屏布局、便携包回归。

## 结论

本地 Electron 夹具端到端烟测通过。真实北大法宝登录、验证码和小范围恢复
仍由使用者在目标校园网络中人工监督完成；自动测试不保存账号、密码或 Cookie，
不尝试识别或绕过验证码。

## 基准一屏布局

使用内容区 1536×864 逻辑像素验收：

| 指标 | 结果 |
|---|---|
| 应用文档 `scrollHeight` | 864 |
| 应用文档 `scrollWidth` | 1536 |
| 任务区顶部 / 底部 | 181 / 844 |
| 浏览器顶部 / 底部 | 181 / 844 |
| 浏览器可见 | `true` |
| 侧边栏独立浏览器入口 | 不存在 |
| 总览 Hero | 不存在 |

任务区和浏览器顶部、底部完全对齐。打开“任务设置”后浏览器
`data-browser-visible=false`；关闭后恢复为 `true`。

## 单按钮与零下载检前核验

通过渲染层 `startCollectorTask` 连续调用同一输入和同一 operationId：

- 任务总数只增加 1；
- 第二次调用返回同一任务 ID，并标记 `idempotent=true`；
- 检前核验的 `downloadsStarted`、`downloadIntentsCreated`、
  `tasksCreated` 均为 0；
- 检前核验通过后进入同一任务工作线程。

## 验证码自动恢复

预检登录失败时，任务进入 `waiting_manual`，临时浏览器租约释放。任务
checkpoint 保存原任务 ID、等待代码和恢复上下文，不保存账号或 Cookie。

人工模拟登录后：

- 第一次稳定清除样本仍保持 `waiting_manual`；
- 第二次稳定清除后只产生 1 个 `task.manual-recovered` 事件；
- 事件中的任务 ID 与最初等待任务一致；
- `action=restart`，使用同一任务从断点恢复；
- 没有恢复其他任务。

自动恢复失败显示“重新检测并继续”；同名版本、页数上限、批量规模、
机构额度和内容完整性不会进入自动恢复。

## 运行中任务设置

配置发生实际变化后：

- 任务 ID 保持不变；
- `configGeneration` 增加 1；
- 旧扫描日志、旧页数和旧检前快照不再作为新配置起点；
- 旧配置下未完成条目/批次从新版本派生中隔离，并记录审计事件；
- 已完成 Word 路径在设置切换前后完全相同：

```text
phase11-single/01_来源Word/北大法宝/
中央政策_2024_分页测试政策05_pagination-5.docx
```

- 同一 operationId 只停止/重启一次；
- 停止超时返回 `TASK_PAUSE_TIMEOUT`，不写入新设置；
- “任务设置”打开时浏览器收起，关闭或应用后恢复；
- 验证码打断设置时，任务级本地草稿保留。

## 自动回归

已执行：

```powershell
pnpm test
pnpm --dir apps/desktop smoke:stage6
```

最终通过结果以提交前最后一次输出为准；完整烟测报告保存在：

```text
apps/desktop/runtime/smoke-stage6/smoke-report.json
```

烟测截图：

```text
apps/desktop/runtime/smoke-stage6/phase11-unified-workspace.png
```

## 便携包

构建和验证命令：

```powershell
pnpm build:portable
pnpm verify:portable
```

最终发布包：

```text
北大法宝采集器_Windows便携版_0.2.2_20261103.zip
```

SHA-256：

```text
10bcf4289517b2f8a8c52305686bd8d89bbca47dabfb582dde436682a5ea669a
```

验证结果：

- 隔离目录首次启动通过；
- 超长中文、空格路径通过；
- 打包 Python 后端从短路径运行时镜像启动通过；
- 一键启动器通过；
- 后端缺失保护通过；
- 强制退出后 SQLite 完整性为 `ok`；
- 强制退出后重启恢复通过；
- 残留进程为 0；
- 便携包 smoke 中基准布局仍为 1536×864，任务和浏览器同屏。

最终验证报告：

```text
0.2.2_便携包首次启动报告_20261103.json
```

## 未纳入本阶段

- WPS、PDF、纯文本、超文本、纯净版下载格式；
- “保留字段信息”“保留本篇右侧的法宝联想”“保留正文中的法宝联想”；
- macOS 或 Linux 发布包；
- 自动识别、外送或绕过验证码；
- 自动输入账号密码、导出或共享 Cookie。
