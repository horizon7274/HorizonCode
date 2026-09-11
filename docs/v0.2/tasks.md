# HorizonCode — Tasks v0.2：工具系统

## 依赖关系

```
T1（工具契约） ─┬─▶ T2（路径与结果） ─┬─▶ T3（文件工具） ─┐
                │                       ├─▶ T4（搜索工具） ─┤
                │                       └─▶ T5（命令工具） ─┤
                └─▶ T6（注册中心） ──────────────────────────┤
T1 ─▶ T7（Provider 工具流） ─▶ T8（历史适配） ───────────────┤
                                                        ▼
                                             T9（TUI 编排）
                                                        │
                                             T10（接入主流程）
                                                        │
                                             T11（端到端验证）
```

## T1：定义工具领域模型与统一契约

**影响文件**：`horizoncode/tools/base.py`、`horizoncode/tools/__init__.py`、`tests/test_tools_base.py`

**依赖任务**：无

**产出**：

- 定义工具元信息、调用请求、结构化执行结果和抽象工具接口。
- 统一结果至少区分成功、参数错误、路径越界、未找到、冲突、拒绝、超时和执行失败。
- 约定工具为异步执行，调用请求携带 Provider 关联 ID、工具名和已解析的参数对象。
- 为公开类型和复杂内部转换添加中文 docstring。

**参考资料**：`docs/v0.2/spec.md` → 能力清单 1、非功能要求 → 失败可恢复

## T2：实现项目根目录路径守卫与结果序列化

**影响文件**：`horizoncode/tools/paths.py`、`horizoncode/tools/result.py`、`tests/test_tool_paths.py`

**依赖任务**：T1

**产出**：

- 启动时固定项目根目录，解析相对路径并拒绝绝对路径、`..` 穿越和符号链接逃逸。
- 实现工具结果到终端展示和 Provider 回灌载荷的稳定序列化。
- 为文本和结果设置单一的截断入口，保证截断状态可被模型识别。

**参考资料**：`docs/v0.2/spec.md` → 非功能要求 → 作用域隔离、输出有界

## T3：实现读文件、写文件和唯一替换编辑工具

**影响文件**：`horizoncode/tools/files.py`、`tests/test_file_tools.py`

**依赖任务**：T1、T2

**产出**：

- 读取 UTF-8 文本文件，支持可选行范围并返回内容和实际范围。
- 覆盖写入文本文件；父目录不存在时返回结构化失败，不隐式创建目录。
- 基于原文唯一匹配的替换编辑；零次匹配和多次匹配分别返回可行动的冲突信息，文件不得改变。
- 拒绝目录、二进制文件和项目外路径。

**参考资料**：`docs/v0.2/spec.md` → 能力清单 2；`AGENTS.md` → 文件操作约束

## T4：实现 glob 文件发现和代码内容搜索工具

**影响文件**：`horizoncode/tools/search.py`、`tests/test_search_tools.py`

**依赖任务**：T1、T2

**产出**：

- 支持项目根目录内的 glob 模式匹配，只返回文件并按稳定顺序排列。
- 支持文本与正则两种内容搜索，返回相对路径、行号、行文本和匹配位置。
- 无匹配、无效正则、权限错误和结果被截断均返回结构化结果。

**参考资料**：`docs/v0.2/spec.md` → 能力清单 3、非功能要求 → 输出有界

## T5：实现带确认和超时的命令执行工具

**影响文件**：`horizoncode/tools/command.py`、`tests/test_command_tool.py`

**依赖任务**：T1、T2

**产出**：

- 通过可注入的确认回调在执行前询问用户；拒绝时不创建子进程。
- 在项目根目录启动命令，捕获标准输出、标准错误、退出码、超时和启动异常。
- 不接受由工具调用参数指定的工作目录或 shell 类型，避免越过固定边界。

**参考资料**：`docs/v0.2/spec.md` → 能力清单 4、非功能要求 → 命令授权与超时可控

## T6：实现工具注册中心和双协议 Schema 导出

**影响文件**：`horizoncode/tools/registry.py`、`horizoncode/tools/__init__.py`、`tests/test_tool_registry.py`

