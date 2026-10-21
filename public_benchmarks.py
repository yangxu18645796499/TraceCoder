"""Dataset I/O only. Original prompts, processor, repairs and rollback unchanged.

The HE adapter is deliberately not changed. Stdin extraction is the sole
dataset-specific extraction exception, approved before this experiment.
"""
import re
from types import SimpleNamespace
from unittest.mock import patch
from src.generation import configure_transport

PUBLIC_FIELDS = {'task_id','entry_point','prompt','complete_prompt','test'}
DATASETS = {'mbpp_500','mbpp_plus','livecodebench_450','classeval'}


def extract_stdin_code(text):
    # Keep the original last-Python-block preference, but scripts need not
    # contain def/class and their __main__ block is part of the answer.
    matches = re.findall(r'```(?:python|python3|py)\s*\n([\s\S]*?)```',text,re.I)
    return matches[-1].strip() if matches else text


def process_public_benchmark(problem, transport, public_checker, public_trace,
                             *, dataset, stdin=False):
    if set(problem) != PUBLIC_FIELDS or dataset not in DATASETS:
        raise ValueError('Only the five public fields and approved dataset are permitted')
    if stdin and dataset != 'livecodebench_450': raise ValueError('Stdin adapter is LCB-only')
    configure_transport(transport)
    import problem_processor
    args = SimpleNamespace(model='deepseek-flash',dataset=dataset,timeout=10,
        max_debug_attempts=5,max_no_improvement_streak=2,
        no_instrumentation=False,no_two_step_repair=False)
    with patch.object(problem_processor,'execute_code_and_capture_prints_last',public_trace):
        if stdin:
            with patch('src.generation.extract_python_code',extract_stdin_code):
                return problem_processor.process_problem(problem,problem['task_id'],args,public_checker)
        return problem_processor.process_problem(problem,problem['task_id'],args,public_checker)
