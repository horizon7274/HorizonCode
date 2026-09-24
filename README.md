# HorizonCode

**v0.3.0** · 终端 AI 编程助手

HorizonCode 是一个运行在当前项目目录中的终端 AI 编程助手。它支持多轮对话、流式回复和工具调用：模型可以读取、搜索和修改项目文件，也可以在用户逐条确认后执行命令。

## 功能

- **交互式终端界面**：基于 prompt_toolkit 和 Rich，支持流式回复、取消当前任务和用量展示
- **Agent 工具循环**：自动执行模型请求的工具，并将结果交回模型继续处理，直到任务完成
- **项目文件工具**：读取、写入、唯一匹配编辑、文件查找和代码搜索；文件路径限制在启动时的项目根目录内
- **命令执行确认**：每条命令执行前显示命令和工作目录，需用户明确输入 `y` 或 `yes`；命令运行目录为项目根目录，超时 30 秒
- **计划与接力执行**：`/plan` 进入只读计划模式，`/do` 退出计划模式并开始执行
- **多协议支持**：Anthropic、OpenAI Chat Completions 和 Ollama，可通过 YAML 配置切换
- **会话保存**：退出时将会话保存为 JSON 文件
- **分层配置**：支持用户级、项目级和命令行指定的配置文件

> 文件工具限制在项目根目录内；命令工具需要逐条确认，但这并不构成命令的文件系统沙箱。请在信任当前模型请求和命令内容后再确认执行。

## 环境要求

- Python 3.10 或更高版本
- [uv](https://github.com/astral-sh/uv)

## 快速开始

```bash
# 克隆项目并进入目录
git clone <仓库地址>
cd HorizonCode

# 安装依赖
uv sync

# 创建用户配置目录，并复制示例配置
mkdir -p ~/.horizoncode
cp config.example.yaml ~/.horizoncode/config.yaml

# 编辑配置，填入 API 密钥后启动
uv run horizoncode
```

Windows PowerShell 下可使用以下命令创建配置文件：

```powershell
New-Item -ItemType Directory -Force "$HOME\.horizoncode"
Copy-Item config.example.yaml "$HOME\.horizoncode\config.yaml"
```

## 配置

配置文件采用 YAML 格式。每个 profile 包含协议、模型、服务地址和 API 密钥；Ollama 本地服务的 `api_key` 可以留空。API 密钥支持通过 `${ENV_VAR}` 引用环境变量。

```yaml
default_profile: claude
profiles:
  claude:
    protocol: anthropic
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
agent:
  max_iterations: 25
```

### 配置文件优先级

配置按以下顺序加载，后加载的配置覆盖先加载的同名值：

1. `~/.horizoncode/config.yaml` — 用户级配置
2. `./.horizoncode/config.yaml` — 项目本地配置
3. `./horizoncode.yaml` — 项目级配置
4. `horizoncode -c <path>` — 显式指定的配置文件，优先级最高

### 使用 OpenAI 兼容服务

```yaml
default_profile: openai
profiles:
  openai:
    protocol: openai
    model: gpt-4o
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
```

也可以将 `base_url` 设置为其他 OpenAI Chat Completions 兼容服务的地址。

### 使用本地 Ollama

先安装并启动 [Ollama](https://ollama.com)，并拉取支持工具调用的模型，例如：

```bash
ollama pull qwen2.5-coder:7b
```

配置示例：

```yaml
default_profile: local
profiles:
  local:
    protocol: ollama
    model: qwen2.5-coder:7b
    base_url: http://localhost:11434
    api_key: ""
```

## 使用

```bash
uv run horizoncode
```

常用命令行选项：

```text
-c, --config <path>  指定额外的配置文件
-v, --verbose        显示详细日志
--version            显示版本
```

### 内置工具

| 工具 | 用途 |
|------|------|
| `read_file` | 读取项目文件 |
| `write_file` | 写入项目文件 |
| `edit_file` | 将唯一匹配的文本替换为新内容 |
| `glob_files` | 按模式查找项目文件 |
| `grep_code` | 在项目文件中搜索代码 |
| `execute_command` | 在项目根目录执行命令；每条命令都需确认 |

文件工具只允许访问启动时项目根目录内的路径。工具调用完成后，结果会交回模型继续处理；只读工具可以并发执行，可能产生副作用的操作会按顺序执行。

### 快捷键与命令

| 操作 | 输入 |
|------|------|
| 发送消息 | Enter |
| 插入换行 | Alt+Enter |
| 取消当前任务 | Ctrl+C |
| 退出 | `/exit` 或 Ctrl+D |
| 查看帮助 | `/help` |
| 进入只读计划模式 | `/plan` |
| 按计划接力执行 | `/do` |

会话在程序运行期间保存在内存中，并在退出时保存到 `~/.horizoncode/sessions/`。当前终端界面会话不会在下次启动时自动恢复。

## 开发

```bash
uv sync
uv run pytest
uv run horizoncode
```
