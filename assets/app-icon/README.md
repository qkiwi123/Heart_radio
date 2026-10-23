# HeartRadio 应用图标

当前采用用户选定的 **B 版：黑白、设备轮廓加粗、两段主转折中心对称**。`heartradio.svg` 是正式矢量母版，来自已确认的 `design/app-icon/candidates/bridge-devices-bw-v2-symmetric/b-bold-devices-symmetric.svg`。两段主转折共用同一个 path，其中一段旋转 180 度；电脑和手表保留各自轮廓。

- `heartradio.png`：1024×1024 母版预览。
- `heartradio.ico`：Windows 多尺寸图标，包含 16、20、24、32、40、48、64、96、128、256 像素。
- `watch-launcher-preview.png`：圆形手表启动图标预览。
- `desktop-icon-preview.png`：电脑图标预览。
- `manifest.json`：母版哈希、导出尺寸与平台留白设置。

Android 使用白色自适应背景和带透明屏幕留白的黑色矢量前景，同时更新五档普通及圆形图标。Windows 小窗编译时将 ICO 同时嵌入可执行文件图标和 `HeartRadio.AppIcon` 资源；心率、趋势两个窗口加载同一个嵌入资源，因此不会依赖外部图标文件的运行时路径。

如需从母版重新导出，使用 `scripts/Build-AppIcons.cjs`，需要 Node.js 与 sharp；若 sharp 位于单独的运行时目录，可设置 `HEARTRADIO_NODE_MODULES` 指向其 node_modules。普通构建小窗无需重新生成图标，现有 ICO 可直接使用。历次候选和旧资源备份保留在 design 与 evidence 中。
