"""Independent provider cohort, durable attempts, no legacy response reuse."""
import json
import os
from pathlib import Path
import time
import urllib.request
from .aligned_client import RequestFailure, utc
from .aligned_protocol import persist, sha256
from .aligned_v3_control import Circuit
from .provider_profiles_v4 import OFFICIAL
from .provider_transport_v4 import http_once_profile, TransportFailure

class ProviderUnavailable(BaseException):
    """Stop dispatch; authentication, rate or endpoint failures are not model failures."""

class ProviderClient:
    max_tokens = repair_max_tokens = 8192
    timeout = 180
    reasoning_effort = 'low'

    def __init__(self, directory, *, circuit_path, profile=OFFICIAL, transport=http_once_profile):
        self.directory, self.circuit_path = Path(directory), Path(circuit_path)
        self.profile, self.transport = profile, transport

    def for_directory(self, directory):
        return type(self)(directory, circuit_path=self.circuit_path, profile=self.profile, transport=self.transport)

    def policy(self):
        return {'version': 'official-independent-v4', 'provider': self.profile.document(),
                'max_tokens_all_stages': self.max_tokens, 'timeout_all_stages': self.timeout,
                'thinking': 'enabled', 'reasoning_effort': self.reasoning_effort,
                'temperature': 'omitted; ineffective in thinking mode', 'stream': False,
                'max_additional_confirmed_pre_request_attempts': 3,
                'unknown_response_resend': False, 'prior_cohort_response_reuse': False}

    def payload(self, messages, call_id='initial'):
        return {'model': self.profile.model, 'messages': messages,
                'max_tokens': self.max_tokens, 'stream': False,
                'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'}

    @staticmethod
    def accept(result):
        if result.get('http_status') in (401, 402, 403, 404, 429):
            raise ProviderUnavailable('ProviderHTTP:' + str(result['http_status']))
        if result['status'] != 'ok':
            raise RequestFailure(result.get('classification', 'RequestFailed'))
        return result

    def call(self, call_id, messages, session_id):
        if not call_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in call_id):
            raise ValueError('UnsafeCallId')
        payload = self.payload(messages, call_id)
        fingerprint = sha256(json.dumps({'provider': self.profile.document(), 'payload': payload}, sort_keys=True))
        folder = self.directory / call_id
        folder.mkdir(parents=True, exist_ok=True)
        final = folder / 'result.json'
        if final.exists():
            result = json.loads(final.read_text('utf-8'))
            if result.get('request_sha256') != fingerprint:
                raise RequestFailure('ResumeProviderOrRequestMismatch')
            return self.accept(result)
        circuit = Circuit(self.circuit_path)
        for number in range(1, 5):
            location = folder / 'attempts' / f'{number:04d}'
            saved = location / 'result.json'
            if saved.exists():
                result = json.loads(saved.read_text('utf-8'))
                if result.get('request_sha256') != fingerprint:
                    raise RequestFailure('AttemptResumeMismatch')
            elif (location / 'attempt.json').exists():
                result = {'status': 'api_failed', 'request_sha256': fingerprint,
                          'classification': 'interrupted_response_unknown', 'usage': None,
                          'origin': 'official_v4', 'number': number, 'actual_http_attempts_new': number}
                persist(saved, result)
            else:
                circuit.check()
                secret = os.environ.get(self.profile.key_variable, '').strip()
                if not secret:
                    raise ProviderUnavailable('CredentialUnavailable')
                persist(location / 'attempt.json', {'started_utc': utc(), 'number': number,
                        'request_sha256': fingerprint, 'provider': self.profile.document(), 'timeout': self.timeout,
                        'session_id': session_id})
                persist(location / 'request.json', payload)
                headers = {'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json',
                           'User-Agent': 'tracecoder-official/4'}
                if self.profile.channel == 'OpenCode Go':
                    headers['x-opencode-session'] = session_id
                request = urllib.request.Request(self.profile.endpoint, data=json.dumps(payload).encode(), headers=headers)
                started = time.monotonic()
                result = {'status': 'api_failed', 'request_sha256': fingerprint, 'origin': 'official_v4',
                          'provider': self.profile.document(), 'requested_model': self.profile.model,
                          'usage': None, 'number': number, 'actual_http_attempts_new': number,
                          'usage_billed_in_this_run': True, 'started_utc': utc()}
                try:
                    status, body, network = self.transport(request, self.timeout)
                    body = json.loads(json.dumps(body).replace(secret, '[REDACTED]'))
                    persist(location / 'response.json', body)
                    result.update(http_status=status, network=network, returned_model=body.get('model'), usage=body.get('usage'))
                    circuit.note(True, 'http_response_received')
                    if status != 200 or body.get('model') != self.profile.model or len(body.get('choices', [])) != 1:
                        raise RequestFailure('ProviderOrChoiceMismatch')
                    choice = body['choices'][0]
                    result['finish_reason'] = choice.get('finish_reason')
                    if choice.get('finish_reason') != 'stop':
                        result['classification'] = ('output_budget_exceeded_response_known' if choice.get('finish_reason') == 'length' else 'non_stop_response_known')
                        raise RequestFailure('NonStopResponse')
                    content = choice.get('message', {}).get('content')
                    if not isinstance(content, str) or not content.strip():
                        raise RequestFailure('EmptyResponse')
                    result.update(status='ok', content=content, classification='response_known_ok')
                except TransportFailure as error:
                    result.update(network=error.metadata, classification=error.metadata['classification'],
                                  http_status=error.metadata.get('http_status'), error_type='TransportFailure')
                    circuit.note(error.metadata.get('response_known') is True, result['classification'])
                except Exception as error:
                    result.update(error_type=type(error).__name__)
                    result.setdefault('classification', 'response_protocol_failure_known' if result.get('network', {}).get('response_known') else 'worker_response_unknown')
                    if 'network' not in result:
                        circuit.note(False, result['classification'])
                result.update(seconds=round(time.monotonic() - started, 4), finished_utc=utc())
                persist(saved, result)
            retry = (number < 4 and result.get('classification') == 'confirmed_pre_request_failure'
                     and result.get('network', {}).get('request_not_sent') is True)
            if not retry:
                persist(final, result)
                if result.get('http_status') in (401, 402, 403, 404, 429):
                    with circuit.connection() as database:
                        database.execute('UPDATE circuit SET opened=1 WHERE id=1')
                return self.accept(result)
            circuit.check()
            time.sleep(min(2 ** number, 8))
        raise RequestFailure('AttemptLimitExceeded')
