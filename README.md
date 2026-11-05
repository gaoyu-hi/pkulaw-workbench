# 北大法宝采集器 0.2.4

Windows 桌面采集应用，采用 Electron、React、TypeScript 与 Python 后端。

0.2.4 已接通真实 V6 页面适配、统一验证码状态、受控分页、内置浏览器
目标绑定、单篇/批量/数据处理暂停恢复停止、年份全部/单年/区间、
逐组合尾页与分页上限、批量规模确认、原生 XLS 与本地派生目录、
内置旧版 DOC 转换、正文完整性门禁、SQLite v6 来源模型、统一进度、
0 下载预览、网页缩放、任务归档/回收站、逐条 TXT、总目录 CSV/XLSX、
来源证据和 Windows 免安装发布。

0.2.2 阶段 I 已完成真实站点验收：正式碳中和扫描覆盖 19,040 个唯一结果和
111 个分区；真实下载采用固定分层样本，包括 2,784 条 I-7 Word、25 条
I-7S 跨年样本和 100 条 I-8S 地方双批次。

0.2.4 重点修复长时间启动预检造成的界面假死：任务创建后立即显示，
预检在后台逐组合更新进度，任务列表和浏览器状态保持可读，预检阶段
可以停止并释放浏览器租约。检索草稿在页面切换后保留，年份可手输
1949 至当前年份。

## 团队使用

完整的安装、登录、单篇、批量、数据处理、任务恢复、报告核验和故障处理说明见
[`docs/PKULAW_WORKBENCH_0.2.4_TEAM_MANUAL_ZH.md`](docs/PKULAW_WORKBENCH_0.2.4_TEAM_MANUAL_ZH.md)。

0.2.4 发布说明与验收记录见
[`docs/2026-11-05_PREFLIGHT_ASYNC_RELEASE.md`](docs/2026-11-05_PREFLIGHT_ASYNC_RELEASE.md)。

## 目录

```text
apps/desktop/   Electron 主进程、preload 和 React 界面
engine/         Python 后端、JSON 行协议与 SQLite 任务库
spikes/         阶段 0 技术探针
docs/           实施计划、项目状态与测试记录
scripts/        测试与开发辅助脚本
```

## 开发

```powershell
pnpm install
pnpm dev
```

`pnpm dev` 会构建并启动本地夹具模式。默认使用 `D:\Python\python.exe`，可通过 `PKULAW_PYTHON` 指定开发环境 Python 路径。发布版不要求目标电脑安装 Python 或 Node.js。

## 测试

```powershell
pnpm test
pnpm build
pnpm smoke
pnpm build:portable
pnpm verify:portable
```

发布便携包会生成：

- `outputs/北大法宝采集器_Windows便携版_0.2.4_<日期>.zip`
- 同名 `.sha256`；
- `outputs/北大法宝采集器_源码与构建脚本_<日期>.zip` 及 SHA-256。

## 边界

- 正常运行不调用 Agent、Codex 或大模型；
- 不保存账号密码；
- 不导出 Cookie，不绕过登录、滑块、验证码或机构额度；
- 可使用本地测试机构验证浏览器绑定、逐篇和批量检索、目录/Word ZIP、同名消歧、暂停恢复、分页和失败隔离；
- 逐篇精确标题缺失时可显式开启政府官网补全，宽泛关键词不会自动回退；
- 政府官网正文整理件与原生附件分开标注；
- 发布包内含 Python 3.14.7、DrissionPage、Electron 和文档，不要求目标电脑安装 Python；
- 真实北大法宝尾页、固定 Word 样本、地方双批次、数据处理、分页上限和规模确认已通过阶段 I 验收；
- 当前版本面向 Windows x64 本地单机使用，不含多用户共享数据库、自动更新或代码签名；
- 当前下载范围以 Word、原生 XLS 和 Word ZIP 为主，不含 WPS、PDF、纯文本、超文本、纯净版以及三个法宝联想选项。
