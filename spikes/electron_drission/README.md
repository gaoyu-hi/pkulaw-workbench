# Electron 内置浏览器 + DrissionPage 技术探针

本目录只用于验证 V3 的关键技术链路，不属于正式产品代码。

验证内容：

- Electron `WebContentsView` 能否显示网页；
- Electron 动态本机调试端点能否被 DrissionPage 连接；
- DrissionPage 能否找到并操作 `WebContentsView`；
- Electron `will-download` 能否接管下载；
- Python 能否通过阶段结果文件确认下载完成。

运行：

```powershell
pnpm install
pnpm start
```

运行时会打开一个 Electron 窗口，并自动启动 Python 探针。结果写入 `runtime/`。
