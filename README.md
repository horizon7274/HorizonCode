# HorizonCode

终端 AI 编程助手，类似 Claude Code。纯对话模式，支持流式输出和多轮记忆。

## 功能

- **交互式 TUI**：基于 prompt_toolkit + rich 的终端聊天界面
- **流式输出 (SSE)**：逐字打印 AI 回复，支持中途取消 (Ctrl+C)
- **多轮对话**：AI 记住会话内所有上下文
- **双协议支持**：Anthropic Claude API 和 OpenAI API，YAML 配置切换
- **Extended Thinking**：展示 Claude 的思考过程（dimmed 样式区分）
- **持久化**：会话自动保存为 JSON 文件
- **双层配置**：用户级 + 项目级配置，项目级覆盖用户级

## 环境要求

- Python >= 3.10
- [uv](https://github.com/astral-sh/uv)（包管理）

## 快速开始

```bash
# 1. 克隆项目
git clone <repo-url>
cd my_coding_agent

# 2. 安装依赖
uv sync

# 3. 创建配置
mkdir -p ~/.horizoncode
cp config.example.yaml ~/.horizoncode/config.yaml
# 编辑 ~/.horizoncode/config.yaml，填入你的 API key

# 4. 运行
uv run horizoncode
```

## 配置

配置文件为 YAML 格式，四个必填字段：

| 字段 | 说明 |
|------|------|
| `protocol` | 协议类型：`anthropic` 或 `openai` |
| `model` | 模型名称 |
| `base_url` | API 端点地址（支持代理/兼容接口） |
| `api_key` | API 密钥，支持 `${ENV_VAR}` 引用环境变量 |

### 搜索顺序（后者覆盖前者）

1. `~/.horizoncode/config.yaml` — 用户级
2. `./.horizoncode/config.yaml` — 项目本地
3. `./horizoncode.yaml` — 项目级
4. `horizoncode -c <path>` — 命令行指定（最高优先级）

### 示例：使用 Anthropic

```yaml
default_profile: claude
profiles:
  claude:
    protocol: anthropic
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
```

### 示例：使用 OpenAI 兼容代理

```yaml
default_profile: glm
profiles:
  glm:
    protocol: anthropic           # 智谱 GLM 支持 Anthropic 协议
    model: glm-4.7-flash
    base_url: https://open.bigmodel.cn/api/anthropic
    api_key: xxx-your-key
```

## 使用

```
uv run horizoncode
```

### 快捷键

| 操作 | 按键 |
|------|------|
| 发送消息 | Enter |
| 插入换行 | Alt+Enter |
| 取消生成 | Ctrl+C |
| 退出 | /exit 或 Ctrl+D |
| 帮助 | /help |

## 项目结构

```
horizoncode/
├── main.py              # CLI 入口
├── config.py            # YAML 配置加载
├── history.py           # 会话持久化
├── providers/
│   ├── base.py          # 抽象接口 + 工厂
│   ├── anthropic.py     # Anthropic SSE 流式
│   └── openai.py        # OpenAI SSE 流式
└── tui/
    └── app.py           # TUI 交互界面
```

## 开发

```bash
uv sync              # 安装依赖（含 dev）
uv run pytest        # 运行测试
uv run horizoncode   # 启动
```
