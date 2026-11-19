# ClassEval对齐修正开发分支

本分支保留problem_processor.py、所有原智能体提示、5轮上限、patience=2和回退逻辑。

新增公开适配参数max_tokens，默认仍1024；只有ClassEval可以显式选择8192。该参数通过真实传输配置传递，不修改温度或开启thinking。

新ClassEval批次共享类代码块选择规则：优先保留包含指定类的完整围栏块，不用后续单独方法覆盖；无此块时沿用旧提取，不拼接或修补源码。执行端仅使用公开数据，诊断打印与正式未插桩公开评分分离。

ClassEval独立8192-token批次已完成100题及隐藏评分，TraceCoder的实验源码为`72b2594`。共享初始程序通过50题，TraceCoder调试后通过51题。数据、详细审计与七方法比较见[self-debug实验分支](https://github.com/yangxu18645796499/self-debug/tree/codex/selfdebug-four-variants-audit)。
