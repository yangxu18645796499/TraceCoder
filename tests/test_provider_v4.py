import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from src.provider_client_v4 import ProviderClient, ProviderUnavailable
from src.provider_profiles_v4 import OFFICIAL, ProviderProfile
from src.provider_transport_v4 import TransportFailure
from src.aligned_client import RequestFailure
from src.aligned_v3_control import Circuit, CircuitOpen

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'DEEPSEEK_API': 'synthetic-test-key'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.temp.cleanup)

    def client(self, transport):
        return ProviderClient(self.root / 'calls', circuit_path=self.root / 'control.sqlite', transport=transport)

    def good(self, request, timeout):
        self.assertEqual(request.full_url, OFFICIAL.endpoint)
        self.assertNotIn('X-opencode-session', dict(request.header_items()))
        self.assertEqual(timeout, 180)
        body = json.loads(request.data)
        self.assertEqual(body['max_tokens'], 8192)
        self.assertEqual(body['thinking'], {'type': 'enabled'})
        self.assertNotIn('temperature', body)
        return 200, {'model': 'deepseek-flash', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'def f(): return 1'}}], 'usage': {'total_tokens': 12}}, {'response_known': True, 'request_not_sent': False}

    def test_official_payload_and_same_cohort_resume(self):
        client = self.client(self.good)
        first = client.call('initial', [], 'session')
        client.transport = lambda *a: self.fail('resume must not request')
        self.assertEqual(client.call('initial', [], 'session'), first)
        with self.assertRaises(RequestFailure):
            client.call('initial', [{'role': 'user', 'content': 'changed'}], 'session')

    def test_only_proven_pre_send_retries(self):
        calls = []
        def transport(request, timeout):
            calls.append(1)
            if len(calls) < 4:
                raise TransportFailure({'classification': 'confirmed_pre_request_failure', 'request_not_sent': True, 'response_known': False})
            return self.good(request, timeout)
        with patch('src.provider_client_v4.time.sleep'):
            self.client(transport).call('initial', [], 's')
        self.assertEqual(len(calls), 4)

    def test_unknown_deadline_never_resends(self):
        calls = []
        def transport(*args):
            calls.append(1)
            raise TransportFailure({'classification': 'deadline_response_unknown', 'request_not_sent': False, 'response_known': False})
        client = self.client(transport)
        for _ in range(2):
            with self.assertRaises(RequestFailure): client.call('initial', [], 's')
        self.assertEqual(len(calls), 1)

    def test_interrupted_attempt_never_resends(self):
        folder = self.root / 'calls/initial/attempts/0001'
        folder.mkdir(parents=True)
        (folder / 'attempt.json').write_text('{}')
        with self.assertRaises(RequestFailure):
            self.client(lambda *a: self.fail('must not send')).call('initial', [], 's')
        self.assertEqual(json.loads((folder / 'result.json').read_text())['classification'], 'interrupted_response_unknown')

    def test_model_mismatch_no_fallback(self):
        def mismatch(request, timeout):
            status, body, metadata = self.good(request, timeout)
            body['model'] = 'deepseek-v4-pro'
            return status, body, metadata
        with self.assertRaises(RequestFailure): self.client(mismatch).call('initial', [], 's')

    def test_length_preserved_no_regeneration(self):
        def length(request, timeout):
            status, body, metadata = self.good(request, timeout)
            body['choices'][0]['finish_reason'] = 'length'
            return status, body, metadata
        with self.assertRaises(RequestFailure): self.client(length).call('initial', [], 's')
        result = json.loads((self.root / 'calls/initial/result.json').read_text())
        self.assertEqual(result['classification'], 'output_budget_exceeded_response_known')

    def test_auth_failure_globally_stops(self):
        def denied(*args):
            raise TransportFailure({'classification': 'http_response_failure', 'request_not_sent': False, 'response_known': True, 'http_status': 401})
        with self.assertRaises(ProviderUnavailable): self.client(denied).call('initial', [], 's')
        with self.assertRaises(CircuitOpen): Circuit(self.root / 'control.sqlite').check()

    def test_circuit_after_five_failures(self):
        def disconnected(*args):
            raise TransportFailure({'classification': 'transport_response_unknown', 'request_not_sent': False, 'response_known': False})
        client = self.client(disconnected)
        for index in range(5):
            with self.assertRaises(RequestFailure): client.call('call_' + str(index), [], 's')
        with self.assertRaises(CircuitOpen): client.call('next', [], 's')

    def test_unapproved_profile_rejected(self):
        with self.assertRaises(ValueError): ProviderProfile('DeepSeek official', OFFICIAL.endpoint, 'deepseek-v4-pro', 'DEEPSEEK_API')

if __name__ == '__main__': unittest.main()
