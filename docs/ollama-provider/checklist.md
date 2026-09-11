# Ollama 原生 Provider 验收清单

- [ ] `uv run pytest` 全部通过，且无需安装 Ollama 或下载模型。
- [ ] `get_provider("ollama", api_key="", base_url="http://localhost:11434")` 返回 Ollama Provider 实例。
- [ ] 配置模板包含 `protocol: ollama`、`model: qwen2.5-coder:7b`、`base_url: http://localhost:11434` 和空字符串 `api_key`。
- [ ] 对 `POST http://localhost:11434/api/chat` 的请求包含 `model`、`messages`、`tools` 与 `stream: true`。
- [ ] 模拟的 NDJSON 流中 `message.thinking`、`message.content`、`message.tool_calls` 分别产生 `thinking`、`content`、`tool_call` 帧，最终产生一个 `done` 帧。
- [ ] Ollama 返回 `read_file` 与 `{"path":"README.md"}` 时，产生的 `ToolCall` 名称为 `read_file`，参数为对应字典，且调用 ID 非空。
- [ ] `ToolRegistry.definitions_for("ollama")` 返回六个 `type: function` 的工具定义。
- [ ] 含工具调用及结果的历史转换后，Ollama assistant 消息包含 `tool_calls`，工具结果消息的 `role` 是 `tool`、`tool_name` 是被调用工具名。
- [ ] 服务无法连接时，Provider 返回一条 `error` 帧，其文本含“无法连接 Ollama”。
- [ ] 服务响应超过 120 秒时，Provider 返回一条 `error` 帧，其文本含“请求超时”。
- [ ] 在已启动 Ollama 并已拉取支持工具调用模型的机器上，以本地 profile 运行 `uv run horizoncode`，请求读取项目内 `README.md` 后可看到工具调用与执行结果，下一次提问可基于该结果回答。
