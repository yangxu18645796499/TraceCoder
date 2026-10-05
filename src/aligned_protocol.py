"""Strict, data-only experiment protocol. No model code executes here."""
from __future__ import annotations
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

DATASETS = ('humaneval', 'humaneval_plus', 'mbpp_500', 'mbpp_plus',
            'livecodebench_450', 'classeval', 'bigcodebench_complete')
METHODS = ('E0', 'E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'TC_public')
FORBIDDEN_FIELDS = {'test', 'tests', 'hidden', 'hidden_tests', 'canonical_solution',
                    'reference_code', 'solution_code', 'plus_input', 'base_input'}


def sha256(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def read_rows(path):
    rows, seen = [], set()
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            identity = row.get('problem_id')
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError('Missing/duplicate problem_id')
            seen.add(identity)
            rows.append(row)
    return rows


def load_public(root, dataset):
    """Never open raw, evaluator, manifest or any reference field."""
    if dataset not in DATASETS:
        raise ValueError('Unknown dataset')
    base = Path(root).resolve() / 'dataset' / 'processed' / dataset / 'generator'
    questions = read_rows(base / 'problems.jsonl')
    checks = {r['problem_id']: r['tests'] for r in read_rows(base / 'public_tests.jsonl')}
    output = []
    for row in questions:
        if FORBIDDEN_FIELDS.intersection(row):
            raise ValueError('Evaluator field in generator record')
        if not isinstance(row.get('prompt'), str) or not row['prompt'].strip():
            raise ValueError('Invalid prompt')
        if row.get('prompt_hash') != sha256(row['prompt']):
            raise ValueError('Prompt hash mismatch')
        tests = checks.get(row['problem_id'])
        if not isinstance(tests, list) or not tests:
            raise ValueError('Missing public checks')
        if dataset != 'livecodebench_450' and not isinstance(row.get('entry_point'), str):
            raise ValueError('Missing entry point')
        output.append(dict(row, dataset=dataset, debug_tests=tests))
    if set(checks) != {r['problem_id'] for r in questions}:
        raise ValueError('Question/check ID mismatch')
    return output


def parse_code(content, entry=None, class_entry=False):
    if not isinstance(content, str) or not content.strip():
        raise ValueError('EmptySource')
    code = content.strip()
    if '```' in code:
        match = re.fullmatch(r'```python[ \t]*\r?\n(.*?)\r?\n```', code, re.S)
        if not match:
            raise ValueError('NotOneCompletePythonBlock')
        code = match.group(1)
    tree = ast.parse(code)
    if entry:
        allowed = ast.ClassDef if class_entry else ast.FunctionDef
        if not any(isinstance(n, allowed) and n.name == entry for n in tree.body):
            # LCB Solution methods are defined inside Solution.
            if not any(isinstance(n, ast.ClassDef) and n.name == 'Solution' and
                       any(isinstance(m, ast.FunctionDef) and m.name == entry for m in n.body)
                       for n in tree.body):
                raise ValueError('MissingEntryDefinition')
    return code


def parse_generated_tests(content, count=10):
    """No eval, no Python assertions, no reference-based correction."""
    if not isinstance(content, str):
        raise ValueError('MissingGeneratedTests')
    text = content.strip()
    if text.startswith('```'):
        match = re.fullmatch(r'```json[ \t]*\r?\n(.*?)\r?\n```', text, re.S)
        if not match:
            raise ValueError('NotOneJSONBlock')
        text = match.group(1)
    rows = json.loads(text)
    if type(rows) is not list or len(rows) != count:
        raise ValueError('GeneratedSuiteMustContainExactlyTenCases')
    seen = set()
    for row in rows:
        if type(row) is not dict or set(row) != {'args', 'kwargs', 'expected'}:
            raise ValueError('InvalidGeneratedCaseFields')
        if type(row['args']) is not list or type(row['kwargs']) is not dict:
            raise ValueError('InvalidGeneratedArguments')
        key = json.dumps([row['args'], row['kwargs']], sort_keys=True, allow_nan=False)
        if key in seen:
            raise ValueError('DuplicateGeneratedInput')
        seen.add(key)
    return rows


def persist(path, data, *, exclusive=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.pending-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(temporary, path)  # atomic create, never replace an attempt
        else:
            os.replace(temporary, path)
        if os.name == 'posix':
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def same_or_create(path, data):
    path = Path(path)
    if path.exists():
        if json.loads(path.read_text('utf-8')) != data:
            raise ValueError('CheckpointMismatch')
    else:
        persist(path, data)


def instrumentation_guard(original, instrumented):
    """Only added print statements; every original AST node must remain in order.

    This structural check is NOT a proof of arbitrary-program equivalence.
    Public outputs must additionally match before an instrumented trace is used.
    """
    def print_only(node):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == 'print'):
            return False
        call = node.value
        if call.keywords or not call.args:
            return False
        def safe(value):
            return (isinstance(value, (ast.Name, ast.Constant)) or
                    isinstance(value, ast.JoinedStr) and all(
                        isinstance(v, ast.Constant) or isinstance(v, ast.FormattedValue)
                        and isinstance(v.value, ast.Name) and v.format_spec is None for v in value.values))
        return all(safe(v) for v in call.args)
    def equivalent(left, right):
        if type(left) is not type(right):
            return False
        if isinstance(left, ast.AST):
            return all(equivalent(getattr(left, field), getattr(right, field)) for field in left._fields)
        if isinstance(left, list):
            if left and all(isinstance(v, ast.stmt) for v in left):
                i = 0
                for node in right:
                    if i < len(left) and equivalent(left[i], node):
                        i += 1
                    elif not print_only(node):
                        return False
                return i == len(left)
            return len(left) == len(right) and all(equivalent(a, b) for a, b in zip(left, right))
        return left == right
    if not equivalent(ast.parse(original), ast.parse(instrumented)):
        raise ValueError('InstrumentationChangesOriginalAST')
    return instrumented

