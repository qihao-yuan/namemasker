# NameMasker - 隐私遮盖工具

自动识别并遮盖图片、Word 文档、PDF 中的人名、手机号、身份证号等敏感信息。支持大模型 API 自动识别 + 手动画框，批量处理整个文件夹。

## ✨ 功能

- **大模型自动识别** - 调用 Qwen-VL / GPT-4o 等视觉大模型，自动定位图中敏感文字
- **手动画框** - 在图片上直接拖拽画框，精确标记需要遮盖的区域
- **多种遮盖方式** - 填充、模糊、像素化、棋盘格（半透明灰白格）
- **批量处理** - 选择文件夹一键处理图片或文档，支持子文件夹递归扫描
- **多轮查漏** - 自动遮盖已识别区域后再次检查，减少遗漏
- **边缘识别** - 对图片四周边缘区域单独识别，防止边角遗漏
- **自定义识别目标** - 不限于人名，可自定义为手机号、地址、身份证号等任意内容
- **API 可配置** - 支持 DashScope、OpenAI 等任意 OpenAI 兼容接口
- **Word 文档处理** - 自动识别并替换 docx 中的敏感文本
- **PDF 文档处理** - 每页渲染为图像后通过视觉模型检测并遮盖
- **处理过程可视化** - 实时日志 + 页面预览（点击可放大缩小）
- **设置自动保存** - 关闭时自动保存所有设置，下次无需重填

## 📦 安装

### 方式一：pip 安装

```bash
pip install namemasker

# 完整安装（含 Word/PDF/拖拽支持）
pip install namemasker[all]
```

### 方式二：直接下载 exe

前往 [Releases](https://github.com/qihao-yuan/namemasker/releases) 下载最新版 `NameMasker.exe`，双击即用，无需安装 Python。

### 方式三：源码安装

```bash
git clone https://github.com/qihao-yuan/namemasker.git
cd namemasker
pip install -e ".[all]"
```

## 🔑 API 配置（必读）

本工具依赖大模型 API 进行自动识别。你需要先获取一个 API Key。

### 方案一：阿里云 DashScope（默认，推荐）

1. 注册阿里云账号：https://www.aliyun.com
2. 开通 DashScope 服务：https://dashscope.console.aliyun.com
3. 进入 **API-KEY 管理**，创建一个 API Key
4. 新用户有免费额度，之后按量计费（很便宜）

在软件中填写：
- **API Key**：粘贴你的 DashScope API Key
- **API 地址**：保持默认 `https://dashscope.aliyuncs.com/compatible-mode/v1`
- **模型**：保持默认 `qwen2.5-vl-72b-instruct`

### 方案二：OpenAI

1. 获取 OpenAI API Key：https://platform.openai.com/api-keys
2. 在软件中填写：
   - **API Key**：你的 OpenAI Key（`sk-...`）
   - **API 地址**：`https://api.openai.com/v1`
   - **模型**：`gpt-4o`

### 方案三：其他 OpenAI 兼容接口

任何兼容 OpenAI Chat Completions API 的服务都可以使用，包括：
- 本地部署的 vLLM / Ollama（API 地址填 `http://localhost:8000/v1`）
- 第三方中转（硅基流动、零一万物等）

只需要在软件中填写对应的 **API 地址**、**API Key** 和 **模型名称** 即可。

> 所有设置在关闭软件时会自动保存到 `~/.namemasker_config.json`，下次打开无需重填。

## 🚀 使用

### 启动

```bash
# pip 安装后
python -m namemasker

# exe 版本直接双击 NameMasker.exe
```

### 图片处理

1. 填写 API Key（首次使用，之后自动记住）
2. 打开图片（按钮 / Ctrl+O / 拖拽）
3. 点击「自动识别」- 大模型会自动定位敏感区域
4. 也可以手动在图片上拖拽画框
5. 选择遮盖方式（填充/模糊/像素化/棋盘格）
6. 点击「预览」查看效果，满意后「保存」

### 批量处理图片

点击「批量处理文件夹」-> 选输入文件夹 -> 选输出文件夹 -> 自动处理所有图片

### 文档处理 (Word / PDF)

1. 点击「处理文档」或拖拽 .docx / .pdf 到窗口
2. 弹出处理对话框，实时显示日志和页面预览
3. 检测完成后查看结果，点击缩略图可放大查看
4. 确认无误后点击「确认保存」

### 批量处理文档

点击「批量处理文档」-> 选输入文件夹 -> 选输出文件夹 -> 自动处理所有 .docx/.pdf

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+O | 打开图片 |
| Ctrl+S | 保存 |
| Ctrl+Z | 撤销上一个框 |
| 右键点击框 | 删除该框 |

## ⚙️ 设置说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| API Key | 大模型服务的密钥 | 环境变量 `DASHSCOPE_API_KEY` |
| API 地址 | OpenAI 兼容接口地址 | DashScope |
| 模型 | 视觉语言模型名称 | qwen2.5-vl-72b-instruct |
| 识别轮数 | 多轮查漏次数（第2轮起对已遮盖图再检查） | 2 |
| 识别目标 | 要识别的内容描述（如"中文人名"、"手机号"、"身份证号"） | 中文人名 |
| 遮盖方式 | 填充 / 模糊 / 像素化 / 棋盘格 | 填充 |
| 边距扩展% | 向外扩展遮盖区域的比例 | 0 |

## 📋 依赖

- Python >= 3.9
- Pillow >= 10.0.0
- openai >= 1.0.0
- python-docx >= 1.0.0（可选，Word 文档处理）
- PyMuPDF >= 1.24.0（可选，PDF 文档处理）
- tkinterdnd2 >= 0.3（可选，拖拽功能）

exe 版本已内置所有依赖，无需额外安装。

## 📄 License

MIT
