# OpenClaw 中文助手 V1.0

作者：青墨荀
欢迎大家关注我全网同名账号，喜欢折腾，有什么问题欢迎找我交流。

这是一个面向小白用户的 Windows 中文安装器。它会被打包成单文件 `OpenClawSetupTool.exe`，目标电脑不需要预装 Python、Node 或 .NET 才能直接启动。

## 功能

- 检测 Node、Python、Git、OpenClaw 是否已安装
- 从当前目录 `payload` 文件夹自动识别安装包并静默安装
- 通过 `npm install -g openclaw@latest` 安装 OpenClaw，失败时回退到官方 `install.ps1`
- 调用 `openclaw onboard --non-interactive` 完成一键预配置
- 把 `CUSTOM_API_KEY` 写入当前用户环境变量，并让 OpenClaw 通过 `ref` 模式引用它
- 自动生成内部 `Provider ID`，不需要最终用户理解或填写
- 明确区分 `OpenAI 兼容接口` 和 `Anthropic 兼容接口`
- 安装完成后在工具内尝试启动 Gateway，并自动打开带 token 的控制台地址
- 内置 `启动网关 / 重启网关 / 打开控制台 / 复制控制台地址` 按钮
- 内置常用斜杠命令快捷按钮，点一下就复制到剪贴板

## 目录结构

构建产物会放到：

```text
release/OpenClawSetupTool/
  OpenClawSetupTool.exe
  installer-config.template.json
  payload/
    README.txt
```

## 使用方式

1. 把 Node、Python、Git 的安装包按需放到 `payload` 目录。
2. 运行 `OpenClawSetupTool.exe`。
3. 填入或预先写好 `Base URL`、`模型 ID`、`API Key`。
4. 根据接口文档选择协议类型：
   - `/v1/chat/completions` 或 `/v1/responses` 选 `OpenAI 兼容接口`
   - `/v1/messages` 选 `Anthropic 兼容接口`
5. 点击“开始一键安装”。

如果你想让朋友双击后几乎不需要输入，可以把 `installer-config.template.json` 复制为 `installer-config.json`，再把模型信息提前填好。注意这会让 API Key 以明文方式保存在这个 JSON 文件里。

## 推荐安装包

- Node：官方 Windows x64 `.msi`
- Python：官方 Windows x64 `.exe`
- Git：Git for Windows `.exe`

## 打包

在当前目录执行：

```powershell
.\build.ps1
```

脚本会自动调用 PyInstaller，生成单文件管理员权限 `.exe`。
