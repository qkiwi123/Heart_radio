# 本地开发仓库的公开发布入口

本地 `main` 保留完整开发记录和个人测试资料。GitHub 的 `main` 来自独立的 `codex/github-public`，不会继承这些本地历史。请勿直接推送本地 `main`，或使用 `--all`、`--mirror`。

先在本地正常提交代码，运行 `scripts/export_public_repo.py` 生成预览，审查并测试后加 `--create-branch` 创建公开提交，再推 `codex/github-public:main`。具体命令、白名单和替换规则见 [公开发布说明](public/docs/PUBLISHING.md)。

通用 README 和使用说明位于 `public/`，导出时映射到公开仓库的对应位置。`manifest.json` 列出允许发布的文件；新增文件需要人工复核后加入。带源哈希的代码替换需要随源代码变化更新，否则发布工具会停止。

公开版本的测试心率 CSV 完全由算术生成，不读取个人采样。预览及审计写入忽略的 `runs/`。导出工具不上传、不切换分支、不修改实际索引、不修改运行服务或 Agent 配置。
