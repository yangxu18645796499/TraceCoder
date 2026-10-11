# TraceCoder：原版框架＋本轮公开／隐藏最小改造

本main以官方提交`cbd536eb5a02b53ee835cae2f07f5b7ed8bb694d`为基础，保留本次HumanEval／HumanEval+实验已经验证的最小适配。不使用旧`aligned_*.py`流程或八条件实验。

旧工作在`codex/archive-aligned-experiments-20261011`和仓库外备份；本轮原分支`codex/original-tracecoder-he-v32`保留。此仓库仍独立位于`D:\研究项目\竞品代码\TraceCoder`，不是self-debug里另写一套调试方法。

## 原版到当前：保留与修改清单

| 部分 | 当前处理 |
| --- | --- |
| problem_processor.py、config.py、reporting.py | 与原官方核心保持一致；保留提示、阶段衔接、5次修复、2次无改善、历史和回退 |
| src/postprocessing.py、src/traceRunner.py | 保留原提取和轨迹核心；正式运行将执行I/O接到公开隔离执行器，不在宿主不安全执行 |
| src/generation.py | 保留原提取辅助函数与生成分支；API通过注入transport调用，模型工作者不持有凭证，异常不自动重试 |
| public_humaneval.py | 本轮必要适配：只允许5个公开字段，接公开检查和公开轨迹；没有隐藏评分读入接口 |
| trace_learn_coder.py | 保留原代码，但直接运行被明确阻止，避免旧数据入口向模型暴露隐藏测试 |
| datasets与原加载／评测代码 | 保留原版来源，不作为本轮生成数据入口 |
| 旧aligned代码、专用测试／配置、旧结果和缓存 | 不在main工作目录；已归档，不重新引入 |

只有依赖传输和安全I/O改变，不恢复“必须完整初始源码”的额外解析门槛，也不改变原智能体提示及调试算法。隐藏评测在self-debug中由独立可信进程进行。

## 本轮已完成

固定ChatAnywhere `deepseek-v3.2`、1024输出tokens、thinking关闭、temperature=0.2、串行。164题共享初始代码：

- HumanEval：E0 157/164，TraceCoder 161/164，改对4、改错0。
- fixed-snapshot HumanEval+：146/164，151/164，改对5、改错0。
- 正式已知172,167 tokens，未估算金额；模型进程已经结束，不自动运行。

实际运行入口在self-debug的`scripts/he/he_campaign.py`，免费检查为`scripts/he/he_pipeline.py`及对应tests/he。源码与原实验快照、双评分、数据补齐和完整报告见`D:\大三上课程\self-debug\docs\he_restart`。

免费原版边界检查：

```bash
python -m unittest discover -s tests/he -p 'test_*.py' -v
```

不读取API凭证、不调用模型。下面保留官方原说明，仅供原项目背景参考，不代表本轮安全数据入口；不能据旧教程绕过公开／隐藏隔离。

**TraceCoder** is a Python framework designed for conducting experiments in code generation and automated code repair. It enables the evaluation and enhancement of code generation and debugging techniques using various language models and datasets.

## Key Features

- **Configurable Experiment Workflow**: Easily configure models, datasets, number of debugging attempts, and other parameters via command-line arguments.
- **Multi-Dataset Support**: Built-in support for loading and processing popular code datasets such as `HumanEval`, `BigCodeBench`, and others.
- **Code Generation and Evaluation**: Capable of generating code from prompts and evaluating it against test cases provided by the dataset.
- **Self-Debugging Capability**: Implements a multi-round self-debugging loop where the model analyzes errors, instruments code, and attempts repairs.
- **Flexible Evaluation Mechanism**: Dynamically loads dataset-specific evaluation functions for different benchmarks.
- **Results Reporting**: Experiment results can be saved as CSV files with summary statistics.
- **Modular Design**: Well-structured project layout for easy extension and maintenance.

A framework for code generation and self-debugging experiments, supporting evaluation of code generation quality and validation of automated repair workflows across multiple models and datasets.

---

