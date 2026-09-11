# Ollama 原生 Provider 实施任务

1. 明确 Ollama 原生协议映射与测试样本。
   - 影响文件：`spec.md`、`checklist.md`
   - 依赖任务：无
   - 参考资料：Ollama 官方 Tool calling 文档的流式工具调用与消息回灌示例。

2. 新增 Ollama Provider 并接入 Provider 注册表。
   - 影响文件：`horizoncode/providers/ollama.py`、`horizoncode/providers/__init__.py`
   - 依赖任务：1
   - 参考资料：`horizoncode/providers/base.py` 的 `BaseProvider.stream_chat`；`horizoncode/providers/openai.py` 的 HTTP 流处理与资源关闭逻辑。

3. 实现 Ollama NDJSON 响应的文本、思考、工具调用和完成帧转换。
   - 影响文件：`horizoncode/providers/ollama.py`、`tests/test_ollama_provider.py`
   - 依赖任务：2
   - 参考资料：Ollama 官方 Chat API；`horizoncode/providers/openai.py` 的 `StreamFrame` 产出约定。

4. 实现 Ollama 原生工具调用校验与稳定调用标识生成。
   - 影响文件：`horizoncode/providers/ollama.py`、`tests/test_ollama_provider.py`
   - 依赖任务：3
   - 参考资料：`horizoncode/tools/base.py` 的 `ToolCall`；Ollama 官方 Tool calling 文档。

5. 为工具注册中心增加 Ollama 工具声明格式。
   - 影响文件：`horizoncode/tools/registry.py`、`tests/test_tools.py`
   - 依赖任务：1
   - 参考资料：`ToolRegistry.definitions_for`；Ollama Tool calling 文档的 `tools` 请求体。

6. 为历史管理器增加 Ollama 工具调用和工具结果消息转换。
   - 影响文件：`horizoncode/history.py`、`tests/test_tool_protocols.py`
   - 依赖任务：4、5
   - 参考资料：`HistoryManager._get_openai_messages`；Ollama Tool calling 文档的 assistant `tool_calls` 与 tool `tool_name` 消息。

7. 扩展配置样例与 README 中的后端说明。
   - 影响文件：`config.example.yaml`、`README.md`
   - 依赖任务：2
   - 参考资料：`config.example.yaml`；Ollama Windows 与 API Introduction 官方文档。

8. 接入主流程。
   - 影响文件：`horizoncode/providers/__init__.py`、`horizoncode/main.py`（仅在现有导入链不足时）
   - 依赖任务：2、5、6
   - 参考资料：`horizoncode/main.py` 的 `get_provider` 创建逻辑；`horizoncode/tui/app.py` 的 `_stream_response`。

9. 端到端验证。
   - 影响文件：`tests/test_providers.py`、`tests/test_ollama_provider.py`、`checklist.md`
   - 依赖任务：3、4、5、6、7、8
   - 参考资料：`uv run pytest`；Ollama `/api/chat` 的本地运行方式。
