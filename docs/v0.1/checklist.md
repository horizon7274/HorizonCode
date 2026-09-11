# HorizonCode — Checklist v0.1

## 配置层

- [ ] `horizoncode/config.py` 存在，能解析 YAML 中至少两组 profile（anthropic + openai）
- [ ] `${ENV_VAR}` 语法可正确替换：设置 `HORIZON_TEST_KEY=sk-test123` 后，配置中写 `${HORIZON_TEST_KEY}` 读到值为 `sk-test123`
- [ ] 项目级 `./horizoncode.yaml` 中的 `model` 字段覆盖用户级 `~/.horizoncode/config.yaml` 的同名字段
- [ ] 必填字段缺失（如 `api_key` 为空）时，程序启动报错并输出包含 "missing" 和字段名的错误信息，不静默跳过

## Provider 层

- [ ] `grep -r "class BaseProvider" horizoncode/providers/` 能找到抽象基类定义
- [ ] `grep -r "stream_chat" horizoncode/providers/base.py` 返回 ≥1 条，且方法签名含 `async` 和 `AsyncIterator`
- [ ] Anthropic provider: 调用真实 API 返回流式响应，首 chunk 在 3 秒内到达（网络正常时）
- [ ] OpenAI provider: 调用真实 API 返回流式响应，首 chunk 在 3 秒内到达（网络正常时）
- [ ] Anthropic provider: extended thinking 开启时，流中能解析到 `type: "thinking"` 的帧
- [ ] 传入无效 API key 时，provider 返回 `type: "error"` 帧且程序不崩溃

## TUI 交互

- [ ] 启动 `horizoncode` 后看到输入提示符，可键入文本
- [ ] 粘贴多行文本（≥3 行）后，输入缓冲区保留完整换行
- [ ] 按 Enter 发送消息后，AI 回复以逐字流式出现，不是整块突然出现
- [ ] thinking 内容以浅灰色/斜体渲染，与正文肉眼可区分
- [ ] 流式输出期间按 `Ctrl+C`，生成立即停止，已输出的内容保留在屏幕上
- [ ] 输入 `/exit` 或按 `Ctrl+D`，程序正常退出，退出码为 0
- [ ] 输入 `/help` 看到可用命令列表，至少包含 `/exit` 和 `/help`
- [ ] API 调用失败时（如断网），界面显示红色错误文本，程序不退出

## 对话记忆

- [ ] 连续发送 3 条消息且第 3 条引用第 1 条的内容（如 "刚才你说的第一点再解释一下"），AI 能正确关联上下文
- [ ] 退出后重新启动，`~/.horizoncode/sessions/` 下存在一个 JSON 文件，内容包含刚才对话的所有轮次

## Provider 切换

- [ ] 修改配置文件 `default_profile` 从 anthropic 切到 openai（或反过来），重启后对话使用的模型确实切换了（可通过模型自我介绍验证）

## 端到端

- [ ] 从零启动：`pip install -e .` → 写配置文件 → 运行 `horizoncode` → 输入 "用中文说一句你好" → 看到流式中文回复 → `/exit` → `~/.horizoncode/sessions/` 下有 JSON 历史文件 → `cat` 该文件能看到用户消息和 AI 回复
- [ ] 同样流程用另一个 provider 再跑一遍，确认双后端都可正常工作
