# 首次安装与配对

以下以 Windows 原生环境为例。示例项目路径为 `D:\Android\heartradio`，MCP 管理目录为 `D:\AIWorkplace\MCP\heartradio`；可以替换为自己的绝对路径。公开仓库没有运行环境、个人采样或配对信息，克隆后需要先完成本机准备。

## 电脑准备

准备 Git、Python 3、JDK 17、Android SDK 和 OpenSSL。Android SDK 需要 API 36.1、对应构建工具及 platform-tools；手表最低 API 27。Gradle 使用仓库自带 wrapper。Windows 桌面小窗使用系统 .NET Framework 编译器。首次构建及 Python 安装需要下载依赖。

在项目目录执行下面命令。请先确认 `python` 是打算使用的 Windows Python；如果不是，改为解释器的绝对路径。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r receiver\requirements.txt
```

设置 `JAVA_HOME` 为自己的 JDK 17 目录，设置 `ANDROID_HOME` 为 SDK 目录，或在不提交的 `local.properties` 中配置 `sdk.dir`。目前 ADB 帮助脚本默认使用 `D:\Android\SDK\platform-tools\adb.exe`；SDK 在其他位置时，请统一调整脚本中的路径。

```powershell
.\gradlew.bat assembleDebug testDebugUnitTest lintDebug --console=plain
.\scripts\Build-Widgets.ps1
```

## 建立本机 Wi-Fi 配对

电脑和手表连接可互通的同一 Wi-Fi。确认电脑准备使用的局域网 IPv4 与 OpenSSL 路径后，将下面两个占位内容换成实际值：

```powershell
.\.venv\Scripts\python.exe receiver\wifi_setup.py --host '<电脑局域网IPv4>' --openssl '<openssl.exe绝对路径>'
```

生成器在 `.local-wifi/` 创建本地证书、私钥、配置和 `connection-code.txt`，不会覆盖已有配对。上述文件包含连接凭据，应保留本机。系统防火墙需要允许手表访问本项目局域网接收端口8765和按需通道8767；网页8766和维护接口8768用于本机。

用 USB 连接手表并允许调试。在项目中运行：

```powershell
.\scripts\Install-Debug.ps1
```

脚本只接受已授权且身份匹配的 OW20W2；有多个候选设备时使用 `-Serial '<实际ADB设备编号>'`。覆盖安装保留应用数据，签名不符会失败，不会自动卸载。手表应用内授予身体传感器权限，输入 `.local-wifi/connection-code.txt` 中的配对码并保存。配置按需通道时仍连接 USB：

```powershell
.\scripts\Configure-CapabilityBridge.ps1
```

该步骤只将配对配置写入本应用私有目录，不会开启待命或请求传感器。日常通信使用 Wi-Fi，不依赖 USB。

## 固定桌面入口

复制 `launchers/desktop/` 内全部文件到自己的快捷入口目录，在其中 `project-path.txt` 写入实际项目绝对路径。保留这组入口及同目录的 `Launch-HeartRadio.ps1`，不要只复制单个 CMD。先运行01打开电脑接收与仪表盘，再在手表分别开启所需服务；具体规则见 [日常使用](DAILY-USE.md)。

此步骤不修改 Agent 配置。MCP 接入另见 [Agent 说明](LOCAL-AGENT.md)。
