import fcntl
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path('/mnt/d/大三上课程/self-debug')
sys.path.insert(0, str(ROOT / 'scripts'))
from typed_generated_tests_v4 import VERSION, parse
from aligned_debug import evaluate_debug, feedback
from src.typed_self_test_profile_v4 import TypedClient, TypedPipeline
from src.aligned_pipeline import Pipeline
from src.aligned_protocol import parse_generated_tests


class FakeClient:
    max_tokens, repair_max_tokens, timeout, reasoning_effort = 2048, 8192, 90, 'low'

    def __init__(self):
        self.calls = []

    def policy(self):
        return {'kind': 'fixture_no_api'}

    def for_directory(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        return self

    def call(self, call_id, messages, session):
        self.calls.append((call_id, messages))
        if call_id == 'initial':
            return {'content': 'def pair(x): return (x,x+1)\n'}
        if call_id == 'generated_tests':
            return {'content': json.dumps({'schema_version': VERSION, 'cases': [
                {'args': [x], 'kwargs': {}, 'expected': {'$tuple': [x, x+1]}} for x in range(10)]})}
        raise AssertionError('Correct typed self-tests should not trigger repair')


class TypedProfileTests(unittest.TestCase):
    def test_client_policy_and_template_are_explicit(self):
        client = TypedClient(FakeClient(), 'decoder-hash')
        self.assertEqual(client.policy()['self_test_protocol'], VERSION)
        with self.assertRaises(ValueError):
            client.call('generated_tests', [{'content': 'changed'}], 's')

    def test_ordinary_pipeline_parser_not_mutated(self):
        self.assertIs(Pipeline.run.__globals__['parse_generated_tests'], parse_generated_tests)
        raw = [{'args': [i], 'kwargs': {}, 'expected': [i]} for i in range(10)]
        self.assertEqual(parse_generated_tests(json.dumps(raw)), raw)

    @unittest.skipUnless(sys.platform == 'linux', 'WSL sandbox required')
    def test_full_e0_e1_e2_scheduler_and_wire_persistence(self):
        prompt = 'Return a tuple (x,x+1).\ndef pair(x):\n    pass\n'
        problem = {'dataset': 'humaneval', 'problem_id': 'Synthetic/tuple', 'entry_point': 'pair',
                   'prompt': prompt, 'prompt_hash': hashlib.sha256(prompt.encode()).hexdigest(),
                   'generation_group_id': 'synthetic-tuple', 'debug_tests': []}
        client = FakeClient()
        lock = Path.home() / '.local/share/self-debug/run-control/candidate.lock'
        with tempfile.TemporaryDirectory() as directory, lock.open('a') as guard:
            fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
            pipeline = TypedPipeline(directory, client=client, debug=evaluate_debug,
                                     format_feedback=feedback, decode_document=parse,
                                     decoder_sha256=hashlib.sha256((ROOT / 'scripts/typed_generated_tests_v4.py').read_bytes()).hexdigest(),
                                     freeze_sha256='synthetic-fixture-no-formal-api')
            record = pipeline.run(problem, ['E0', 'E1', 'E2'])
            self.assertEqual(record['status'], 'completed', record)
            self.assertEqual([name for name, _ in client.calls], ['initial', 'generated_tests'])
            for method in ('E1', 'E2'):
                self.assertEqual(record['methods'][method]['stop_reason'], 'self_tests_passed')
                self.assertTrue(record['methods'][method]['rounds'][0]['debug_result']['passed'])
            stored = json.loads((Path(directory) / 'humaneval/Synthetic_tuple/generated_tests.json').read_text())
            self.assertEqual(stored[0]['expected'], {'$tuple': [0, 1]})
            self.assertIs(Pipeline.run.__globals__['parse_generated_tests'], parse_generated_tests)
