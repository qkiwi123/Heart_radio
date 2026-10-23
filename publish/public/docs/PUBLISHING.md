# 公开源码的范围与发布流程

公开版本包含手表及电脑源码、独立 MCP 生成工具、通用说明和合成测试数据。个人采样、设备诊断、Agent 实测报告、证书私钥和完整本地开发历史不在公开仓库中。`.gitignore` 只控制新文件，不能删除旧提交已有的数据，因此不能把含个人记录的本地主分支直接推到公开仓库。

保留完整历史的开发仓库使用 `scripts/export_public_repo.py` 创建独立的 `codex/github-public` 分支。初次发布是一个无原历史父提交的源码快照；以后只接续该公开分支。工具用独立临时索引构建提交，不切换当前分支，不改原工作文件，也不自动推送。作者必须使用公开 noreply 邮箱。

`publish/manifest.json` 是逐文件白名单；新文件不会自动加入公开版本。`publish/public/` 是通用文档及少量隐私处理后的源文件替换。需要替换的代码有源文件哈希检查，原代码发生变化后，必须先复核对应公开替换和白名单，工具才允许发布，避免沿用过时逻辑。

从完整本地仓库先生成预览，审查内容并在预览目录进行构建和测试：

```powershell
.\.venv\Scripts\python.exe scripts\export_public_repo.py
```

预览和审计只写入忽略的 `runs/`。确认公开范围、测试和可发布源文件的本地提交完成后，生成或更新公开分支。IDE、运行数据等明确非发布路径可保留未提交状态；其他源文件/模板必须先提交，未列入白名单的新源码也不能悄悄跳过：

```powershell
.\.venv\Scripts\python.exe scripts\export_public_repo.py --create-branch --author '自己的公开名称 <自己的GitHub公开noreply邮箱>'
git push origin codex/github-public:main
```

不要在保留个人记录的仓库执行 `git push --all`、`git push --mirror`，也不要把本地开发 `main` 作为源推送。这些操作会扩大公开范围。作者邮箱可在自己的 GitHub 邮箱设置中核对；不要使用私人邮箱。

原交付仓库把默认 push 目标设置为公开分支：

```powershell
git config remote.origin.push refs/heads/codex/github-public:refs/heads/main
```

这样普通 `git push origin` 只推已生成的公开分支，仍需先运行导出，才能包含最新源码。网络操作失败时先核对登录和权限，不发送密码或 token 到对话。

如果直接克隆公开仓库，它的 `main` 本身就是公开源码历史，可以按普通 Git 流程更新；仍应检查所有新增文件，确保没有个人运行记录或秘密。默认忽略规则和扫描只能防止常见误上传，不能代替发布前内容复核。
