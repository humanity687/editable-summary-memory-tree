【任务 T1·thinking 配置不生效】
这个平台支持通过 Agent 配置中的 thinking 字段（true/false）控制是否启用 DeepSeek 的思考模式。但实测发现：无论 config 里怎么配置 thinking， 发给 LLM API 的请求都不带任何思考模式参数——配置完全不生效。
影响：想开启思考模式的 agent 静默变成普通模式；想关闭的（省 token）也无法关闭。

工作目录为当前目录（旧版 AHI 系统源码）。
验收：`python3 accept_t1.py` 退出码 0 = 通过。修复完成并确认验收通过后调用 finish。