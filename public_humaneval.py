"""Original processor, public-only I/O. The algorithm and prompt builders stay upstream."""
from types import SimpleNamespace
from src.generation import configure_transport

PUBLIC_FIELDS = {"task_id", "entry_point", "prompt", "complete_prompt", "test"}


def process_public_problem(problem, transport, public_checker, public_trace):
    if set(problem) != PUBLIC_FIELDS:
        raise ValueError("Only the five public adapter fields are permitted")
    configure_transport(transport)
    import problem_processor
    # Replace execution I/O, not instrumentation, analysis, repair or rollback.
    problem_processor.execute_code_and_capture_prints_last = public_trace
    args = SimpleNamespace(model="deepseek-v3.2", dataset="humaneval",
        timeout=10, max_debug_attempts=5, max_no_improvement_streak=2,
        no_instrumentation=False, no_two_step_repair=False)
    return problem_processor.process_problem(problem, problem["task_id"], args, public_checker)
