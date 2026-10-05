"""Versioned attempts, conservative retry evidence, explicit v2 response reuse."""
import json
import os
from pathlib import Path
import time
import urllib.request
from .aligned_client import GoClient, RequestFailure, ENDPOINT, MODEL, utc
from .aligned_protocol import persist, sha256
from .aligned_v3_control import Circuit, api_window_check, APIWindowClosed
from .aligned_v3_transport import http_once_v3, TransportFailure


def response_classification(result):
    if result['status'] == 'ok':
        return 'response_known_ok'
    if result.get('finish_reason') == 'length':
        return 'output_budget_exceeded_response_known'
    if result.get('finish_reason') is not None:
        return 'non_stop_response_known'
    status = result.get('error_status', '')
    error = result.get('transport_error_type', result.get('error_type', ''))
    if 'RequestDeadlineExceeded' in status:
        return 'deadline_response_unknown'
    if error == 'URLError' or 'URLError' in status:
        return 'urlerror_phase_not_recorded_response_unknown'
    if error == 'RemoteDisconnected' or 'RemoteDisconnected' in status:
        return 'disconnect_response_unknown'
    return 'old_failure_not_retried'


class V3Client(GoClient):
    def __init__(self, directory, *, root, prior_root, prior_hashes, circuit_path,
                 initial_timeout=90, other_timeout=180, max_pre_request_retries=3,
                 allow_legacy_urlerror=False, cutoff=None, transport=http_once_v3,
                 max_tokens=2048, timeout=90, repair_max_tokens=8192, reasoning_effort='low'):
        super().__init__(directory, transport=transport, max_tokens=max_tokens, timeout=initial_timeout,
                         repair_max_tokens=repair_max_tokens, reasoning_effort=reasoning_effort)
        self.root, self.prior_root = Path(root), Path(prior_root)
        self.prior_hashes, self.circuit_path = prior_hashes, Path(circuit_path)
        self.other_timeout, self.max_retries = other_timeout, max_pre_request_retries
        self.allow_legacy_urlerror, self.cutoff = allow_legacy_urlerror, cutoff

    def for_directory(self, directory):
        return V3Client(directory, root=self.root, prior_root=self.prior_root, prior_hashes=self.prior_hashes,
                        circuit_path=self.circuit_path, initial_timeout=self.timeout,
                        other_timeout=self.other_timeout, max_pre_request_retries=self.max_retries,
                        allow_legacy_urlerror=self.allow_legacy_urlerror, cutoff=self.cutoff,
                        transport=self.transport, max_tokens=self.max_tokens,
                        repair_max_tokens=self.repair_max_tokens, reasoning_effort=self.reasoning_effort)

    def policy(self):
        return {'version': 'v3-observed-send-boundary', 'initial_timeout': self.timeout,
                'other_timeout': self.other_timeout, 'max_confirmed_pre_request_retries': self.max_retries,
                'legacy_urlerror_recovery_explicitly_authorized': self.allow_legacy_urlerror,
                'old_deadline_disconnect_not_resent': True}

    def call(self, call_id, messages, session_id):
        if not call_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in call_id):
            raise ValueError('UnsafeCallId')
        payload = self.payload(messages, call_id)
        fingerprint = sha256(json.dumps(payload, sort_keys=True))
        directory = self.directory / call_id
        directory.mkdir(parents=True, exist_ok=True)
        final = directory / 'result.json'
        if final.exists():
            result = json.loads(final.read_text('utf-8'))
            if result['request_sha256'] != fingerprint:
                raise RequestFailure('ResumeRequestMismatch')
            return self.accept(result)
        previous = self.prior_root / directory.relative_to(self.root)
        prior_result = previous / 'result.json'
        prior_class = None
        if prior_result.exists():
            relative = str(prior_result.relative_to(self.prior_root))
            if sha256(prior_result.read_bytes()) != self.prior_hashes.get(relative):
                raise RequestFailure('PriorResultNotInFrozenIncidentAudit')
            old = json.loads(prior_result.read_text('utf-8'))
            prior_class = response_classification(old)
            if old['status'] == 'ok' and old['request_sha256'] == fingerprint:
                if old.get('returned_model') != MODEL or old.get('finish_reason') != 'stop':
                    raise RequestFailure('InvalidPriorResponse')
                result = dict(old, origin='v2_success_reused', prior_path=str(prior_result),
                              prior_result_sha256=sha256(prior_result.read_bytes()),
                              classification='response_known_ok', actual_http_attempts_new=0,
                              usage_billed_in_this_run=False)
                persist(final, result)
                return result
            if old['status'] != 'ok':
                permitted = (prior_class == 'urlerror_phase_not_recorded_response_unknown'
                             and self.allow_legacy_urlerror)
                if not permitted:
                    result = {'status': 'api_failed', 'classification': prior_class,
                              'request_sha256': fingerprint, 'origin': 'v2_failure_preserved_not_resent',
                              'prior_path': str(prior_result), 'prior_result_sha256': sha256(prior_result.read_bytes()),
                              'actual_http_attempts_new': 0, 'usage_billed_in_this_run': False,
                              'usage': old.get('usage'), 'error_status': 'PriorFailureNotAuthorizedForResend'}
                    persist(final, result)
                    return self.accept(result)
        elif (previous / 'attempt.json').exists():
            result = {'status': 'api_failed', 'classification': 'interrupted_response_unknown',
                      'request_sha256': fingerprint, 'origin': 'v2_pending_not_resent',
                      'actual_http_attempts_new': 0, 'usage': None, 'usage_billed_in_this_run': False}
            persist(final, result)
            return self.accept(result)
        circuit = Circuit(self.circuit_path)
        timeout = self.timeout if call_id == 'initial' else self.other_timeout
        prior_attempts = sorted((directory / 'attempts').glob('*/attempt.json'))
        for path in prior_attempts:
            if not (path.parent / 'result.json').exists():
                result = {'status': 'api_failed', 'classification': 'interrupted_response_unknown',
                          'request_sha256': fingerprint, 'origin': 'v3_pending_not_resent',
                          'actual_http_attempts_new': len(prior_attempts), 'usage': None,
                          'usage_billed_in_this_run': True}
                persist(final, result)
                return self.accept(result)
        max_attempts = self.max_retries + 1
        if prior_class == 'urlerror_phase_not_recorded_response_unknown':
            max_attempts = self.max_retries  # at most 3 new attempts for legacy unknown URLError
        for number in range(1, max_attempts + 1):
            location = directory / 'attempts' / f'{number:04d}'
            saved = location / 'result.json'
            if saved.exists():
                result = json.loads(saved.read_text('utf-8'))
                if result['request_sha256'] != fingerprint:
                    raise RequestFailure('AttemptResumeMismatch')
            else:
                circuit.check()
                api_window_check(self.cutoff)
                if self.cutoff is not None and time.time() + timeout > self.cutoff:
                    raise APIWindowClosed('Full phase deadline cannot fit before API cutoff')
                secret = os.environ.get('OPENCODE_API', '').strip()
                if not secret:
                    raise RequestFailure('OPENCODE_API unavailable; no attempt')
                persist(location / 'attempt.json', {'started_utc': utc(), 'number': number,
                                                    'request_sha256': fingerprint, 'timeout': timeout,
                                                    'legacy_urlerror_recovery': prior_class == 'urlerror_phase_not_recorded_response_unknown',
                                                    'session_id': session_id, 'channel': 'OpenCode Go'})
                persist(location / 'request.json', payload)
                request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
                    'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json',
                    'User-Agent': 'tracecoder-aligned/0.5', 'x-opencode-session': session_id})
                started = time.monotonic()
                result = {'status': 'api_failed', 'request_sha256': fingerprint, 'origin': 'new_v3',
                          'started_utc': utc(), 'number': number, 'timeout': timeout, 'usage': None,
                          'requested_model': MODEL, 'actual_http_attempts_new': number,
                          'usage_billed_in_this_run': True}
                try:
                    status, body, network = self.transport(request, timeout)
                    body = json.loads(json.dumps(body).replace(secret, '[REDACTED]'))
                    persist(location / 'response.json', body)
                    result.update(http_status=status, returned_model=body.get('model'), usage=body.get('usage'), network=network)
                    # A service response resets transport failures even when the
                    # model returns a truncated/invalid response.
                    circuit.note(status == 200, 'http_response_received')
                    if status != 200:
                        raise RequestFailure('Non200Response')
                    if body.get('model') != MODEL or len(body.get('choices', [])) != 1:
                        raise RequestFailure('ModelOrChoiceProtocolMismatch')
                    choice = body['choices'][0]
                    finish = choice.get('finish_reason')
                    result['finish_reason'] = finish
                    if finish != 'stop':
                        result['classification'] = ('output_budget_exceeded_response_known' if finish == 'length'
                                                    else 'non_stop_response_known')
                        raise RequestFailure('NonStopResponse')
                    content = choice.get('message', {}).get('content')
                    if not isinstance(content, str) or not content.strip():
                        raise RequestFailure('EmptyResponse')
                    result.update(status='ok', content=content, classification='response_known_ok')
                except TransportFailure as error:
                    result.update(network=error.metadata, classification=error.metadata['classification'],
                                  error_type='TransportFailure')
                    circuit.note(False, result['classification'])
                except Exception as error:
                    result.update(error_type=type(error).__name__)
                    result.setdefault('classification', 'response_protocol_failure_known'
                                      if result.get('network', {}).get('response_known') else 'worker_response_unknown')
                    if 'http_status' not in result:
                        circuit.note(False, result['classification'])
                result.update(seconds=round(time.monotonic() - started, 4), finished_utc=utc())
                persist(saved, result)
            retry = (result['status'] != 'ok'
                     and result.get('network', {}).get('request_not_sent') is True
                     and result.get('classification') == 'confirmed_pre_request_failure'
                     and number < max_attempts)
            if not retry:
                persist(final, result)
                return self.accept(result)
            circuit.check()
            time.sleep(min(2**number, 8))
        raise RequestFailure('AttemptLimitExceeded')

    @staticmethod
    def accept(result):
        if result['status'] != 'ok':
            raise RequestFailure(result.get('classification', result['status']))
        return result
