import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.aligned_client import RequestFailure, MODEL
from src.aligned_protocol import persist, sha256
from src.aligned_v3_client import V3Client
from src.aligned_v3_control import Circuit, CircuitOpen, APIWindowClosed
from src.aligned_v3_transport import TransportFailure


class V3ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.new = self.base / 'new'
        self.old = self.base / 'old'
        self.key = patch.dict(os.environ, {'OPENCODE_API': 'v3-test-not-real-secret'})
        self.key.start()
        self.hashes, self.calls = {}, []
    def tearDown(self):
        self.key.stop()
        self.temp.cleanup()
    def transport(self, request, deadline):
        self.calls.append((json.loads(request.data), deadline))
        return 200, {'model': MODEL, 'choices': [{'finish_reason': 'stop', 'message': {'content': 'def f(x): return x'}}],
                     'usage': {'prompt_tokens': 1, 'completion_tokens': 2, 'total_tokens': 3}}, {'response_known': True}
    def client(self, **kwargs):
        return V3Client(self.new / 'd/p/calls', root=self.new, prior_root=self.old,
                        prior_hashes=self.hashes, circuit_path=self.base / 'control.sqlite',
                        transport=kwargs.pop('transport', self.transport), **kwargs)
    def old_result(self, status='ok', **fields):
        client = self.client()
        fingerprint = sha256(json.dumps(client.payload([], 'initial'), sort_keys=True))
        result = {'status': status, 'request_sha256': fingerprint, 'returned_model': MODEL,
                  'finish_reason': 'stop', 'content': 'def f(x): return x', 'usage': {'total_tokens': 10}}
        result.update(fields)
        path = self.old / 'd/p/calls/initial/result.json'
        persist(path, result)
        self.hashes[str(path.relative_to(self.old))] = sha256(path.read_bytes())
    def test_reuses_only_exact_known_frozen_response(self):
        self.old_result()
        result = self.client().call('initial', [], 'session')
        self.assertEqual(result['origin'], 'v2_success_reused')
        self.assertEqual(self.calls, [])
        self.assertFalse(result['usage_billed_in_this_run'])
    def test_old_urlerror_needs_explicit_authorization(self):
        self.old_result('api_failed', finish_reason=None, transport_error_type='URLError')
        with self.assertRaises(RequestFailure):
            self.client().call('initial', [], 'session')
        self.assertEqual(self.calls, [])
    def test_authorized_legacy_urlerror_preserves_old_and_adds_attempt(self):
        self.old_result('api_failed', finish_reason=None, transport_error_type='URLError')
        original = (self.old / 'd/p/calls/initial/result.json').read_bytes()
        result = self.client(allow_legacy_urlerror=True).call('initial', [], 'session')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.new / 'd/p/calls/initial/attempts/0001/attempt.json').exists())
        self.assertEqual((self.old / 'd/p/calls/initial/result.json').read_bytes(), original)
    def test_old_deadline_is_never_resent_even_with_urlerror_authorization(self):
        self.old_result('api_failed', finish_reason=None, error_status='RequestDeadlineExceeded')
        with self.assertRaises(RequestFailure):
            self.client(allow_legacy_urlerror=True).call('initial', [], 'session')
        self.assertEqual(self.calls, [])
    def test_new_confirmed_pre_send_failure_retries_as_separate_attempts(self):
        numbers = []
        def transport(request, deadline):
            numbers.append(1)
            if len(numbers) < 3:
                raise TransportFailure({'classification': 'confirmed_pre_request_failure', 'request_not_sent': True})
            return self.transport(request, deadline)
        with patch('src.aligned_v3_client.time.sleep'):
            result = self.client(transport=transport).call('initial', [], 'session')
        self.assertEqual(result['actual_http_attempts_new'], 3)
        self.assertEqual(len(list((self.new / 'd/p/calls/initial/attempts').glob('*/result.json'))), 3)
    def test_new_unknown_does_not_retry(self):
        calls = []
        def transport(request, deadline):
            calls.append(1)
            raise TransportFailure({'classification': 'transport_response_unknown', 'request_not_sent': False})
        with self.assertRaises(RequestFailure):
            self.client(transport=transport).call('initial', [], 'session')
        self.assertEqual(len(calls), 1)
    def test_worker_failure_counts_toward_circuit_without_retry(self):
        def transport(request, deadline):
            raise RuntimeError('worker failure before evidence')
        client = self.client(transport=transport)
        for number in range(5):
            with self.assertRaises(RequestFailure):
                client.call('failed_' + str(number), [], 's')
        with self.assertRaises(CircuitOpen):
            client.call('sixth', [], 's')
        self.assertFalse((self.new / 'd/p/calls/sixth/attempts/0001/attempt.json').exists())
    def test_phase_budgets_and_no_model_fallback(self):
        client = self.client()
        client.call('initial', [], 's')
        client.call('E6_1_analysis', [], 's')
        self.assertEqual([r[1] for r in self.calls], [90, 180])
        self.assertEqual([r[0]['max_tokens'] for r in self.calls], [2048, 8192])
        self.assertTrue(all(r[0]['model'] == MODEL and r[0]['reasoning_effort'] == 'low' for r in self.calls))
    def test_interrupted_new_attempt_never_resends(self):
        path = self.new / 'd/p/calls/initial/attempts/0001/attempt.json'
        persist(path, {'pending': True})
        with self.assertRaises(RequestFailure):
            self.client().call('initial', [], 's')
        self.assertEqual(self.calls, [])
    def test_shared_circuit_latches_and_does_not_burn_next_task(self):
        first = Circuit(self.base / 'control.sqlite')
        second = Circuit(self.base / 'control.sqlite')
        for _ in range(5):
            first.note(False, 'transport_failed')
        with self.assertRaises(CircuitOpen):
            second.check()
        second.note(True, 'late_success')
        with self.assertRaises(CircuitOpen):
            first.check()
        with self.assertRaises(CircuitOpen):
            self.client().call('initial', [], 's')
        self.assertFalse((self.new / 'd/p/calls/initial/attempts/0001/attempt.json').exists())
    def test_cutoff_prevents_new_network_call(self):
        with self.assertRaises(APIWindowClosed):
            self.client(cutoff=0).call('initial', [], 's')
        self.assertEqual(self.calls, [])
    def test_prior_hash_drift_is_rejected(self):
        self.old_result()
        (self.old / 'd/p/calls/initial/result.json').write_text('{}')
        with self.assertRaises(RequestFailure):
            self.client().call('initial', [], 's')
        self.assertEqual(self.calls, [])
    def test_truncation_is_known_budget_failure_not_transport_retry(self):
        def transport(request, deadline):
            status, body, network = self.transport(request, deadline)
            body['choices'][0]['finish_reason'] = 'length'
            return status, body, network
        with self.assertRaises(RequestFailure):
            self.client(transport=transport).call('initial', [], 's')
        result = json.loads((self.new / 'd/p/calls/initial/result.json').read_text())
        self.assertEqual(result['classification'], 'output_budget_exceeded_response_known')
        self.assertEqual(len(self.calls), 1)