## Core Functionalities

- **Multi-Dataset Support**: Built-in support for the following datasets:
  - `humaneval`: HumanEval code generation benchmark
  - `humanevalplus`: HumanEval+ enhanced benchmark
  - `bigcodebench`: BigCodeBench large-scale code evaluation dataset
  - `classeval`: ClassEval class-related code benchmark

- **Code Generation and Evaluation**:
  - Integrated model invocation interfaces
  - Automatic loading of dataset-specific evaluation modules to verify code correctness

- **Self-Debugging Workflow**:
  - Three-step repair mechanism: Code Instrumentation → Error Analysis → Code Repair
  - Configurable parameters such as maximum debugging attempts (`--max-debug-attempts`) and improvement thresholds (`--max-no-improvement-streak`)

- **Execution Safety Measures**:
  - Code sandboxing to restrict dangerous operations (filesystem access, system calls, etc.)
  - Execution timeout control (default 10 seconds, adjustable via `--timeout`)

- **Results Logging**:
  - Automatic generation of CSV reports containing generated code, evaluation results, and debugging logs

---

## Experimental Results

**Performance comparison (Pass@1, %) of TraceCoder against baseline methods on four benchmarks across different foundation models. "Ours" refers to our proposed TraceCoder. The best result in each setting is marked in bold. The value in parentheses indicates the absolute improvement (↑) over the second-best method.**

| Models | Methods | Humaneval | Humanevalplus | ClassEval | BigCodeBench-Complete | BigCodeBench-Instruct |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| Gemini-2.5-Flash-0417 | Direct | 96.34 | 91.46 | 38.00 | 53.77 | 43.77 |
| | CoT | 93.90 | 91.46 | 41.00 | 53.86 | 43.68 |
| | Self-Planning | 94.51 | 90.85 | 36.00 | 55.61 | 43.15 |
| | Self-Debugging | 98.78 | 96.34 | 61.00 | 78.07 | 71.05 |
| | INTERVENOR | **99.39 (↑ 0.61)** | 95.12 | 61.00 | 75.88 | 69.82 |
| | **Ours** | **99.39 (↑ 0.61)** | **98.17 (↑ 1.83)** | **81.00 (↑ 20.00)** | **89.04 (↑ 10.97)** | **85.00 (↑ 13.95)** |
| DeepSeek-V3-0324 | Direct | 94.51 | 90.24 | 41.00 | 38.25 | 46.67 |
| | CoT | 93.29 | 88.41 | 41.00 | 60.35 | 47.98 |
| | Self-Planning | 95.12 | 90.24 | 37.00 | 61.14 | 26.93 |
| | Self-Debugging | **98.78 (↑ 3.05)** | **96.34 (↑ 3.66)** | 61.00 | 82.37 | 74.56 |
| | INTERVENOR | 95.73 | 92.68 | 63.00 | 79.82 | 70.79 |
| | **Ours** | **98.78 (↑ 3.05)** | **96.34 (↑ 3.66)** | **78.00 (↑ 15.00)** | **88.33 (↑ 5.96)** | **83.77 (↑ 9.21)** |
| Qwen-Plus-2025-01-25 | Direct | 90.85 | 86.59 | 31.00 | 50.09 | 41.49 |
| | CoT | 93.29 | 87.19 | 33.00 | 48.07 | 43.50 |
| | Self-Planning | 90.85 | 84.75 | 37.00 | 37.36 | 41.75 |
| | Self-Debugging | **96.34 (↑ 1.22)** | **93.90 (↑ 2.44)** | 49.00 | 70.96 | 63.77 |
| | INTERVENOR | 95.12 | 91.46 | 48.00 | 68.60 | 61.75 |
| | **Ours** | **96.34 (↑ 1.22)** | **93.90 (↑ 2.44)** | **63.00 (↑ 14.00)** | **71.93 (↑ 0.97)** | **68.60 (↑ 4.83)** |

---

## Directory Structure

