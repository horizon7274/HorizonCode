# HorizonCode — Tasks v0.1

## 依赖关系

```
T1 (脚手架)
 ├─▶ T2 (配置层)
 ├─▶ T3 (Provider 抽象接口)
 │    ├─▶ T4 (Anthropic Provider)
 │    └─▶ T5 (OpenAI Provider)
 └─▶ T6 (历史持久化)

T2 + T3 + T6 ──▶ T7 (TUI 核心)
                      │
T4 + T5 + T7 ──▶ T8 (接入主流程)
                      │
                      ▼
                 T9 (端到端验证)
```

---

## T1: 项目脚手架

**影响文件**: `pyproject.toml`, `horizoncode/__init__.py`, `horizoncode/main.py` (空壳)

**产出**:
- `pyproject.toml` 含依赖声明：`prompt_toolkit`, `rich`, `pyyaml`, `httpx`（或 `anthropic` + `openai` SDK），Python ≥3.10
- 项目目录结构建好：
  ```
  horizoncode/
  ├── __init__.py
  ├── main.py
  ├── config.py
  ├── providers/
  │   ├── __init__.py
  │   ├── base.py
  │   ├── anthropic.py
  │   └── openai.py
  ├── tui/
  │   ├── __init__.py
  │   └── app.py
  └── history.py
  ```
- `pip install -e .` 可成功安装，`horizoncode` 命令可调用（打印占位信息）

**参考资料**: spec.md → 设计骨架、非功能要求

---

## T2: 配置层

**影响文件**: `horizoncode/config.py`

**产出**:
- YAML 配置文件加载器
- 支持四个核心字段：`protocol`, `model`, `base_url`, `api_key`
- 支持多 profile 配置，`default_profile` 指定默认使用哪个
- 双层配置合并逻辑：先读 `~/.horizoncode/config.yaml`，再读 `./horizoncode.yaml`（若存在则覆盖同名字段）
- `api_key` 值中的 `${ENV_VAR}` 模式自动替换为环境变量值
- 无效配置（缺失必填字段）时给出明确错误提示，不静默失败

**参考资料**: spec.md → 设计骨架 → 配置层、双层配置能力

---

## T3: Provider 抽象接口

**影响文件**: `horizoncode/providers/base.py`, `horizoncode/providers/__init__.py`

**产出**:
- 抽象基类，定义异步方法 `stream_chat(messages, model)` 返回 AsyncIterator
- 每帧数据结构：区分 `type`（`thinking` / `content` / `error` / `done`），携带文本内容
- Provider 工厂函数：根据 `protocol` 字符串返回对应 Provider 实例
- 接口文档（docstring）明确约定：messages 格式、异常处理方式

**参考资料**: spec.md → Provider 接口、非功能要求 → 扩展性

---

## T4: Anthropic Provider

**影响文件**: `horizoncode/providers/anthropic.py`

**产出**:
- 实现 `stream_chat`，调用 Anthropic Messages API（`/v1/messages`）with `stream=True`
- 解析 SSE 事件流，区分 `content_block_delta`（正文）与 `thinking_delta`（extended thinking）
- 将原始 SSE 事件映射为统一接口的帧类型
- 异常处理：网络错误、认证失败、限流，映射为 `error` 帧并保留原始错误信息供日志使用
- 支持通过 `base_url` 指向 Anthropic 兼容代理

**参考资料**: Anthropic API 文档 (Messages streaming), spec.md → Extended Thinking 透传

---

## T5: OpenAI Provider

**影响文件**: `horizoncode/providers/openai.py`

**产出**:
- 实现 `stream_chat`，调用 OpenAI Chat Completions API（`/v1/chat/completions`）with `stream=True`
- 解析 SSE 事件流（`data: [DONE]` 终止）
- 将 `choices[0].delta.content` 映射为 `content` 帧
- 异常处理同 T4
- 支持通过 `base_url` 指向 OpenAI 兼容代理（如 Azure、本地模型）

**参考资料**: OpenAI API 文档 (Chat streaming), spec.md → 双 Provider 支持

---

## T6: 对话历史持久化

**影响文件**: `horizoncode/history.py`

**产出**:
- 会话保存：退出时将当前对话保存为 JSON 文件到 `~/.horizoncode/sessions/`
- 文件名格式：`YYYY-MM-DD_HHMMSS_<truncated-first-message>.json`
- 消息格式：`[{role, content, timestamp}, ...]`，其中 thinking 内容用 `role: "thinking"` 标记
- 会话加载：启动时可选加载历史会话查看（只读），本次不做会话恢复编辑（Out of Scope 标记）
- 自动创建 `~/.horizoncode/` 目录

**参考资料**: spec.md → 历史层、对话历史持久化能力

---

## T7: TUI 核心

**影响文件**: `horizoncode/tui/app.py`, `horizoncode/tui/__init__.py`

**产出**:
- 基于 `prompt_toolkit` 的输入循环：支持多行输入（粘贴保留换行），Enter 发送，`Alt+Enter` 插换行
- 基于 `rich` 的流式渲染：`Live` context 下逐 chunk 更新面板
- thinking 内容使用 dimmed 样式与正文区分（浅灰色或斜体）
- 中断快捷键：`Ctrl+C` 中止当前流式生成，已生成内容保留在界面上
- 退出命令：`/exit` 或 `Ctrl+D` 退出程序，退出前自动保存历史
- `/help` 显示可用命令
- 基础错误展示：API 错误以红色文本内联显示

**依赖**: T2（配置）, T3（Provider 接口）, T6（历史）

**参考资料**: spec.md → TUI 方案、能力清单 1/3/5/6/7

---

## T8: 接入主流程

**影响文件**: `horizoncode/main.py`

**产出**:
- CLI 入口 `horizoncode`：解析命令行参数（可选指定 config 路径）
- 启动流程：加载配置 → 创建 Provider → 初始化 TUI（传入 provider + history manager）
- 优雅退出：捕获 `SIGINT`/`SIGTERM`，确保历史已保存
- 日志：错误写入 `~/.horizoncode/horizoncode.log`，不在 TUI 上展示（避免刷屏）

**依赖**: T4, T5, T7

**参考资料**: spec.md → 设计骨架（全链路）

---

## T9: 端到端验证

**影响文件**: 无（纯验证）或新增 `tests/` 目录

**产出**:
- 按 checklist.md 逐项验证，全部通过
- 手动测试场景覆盖：单轮对话、多轮记忆、切换 provider、流式中断、重启后历史存在
- 若时间允许，编写 provider 层的单元测试（mock API 响应）

**依赖**: T8

**参考资料**: checklist.md（所有条目）
