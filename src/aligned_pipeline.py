"""Public-only repair scheduler. Hidden scoring is a separate offline job."""
from __future__ import annotations
import json
from pathlib import Path
from .aligned_protocol import (parse_code, parse_generated_tests, persist, same_or_create,
                               instrumentation_guard, sha256, METHODS)
from .aligned_client import GoClient

CODE_SYSTEM = 'Complete the requested Python implementation. Return only complete Python source or one Python code block. Do not use tools or external files.'
SUPPORTED = {'humaneval', 'humaneval_plus', 'mbpp_500', 'mbpp_plus'}


def code_messages(problem, previous=None, diagnostic=None, explanation=None):
    text = problem['prompt']
    if previous is not None:
        text += '\n\nPrevious complete implementation:\n```python\n' + previous + '\n```'
        text += '\n\nAllowed debugging feedback:\n' + (diagnostic or 'No execution feedback.')
        if explanation:
            text += '\n\nAnalysis/repair plan:\n' + explanation
        text += '\nReturn the complete corrected implementation; you may preserve the previous source.'
    return [{'role': 'system', 'content': CODE_SYSTEM}, {'role': 'user', 'content': text}]


def behavior_key(result):
    return {key: result.get(key) for key in ('passed', 'checks_run', 'calls_run', 'failures', 'observations')}


