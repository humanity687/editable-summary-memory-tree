【任务 T2·data/ 目录下的 config.json 被误注册为新 Agent】
系统启动时扫描 agents/ 目录发现 Agent。实测发现：如果在某个 Agent 的 data/ 目录下存在任何 config.json（例如用户调试时把配置文件复制到了 data/ 里备份）， 它会被当成一个全新 Agent 注册，出现在 Agent 列表里。
影响：启动时可能加载意外配置，导致 Agent 注册异常、列表污染、 甚至尝试启动不存在的 Agent。
线索：Agent 发现注册逻辑在 src/process_manager.py。

工作目录为当前目录（旧版 AHI 系统源码）。
验收：`python3 accept_t2.py` 退出码 0 = 通过。修复完成并确认验收通过后调用 finish。