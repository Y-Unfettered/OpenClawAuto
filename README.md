# OpenClawAuto

`OpenClawAuto` 是一个面向普通 Windows 用户的 `OpenClaw` 中文一键安装工具。

它的目标是把原本偏开发者的环境检查、依赖安装、模型预配置、网关启动和聊天接入流程，封装成一个完全不懂代码的朋友也能直接双击使用的中文 GUI 工具。

项目免费开源，采用 `MIT License`。

## 项目定位

- 给不熟悉终端和英文提示的朋友提供一个可直接使用的安装器
- 提供一个可交付、可演示、可复用的 AI 工具安装器项目模板
- 作为后续开发 Windows 中文一键安装工具的参考样板

## 当前功能

- 检测 `Node`、`Python`、`Git`、`OpenClaw` 是否已安装
- 从同目录 `payload` 文件夹自动识别安装包并静默安装
- 自动安装 `OpenClaw`
- 通过非交互方式完成 `OpenClaw` 初始配置
- 自动写入模型相关配置
- 支持 `OpenAI 兼容接口` 和 `Anthropic 兼容接口`
- 自动生成内部 `Provider ID`，不要求用户理解或填写
- 安装完成后尝试启动 `Gateway`
- 自动获取带 token 的控制台地址
- 提供 `启动网关 / 重启网关 / 打开控制台 / 复制控制台地址`
- 提供 `飞书 / 微信` 接入入口
- 提供常用斜杠命令复制按钮
- 提供浅色 / 深色模式和中文化界面

## 技术路线

这个工具走的是 Windows 原生安装路线，不依赖用户提前配置开发环境。

整体流程大致是：

1. 检测本机是否已经安装 `Node / Python / Git`
2. 如果未安装，则优先使用本地安装包静默安装
3. 安装 `OpenClaw`
4. 用非交互方式写入模型配置
5. 启动 `Gateway`
6. 提供日常控制入口和消息接入入口

这意味着：

- 目标机器不需要预装 Python 才能启动这个 GUI 工具
- 最终用户不需要自己理解 `Provider ID`、网关 token 等细节
- 但如果第三方模型平台、OpenClaw CLI 或消息渠道流程后续更新，安装器逻辑也要跟着调整

## 离线资源

当前工具优先读取 `payload` 目录中的 Windows 安装包。

推荐准备：

- Node：官方 Windows x64 `.msi`
- Python：官方 Windows x64 `.exe`
- Git：Git for Windows `.exe`

这些安装包用于本地交付和打包测试，但默认**不会提交到 Git 仓库**，避免仓库体积过大，也避免把第三方安装程序直接塞进源码仓库。

## 使用方式

1. 把需要的安装包放到 `payload` 目录
2. 运行 `OpenClawSetupTool.exe`
3. 填写或预设好 `Base URL`、`模型 ID`、`API Key`
4. 根据接口协议选择：
   - `/v1/chat/completions` 或 `/v1/responses` 选 `OpenAI 兼容接口`
   - `/v1/messages` 选 `Anthropic 兼容接口`
5. 点击“开始一键安装”

如果你想让朋友双击后几乎不需要输入，可以把 `installer-config.template.json` 复制成 `installer-config.json`，提前填好模型参数。  
注意：这会让 `API Key` 以明文形式保存在本地 JSON 文件中。

## 本地开发

环境要求：

- Windows
- Python 3.13+
- PyInstaller

常用命令：

```powershell
python -m py_compile .\main.py
.\build.ps1
```

## 打包输出

```text
release/OpenClawSetupTool/
  OpenClawSetupTool.exe
  installer-config.template.json
  payload/
    README.txt
```

如果项目根目录的 `payload` 下存在安装包，构建或交付时可以一并带走。

## 仓库结构

```text
openclawAuto/
  main.py
  build.ps1
  OpenClawSetupTool.spec
  installer-config.template.json
  PROJECT_RETROSPECTIVE.md
  payload/
    README.txt
  README.md
  LICENSE
```

## 适用场景

- 想把 OpenClaw 做成“发给朋友直接用”的中文安装器
- 想研究 Windows GUI 工具如何封装 Node / Python / CLI 安装流程
- 想沉淀一套适合 AI 工具分发的中文安装器模板

## 不放进仓库的内容

默认不提交以下内容：

- PyInstaller 构建缓存
- `release` 打包产物
- 本地保存的 `installer-config.json`
- `payload` 里的第三方安装包

如果你要对外分发成品，建议走：

- GitHub 仓库放源码
- GitHub Release 或网盘分发 `.exe + payload 安装包`

## 开源协议

本项目采用 [MIT License](./LICENSE)。

你可以自由使用、修改、分发，但请自行评估 `OpenClaw` 官方项目、第三方模型服务、以及第三方安装包各自的许可和分发要求。