class Pipeline:
    def __init__(self, directory, *, client, debug, format_feedback, limits=None,
                 two_step=True, freeze_sha256=None):
        self.directory = Path(directory)
        self.client, self.debug, self.format_feedback = client, debug, format_feedback
        self.limits, self.two_step, self.freeze_sha256 = limits, two_step, freeze_sha256

    def _code(self, content, problem):
        return parse_code(content, problem['entry_point'], class_entry=problem['dataset'] == 'classeval')

    def run(self, problem, methods):
        if not methods or len(set(methods)) != len(methods) or set(methods) - set(METHODS):
            raise ValueError('InvalidMethods')
        path = self.directory / problem['dataset'] / problem['problem_id'].replace('/', '_')
        context = {'methods': list(methods), 'prompt_hash': problem['prompt_hash'],
                   'public_checks_sha256': sha256(json.dumps(problem['debug_tests'], sort_keys=True)),
                   'limits': self.limits, 'two_step': self.two_step, 'freeze_sha256': self.freeze_sha256,
                   'max_tokens': self.client.max_tokens, 'timeout': self.client.timeout,
                   'repair_max_tokens': self.client.repair_max_tokens, 'reasoning_effort': self.client.reasoning_effort}
        context['client_policy'] = self.client.policy()
        final_path = path / 'trajectory.json'
        if final_path.exists():
            record = json.loads(final_path.read_text('utf-8'))
            if record.get('context') != context:
                raise ValueError('TrajectoryResumeContextMismatch')
            return record
        session = sha256(str(self.directory.resolve()) + ':' + problem['generation_group_id'])[:32]
        calls = self.client.for_directory(path / 'calls')
        record = {'problem_id': problem['problem_id'], 'dataset': problem['dataset'],
                  'generation_group_id': problem['generation_group_id'], 'prompt_hash': problem['prompt_hash'],
                  'methods': {}, 'hidden_feedback': False, 'session_id': session,
                  'context': context, 'status': 'running'}
        if problem['dataset'] not in SUPPORTED:
            record.update(status='adapter_blocked', error_type='AdapterNotImplemented')
            persist(final_path, record)
            return record
        try:
            response = calls.call('initial', code_messages(problem), session)
            initial = self._code(response['content'], problem)
            record['initial_code_sha256'] = sha256(initial)
            (path / 'initial.py').write_text(initial, encoding='utf-8')
        except Exception as error:
            record.update(status='initial_failed', error_type=type(error).__name__)
            persist(final_path, record)
            return record
        suites = None
        if set(methods).intersection({'E1', 'E2', 'E3'}):
            messages = [{'role': 'system', 'content': 'Design exactly ten tests from the specification, without seeing an implementation. Return only a JSON array, no Python code.'},
                        {'role': 'user', 'content': problem['prompt'] + '\nEach case must have exactly args (JSON array), kwargs (JSON object), and expected (JSON value). Use ten distinct valid inputs.'}]
            try:
                response = calls.call('generated_tests', messages, session)
                suites = parse_generated_tests(response['content'])
                same_or_create(path / 'generated_tests.json', suites)
            except Exception as error:
                record['generated_suite_error'] = type(error).__name__
        for method in methods:
            code = initial
            trajectory = {'status': 'completed', 'rounds': [{'round': 0, 'code': code, 'code_sha256': sha256(code)}],
                          'stop_reason': 'no_debug' if method == 'E0' else None,
                          'trace_representation': 'llm-added-print-guarded' if method == 'TC_public' else
                                                   'bounded-line-state' if method == 'E3' else None}
            if method in {'E1', 'E2', 'E3'} and suites is None:
                trajectory.update(status='generated_suite_failed', stop_reason='invalid_generated_suite')
            elif method != 'E0':
                for iteration in range(1, 3):
                    diagnostic = 'No execution was performed. Analyze the specification and source.'
                    try:
                        debug = None
                        if method != 'E6':
                            debug = self.debug(problem, code, cases=suites if method in {'E1','E2','E3'} else None,
                                               trace=method == 'E3', runtime_only=method == 'E3', limits=self.limits)
                            trajectory['rounds'][-1]['debug_result'] = debug
                            if debug.get('status') == 'blocked':
                                trajectory.update(status='adapter_blocked', stop_reason='adapter_not_implemented')
                                break
                            if method != 'E3' and debug['passed']:
                                trajectory['stop_reason'] = 'self_tests_passed' if method in {'E1','E2'} else 'public_tests_passed'
                                break
                            diagnostic = self.format_feedback(debug, 'label' if method == 'E1' else
                                                               'trace' if method == 'E3' else 'details')
                        plan = None
                        if method == 'TC_public':
                            messages = [{'role': 'system', 'content': 'Instrument Python by adding ONLY print statements. Preserve every original statement and signature. Do not add imports, rename variables or change expressions/control flow. Print only constants, variable names or f-strings of variable names, with no keyword arguments. Return complete Python source.'},
                                        {'role': 'user', 'content': problem['prompt'] + '\nOriginal source:\n' + code}]
                            instrumented_response = calls.call(f'{method}_{iteration}_instrument', messages, session)
                            instrumentation = {'response_sha256': sha256(instrumented_response['content']),
                                               'accepted': False, 'equivalence_claim': 'AST preservation plus public behavior check only'}
                            trajectory['rounds'][-1]['instrumentation'] = instrumentation
                            try:
                                instrumented = instrumentation_guard(code, self._code(instrumented_response['content'], problem))
                                instrumented_result = self.debug(problem, instrumented, capture=True, limits=self.limits)
                                instrumentation['code'] = instrumented
                                instrumentation['debug_result'] = instrumented_result
                                if behavior_key(debug) != behavior_key(instrumented_result):
                                    raise ValueError('InstrumentationPublicBehaviorMismatch')
                                instrumentation['accepted'] = True
                                diagnostic += '\nGuarded print observations (untrusted):\n' + instrumented_result.get('trace', '')
                            except (ValueError, SyntaxError) as error:
                                instrumentation['error_type'] = type(error).__name__
                                diagnostic += '\nInstrumentation rejected; no trace supplied.'
                        if method == 'E3':
                            messages = [{'role': 'system', 'content': 'Assess correctness using ONLY the specification, source and runtime observations. No expected outputs or test labels are supplied. Return ONLY JSON with decision (correct or repair) and explanation (string).'},
                                        {'role': 'user', 'content': problem['prompt'] + '\nSource:\n' + code + '\nRuntime:\n' + diagnostic}]
                            decision = json.loads(calls.call(f'{method}_{iteration}_decision', messages, session)['content'])
                            if set(decision) != {'decision', 'explanation'} or decision['decision'] not in {'correct','repair'} or not isinstance(decision['explanation'], str):
                                raise ValueError('InvalidRuntimeDecision')
                            trajectory['rounds'][-1]['model_decision'] = decision
                            if decision['decision'] == 'correct':
                                trajectory['stop_reason'] = 'model_declared_correct'
                                break
                            plan = decision['explanation']
                        elif method in {'E5', 'E6'} or (method == 'TC_public' and self.two_step):
                            analysis = [{'role': 'system', 'content': 'Analyze the code using only the supplied specification and allowed feedback. Give a concise explanation and repair plan, not code.'},
                                        {'role': 'user', 'content': problem['prompt'] + '\nCode:\n' + code + '\nFeedback:\n' + diagnostic}]
                            plan = calls.call(f'{method}_{iteration}_analysis', analysis, session)['content']
                        repaired = calls.call(f'{method}_{iteration}_repair', code_messages(problem, code, diagnostic, plan), session)
                        code = self._code(repaired['content'], problem)
                        trajectory['rounds'].append({'round': iteration, 'code': code, 'code_sha256': sha256(code)})
                    except Exception as error:
                        trajectory.update(status='repair_failed', stop_reason='parse_or_request_failed', error_type=type(error).__name__)
                        break
                if trajectory['stop_reason'] is None:
                    trajectory['stop_reason'] = 'max_iterations'
            trajectory['final_code'] = trajectory['rounds'][-1]['code']
            trajectory['final_code_sha256'] = trajectory['rounds'][-1]['code_sha256']
            record['methods'][method] = trajectory
            persist(path / 'progress.json', record, exclusive=False)
        record['status'] = 'completed'
        persist(final_path, record)
        return record