```plaintext
TraceCoder/
├── config.py               # Global configuration
├── trace_learn_coder.py    # Main execution entry point
├── problem_processor.py    # Core problem processing logic
├── reporting.py            # Results saving and summarization
├── src/                    # Utility modules
│   ├── dataset_loader.py   # Dataset loading
│   ├── generation.py       # Model invocation and generation logic
│   ├── traceRunner.py      # Code execution and output capture
│   └── postprocessing.py   # Code post-processing
└── datasets/               # Dataset-specific evaluation modules
    ├── human_eval/         # HumanEval execution logic
    ├── human_eval_plus/    # HumanEval+ execution logic
    ├── BigCodeBench/       # BigCodeBench evaluation logic
    └── ClassEval/          # ClassEval evaluation logic
```

## Quick Start

### 1. Environment Setup
```bash
# Install base dependencies
pip install -r requirements.txt

# For evaluating with the bigcodebench dataset, install additional dependencies
pip install -r requirements-eval.txt
```

### 2. Configure Dataset Paths

Modify the DATASET_PATHS section in config.py to point to your local dataset storage locations:
```python
# config.py
DATASET_PATHS = {
    "humaneval": {
        "data_path": "/your/local/path/human_eval/data/test.jsonl",  # Local HumanEval test set path (JSONL)
        "eval_module": "datasets.human_eval.execution"
    },
    "humanevalplus": {
        "data_path": "/your/local/path/human_eval_plus/data/test.parquet",  # Local HumanEval+ test set path (Parquet)
        "eval_module": "datasets.human_eval.execution"
    },
    "bigcodebench": {
        "data_path": "/your/local/path/BigCodeBench/data/v0.1.4.parquet",  # Local BigCodeBench test set path (Parquet)
        "eval_module": "datasets.BigCodeBench.evaluation"
    },
    "classeval": {
        "data_path": "/your/local/path/ClassEval/data/test.parquet",  # Local ClassEval test set path (Parquet)
        "eval_module": "datasets.ClassEval.evaluation"
    }
}
```

### 3. Configure API Keys

Modify the OpenAI configuration in src/generation.py:
```python
# --- OpenAI/Compatible API Client Configuration ---
client = OpenAI(
    api_key="your_api_key_here", 
    base_url="your_base_url_here" 
)
```

### 4. Run Experiments

```bash
# Basic run (using default model and HumanEval dataset)
python trace_learn_coder.py -m gemini-2.5-flash -d humaneval

# Advanced parameter example (disable instrumentation, limit problems, customize timeout)
python trace_learn_coder.py \
    -m your_custom_model \          # Replace with actual model name
    -d bigcodebench \               # Specify BigCodeBench dataset
    --max-problems 50 \             # Process up to 50 problems
    --timeout 15 \                  # Adjust code execution timeout to 15 seconds
    --output-file ./results/my_exp.csv  # Custom results output path
```

---

## 📚 Citation

If you use TraceCoder in your research, please cite our ICSE 2026 paper:

### ACM Reference Format
Jiangping Huang, Wenguang Ye, Weisong Sun, Jian Zhang, Mingyue Zhang,  
and Yang Liu. 2026. **TraceCoder: A Trace-Driven Multi-Agent Framework  
for Automated Debugging of LLM-Generated Code.**  
In *48th International Conference on Software Engineering (ICSE ’26)*,  
April 12–18, 2026, Rio de Janeiro, Brazil. ACM, New York, NY, USA, 13 pages.

### BibTeX
```bibtex
@inproceedings{huang2026tracecoder,
  author       = {Jiangping Huang and Wenguang Ye and Weisong Sun 
                  and Jian Zhang and Mingyue Zhang and Yang Liu},
  title        = {TraceCoder: A Trace-Driven Multi-Agent Framework 
                  for Automated Debugging of LLM-Generated Code},
  booktitle    = {Proceedings of the 48th International Conference on 
                  Software Engineering (ICSE ’26)},
  year         = {2026},
  month        = apr,
  address      = {Rio de Janeiro, Brazil},
  publisher    = {ACM},
  pages        = {1--13}
}
