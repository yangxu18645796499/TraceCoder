# TraceCoder 公开测试对齐路径

新入口是 `trace_learn_coder.py`，只接受 WSL/Linux CPython 3.12.3 和冻结文件。旧入口的 raw 数据、通用 API、隐藏反馈、普通子进程执行路径不再由命令行调用。旧函数留作历史源码，不是可信评测器。

生成侧仅读取 self-debug 的 `dataset/processed/<view>/generator/`。调试只调用公开检查或固定的模型自生成输入；独立评分程序在轨迹保存后才读取 evaluator。Hidden 分数不参与触发修复、终止、回退和择优。

支持函数数据的公开侧调度；ClassEval、BigCodeBench、LiveCodeBench 的新模型适配器尚未就绪，会在 API 调用前阻断。不能将 loader 能读七个视图解释为七个视图均能实验。

方法：E0–E6 加 TC_public。所有条件共享一份初始代码；E1–E3 共享独立设计的 10 个 JSON 测试。E3 只反馈运行时观察，由模型声明是否正确，其逐行轨迹不是 2025 基本块复现。TC_public 保留 LLM 加打印机制，但限制仅插入打印，并验证原 AST 和公开行为；被拒的插桩不作为有效轨迹。最多修复两轮，保留最后一份有效原代码，隐藏分数不选优。

`--no-two-step-repair` 真正移除 TC_public 的分析调用，且必须与冻结配置一致。模型仅 Go / deepseek-v4.1-flash，温度0，2048输出token，90秒请求截止，重试0，并发1。缺失 usage 保留未知值，不作为零成本。

账本在发出请求前不可覆盖创建，完成后保存脱敏响应、实际 usage、各轮源代码与诊断；未知状态的请求不自动重发。断点恢复配置或提示不同会失败。

## WSL 命令

```bash
PY=/home/xuyang18645796499/.local/share/self-debug/venv-3.12.3/bin/python
DATA=/mnt/d/大三上课程/self-debug
TC=/mnt/d/研究项目/竞品代码/TraceCoder

cd "$DATA"
"$PY" scripts/tracecoder_experiment.py preflight --preflight experiments/tracecoder_v1/preflight.json
# 只有全量参考门槛通过的视图可冻结；HumanEval+复用HumanEval生成。
"$PY" scripts/tracecoder_experiment.py freeze --datasets humaneval \
  --preflight experiments/tracecoder_v1/preflight.json \
  --freeze experiments/tracecoder_v1/he_full.freeze.json \
  --experiment-dir experiments/tracecoder_v1/he_full

cd "$TC"
"$PY" trace_learn_coder.py --data-root "$DATA" \
  --freeze "$DATA/experiments/tracecoder_v1/he_full.freeze.json" \
  --experiment-dir "$DATA/experiments/tracecoder_v1/he_full"
cd "$DATA"
"$PY" scripts/tracecoder_experiment.py score --freeze experiments/tracecoder_v1/he_full.freeze.json
```

必须先在 Linux 环境传入 OPENCODE_API，命令不会加载 `.env`。原 E0 和 v3 冻结保留；新运行单独保存，试点不能冒充全量结果。

Git 基线：上游 HEAD cbd536eb5a02b53ee835cae2f07f5b7ed8bb694d；用户原有六处跟踪修改已用 `refs/codex/baselines/pre-alignment-20261005` 保存可恢复快照，工作树未 reset。快照不包含未跟踪凭证。只提交新路径及入口，不将原有六处修改混入本轮提交。
