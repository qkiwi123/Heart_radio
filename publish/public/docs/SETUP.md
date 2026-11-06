# 首次构建、安装与配对

当前源码针对Windows原生Python、OPPO Watch 2 OW20W2/API27。没有捆绑运行环境、系统驱动、私钥或配对；不要把示例路径、编号或地址直接当成你的设备信息。

## 电脑和构建环境

准备Git、Python 3.11或更高版本、Android SDK及匹配的JDK。推荐使用安装的Android Studio自带JBR；将JAVA_HOME指向自己的JDK目录，ANDROID_HOME指向SDK，SDK platform-tools加入PATH，确认`adb devices -l`可用。在本机local.properties设置sdk.dir，该文件不会提交。Android compileSdk由app/build.gradle.kts指定（36.1）。

在项目根目录运行：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r receiver\requirements.txt
.\gradlew.bat :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
```

本地依赖已经缓存时可给Gradle添加--offline。桌面小窗是可选功能，需要时再运行 `.\scripts\Build-Widgets.ps1`，使用 Windows 的 .NET Framework 编译器构建；网页仪表盘无需先构建小窗。图标已随源码提供，不需要重新生成。

## 手表安装

通过USB连接手表，开启USB调试并允许此电脑。运行`.\scripts\Install-Debug.ps1`；脚本只接受身份匹配且已授权的OW20W2。有多个设备时传`-Serial '<实际ADB编号>'`。覆盖安装保留数据，签名不符会失败，不自动卸载或清数据。首次使用在手表授予身体传感器权限。

## 蓝牙配对路径

先通过Windows/手表系统完成蓝牙配对，再填写实际的手表蓝牙MAC、电脑蓝牙MAC及ADB编号：

```powershell
.\.venv\Scripts\python.exe receiver\rfcomm_probe.py prepare --watch-address '<手表蓝牙MAC>' --pc-address '<电脑蓝牙MAC>' --serial '<实际ADB编号>'
```

该命令仅向本机.local-wifi/rfcomm-probe.json及本应用私有文件写入相同的对等设备/随机令牌，不开始采样。保护生成文件，不上传内容。日常网页选择“仅蓝牙”或“自动·蓝牙优先”，不要求同一Wi-Fi。没有蓝牙配置时不能把自动策略显示等待视为已配对。

## 可选Wi-Fi路径

准备OpenSSL与可互通的局域网，填写电脑实际局域网IPv4及OpenSSL绝对路径：

```powershell
.\.venv\Scripts\python.exe receiver\wifi_setup.py --host '<电脑局域网IPv4>' --openssl '<openssl.exe绝对路径>'
.\scripts\Configure-CapabilityBridge.ps1 -Serial '<实际ADB编号>'
```

生成器仅首次在.local-wifi/创建证书、私钥、配置及connection-code.txt，不覆盖旧配对。按应用界面将自己的连接码保存到手表；Configure-CapabilityBridge只配置本应用的按需Wi-Fi通道，不开启交互。防火墙按需要允许手表访问项目局域网端口8765和8767；网页8766、维护8768及8769仅用于本机。日常Wi-Fi不依赖USB，但不是互联网远程通道。

## 启动入口

在 Windows 完成上述电脑环境准备后，可以运行项目根目录的 `Start-HeartRadio.cmd` 和 `Stop-HeartRadio.cmd`；入口按文件位置找到项目。换电脑或移动项目后，在新位置重新创建 `.venv` 并安装依赖，避免沿用指向旧 Python 路径的环境。

桌面 01～08 也可以在项目的 `launchers/desktop` 目录里直接运行，`project-path.txt` 保留占位值或留空即可。若要把入口放到项目目录外，请整套复制，保留 `Launch-HeartRadio.ps1`，并在同目录的 `project-path.txt` 填写自己的项目绝对路径。使用 01 开启电脑管理后，在手表按需要开启连接、连续心率或 Agent 交互。详细开关与期限见[日常使用](DAILY-USE.md)。MCP 需另行[导出并接入](LOCAL-AGENT.md)。