**依赖任务**：T1、T3、T4、T5

**产出**：

- 集中注册六个核心工具，支持按名称查找并拒绝重复名称。
- 从统一元信息生成 Anthropic 与 OpenAI 所需的工具声明，参数 Schema 保持等价。
- 提供默认注册表构造入口，供主流程一次性注入。

**参考资料**：`docs/v0.2/spec.md` → 能力清单 5、设计骨架 → 工具层

## T7：扩展 Provider 以解析流式工具调用

**影响文件**：`horizoncode/providers/base.py`、`horizoncode/providers/openai.py`、`horizoncode/providers/anthropic.py`、`tests/test_providers.py`、新增 Provider 流测试文件

**依赖任务**：T1、T6

**产出**：

- 扩展统一流帧类型以表达完整工具调用及其关联 ID。
- OpenAI Provider 拼接每个 `tool_call` 的分片参数，并在调用完成后产生完整调用。
- Anthropic Provider 处理工具使用内容块的输入 JSON 分片，并在内容块结束时产生完整调用。
- 两个 Provider 请求均附带相应协议格式的工具 Schema，并保留既有文本流和错误帧行为。

**参考资料**：`horizoncode/providers/openai.py` → `stream_chat`；`horizoncode/providers/anthropic.py` → `stream_chat`；`docs/v0.2/spec.md` → 能力清单 7

## T8：扩展会话历史以保存和转换工具消息

**影响文件**：`horizoncode/history.py`、`tests/test_history.py`

**依赖任务**：T1、T7

**产出**：

- 在内部会话记录中保存 assistant 工具调用与 tool 结果，并持久化关联 ID、名称、参数和结果。
- 为 Anthropic 和 OpenAI 分别生成合法的下一轮 API 消息，工具结果必须与原调用 ID 对应。
- 保持 v0.1 的普通用户/助手/思考消息历史行为与旧会话读取兼容。

**参考资料**：`horizoncode/history.py` → `HistoryManager.get_api_messages`；`docs/v0.2/spec.md` → 设计骨架 → 历史层

## T9：在 TUI 中编排工具调用、确认和结果展示

**影响文件**：`horizoncode/tui/app.py`、`tests/test_tui_tools.py`

**依赖任务**：T3、T4、T5、T6、T7、T8

**产出**：

- 收集一轮流中全部完整工具调用；流结束后按原始顺序逐个执行。
- 对每个命令工具调用显示完整命令、项目工作目录和超时，并读取用户明确确认。
- 在终端简洁展示每个工具的成功或失败摘要，完整结构化结果写入历史。
- 一轮调用执行完毕后直接回到输入提示，不发起第二次 Provider 请求。
- 用户中断流时不执行尚未完整收到的工具调用。

**参考资料**：`horizoncode/tui/app.py` → `_stream_response`；`docs/v0.2/spec.md` → 能力清单 8、9

## T10：接入主流程与配置项目根目录

**影响文件**：`horizoncode/main.py`、`horizoncode/config.py`（如需）、`config.example.yaml`、`README.md`、相关测试

**依赖任务**：T6、T9

**产出**：

- 在启动时确定项目根目录，创建默认工具注册表并注入 TUI 与 Provider。
- 更新示例配置和 README，说明工具可用性、项目根目录限制、命令确认和本章不含自动循环的边界。
- 既有不使用工具的普通对话仍可正常启动和结束。

**参考资料**：`horizoncode/main.py` → `_run_app`；`docs/v0.2/spec.md` → 完成定义

## T11：端到端验证

**影响文件**：`tests/`、`docs/v0.2/checklist.md`（必要时补充可观测验收命令）

**依赖任务**：T10

**产出**：

- 执行全量自动化测试，并对六个工具、双 Provider 分片解析、命令拒绝和多调用无循环行为完成验收。
- 使用 mock 流模拟至少两个独立工具调用，验证结果持久化且不会发生第二次 API 请求。
- 按 checklist 的端到端场景在临时项目中完成一次人工验证。

**参考资料**：`docs/v0.2/checklist.md`（全部条目）
