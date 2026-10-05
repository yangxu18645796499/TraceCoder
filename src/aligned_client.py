"""Go-only client, one explicit HTTP attempt, durable ledger before networking."""
from __future__ import annotations
import datetime
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time
import urllib.error
import urllib.request
from .aligned_protocol import persist, sha256

ENDPOINT = 'https://opencode.ai/zen/go/v1/chat/completions'
MODEL = 'deepseek-v4.1-flash'


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class RequestFailure(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _http_worker(request, deadline, sender):
    """Trusted network worker; JSON IPC only, never a candidate process."""
    try:
        os.setsid()
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=deadline) as response:
            if response.geturl() != ENDPOINT:
                raise RequestFailure('EndpointChanged')
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise RequestFailure('ResponseSizeExceeded')
            body = {'status': response.status, 'body': json.loads(raw)}
    except BaseException as error:
        body = {'error_type': type(error).__name__}
        if isinstance(error, urllib.error.HTTPError):
            body['http_status'] = error.code
        if isinstance(error, RequestFailure):
            body['error_status'] = str(error)
    try:
        sender.send_bytes(json.dumps(body).encode())
    finally:
        sender.close()


def _stop_worker(process):
    # No pending worker may overlap the next request after a deadline.
    for action in (signal.SIGTERM, signal.SIGKILL):
        if not process.is_alive():
            break
        try:
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, action)
            else:
                os.kill(process.pid, action)
        except ProcessLookupError:
            pass
        process.join(timeout=0.5)
    process.join(timeout=0)
    if process.is_alive():
        raise RequestFailure('HTTPWorkerCleanupFailed; no next request permitted')


def http_once(request, deadline):
    if os.name != 'posix':
        raise RequestFailure('LinuxHTTPWorkerRequired')
    started = time.monotonic()
    receiver, sender = multiprocessing.get_context('fork').Pipe(duplex=False)
    process = multiprocessing.get_context('fork').Process(target=_http_worker,
                                                          args=(request, deadline, sender))
    process.start()
    sender.close()
    try:
        remaining = max(0, deadline - (time.monotonic() - started))
        if not receiver.poll(remaining):
            raise RequestFailure('RequestDeadlineExceeded; response unknown; never resend')
        body = json.loads(receiver.recv_bytes(24_000_000))
        if 'error_type' in body:
            error = RequestFailure(body.get('error_status', 'HTTPTransportError:' + body['error_type']))
            error.transport_error_type = body['error_type']
            error.http_status = body.get('http_status')
            raise error
        return body['status'], body['body']
    finally:
        receiver.close()
        _stop_worker(process)


class GoClient:
    def __init__(self, directory, *, transport=http_once, max_tokens=2048, timeout=90,
                 repair_max_tokens=None, reasoning_effort=None):
        self.directory = Path(directory)
        self.transport = transport
        self.max_tokens, self.timeout = max_tokens, timeout
        self.repair_max_tokens = repair_max_tokens or max_tokens
        if reasoning_effort not in (None,'low','high','max'):
            raise ValueError('InvalidReasoningEffort')
        self.reasoning_effort = reasoning_effort

    def call(self, call_id, messages, session_id):
        if not isinstance(call_id, str) or not call_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in call_id):
            raise ValueError('Unsafe call_id')
        directory = self.directory / call_id
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / 'result.json').exists():
            result = json.loads((directory / 'result.json').read_text('utf-8'))
            if result.get('request_sha256') != sha256(json.dumps(self.payload(messages,call_id), sort_keys=True)):
                raise RequestFailure('ResumeRequestMismatch')
            if result['status'] != 'ok':
                raise RequestFailure(result['status'])
            return result
        secret = os.environ.get('OPENCODE_API', '').strip()
        if not secret:
            raise RequestFailure('OPENCODE_API missing; no attempt made')
        payload = self.payload(messages,call_id)
        fingerprint = sha256(json.dumps(payload, sort_keys=True))
        # Interrupted pending attempts are never replayed automatically.
        persist(directory / 'attempt.json', {'started_utc': utc(), 'request_sha256': fingerprint,
                                             'session_id': session_id, 'attempts': 1, 'retries': 0})
        persist(directory / 'request.json', payload)
        request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode(), headers={
            'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json',
            'User-Agent': 'tracecoder-aligned/0.4', 'x-opencode-session': session_id})
        started = time.monotonic()
        result = {'status': 'pending', 'request_sha256': fingerprint, 'started_utc': utc(),
                  'usage': None, 'requested_model': MODEL, 'attempts': 1, 'retries': 0}
        try:
            status, body = self.transport(request, self.timeout)
            body = json.loads(json.dumps(body).replace(secret, '[REDACTED]'))
            persist(directory / 'response.json', body)
            result.update(http_status=status, returned_model=body.get('model'), usage=body.get('usage'))
            if status != 200:
                raise RequestFailure('Non200Response')
            if body.get('model') != MODEL:
                raise RequestFailure('ModelIdentifierMismatch')
            if len(body.get('choices', [])) != 1:
                raise RequestFailure('NotOneChoice')
            choice = body['choices'][0]
            result['finish_reason'] = choice.get('finish_reason')
            if choice.get('finish_reason') != 'stop':
                raise RequestFailure('TruncatedOrNonStopResponse')
            content = choice.get('message', {}).get('content')
            if not isinstance(content, str) or not content.strip():
                raise RequestFailure('EmptyResponse')
            result.update(status='ok', content=content)
        except BaseException as error:
            # Do not print API exception details or headers; preserve only safe codes.
            result.update(status='api_failed', error_type=type(error).__name__)
            if getattr(error, 'transport_error_type', None):
                result['transport_error_type'] = error.transport_error_type
            if getattr(error, 'http_status', None) is not None:
                result['http_status'] = error.http_status
            if isinstance(error, RequestFailure):
                result['error_status'] = str(error).replace(secret, '[REDACTED]')[:500]
            if isinstance(error, urllib.error.HTTPError):
                result['http_status'] = error.code
        result.update(seconds=round(time.monotonic()-started, 4), finished_utc=utc())
        persist(directory / 'result.json', result)
        if result['status'] != 'ok':
            raise RequestFailure(result.get('error_status', result['error_type']))
        return result

    def payload(self, messages, call_id='initial'):
        payload = {'model': MODEL, 'messages': messages, 'temperature': 0,
                   'max_tokens': self.max_tokens if call_id=='initial' else self.repair_max_tokens, 'stream': False}
        if self.reasoning_effort is not None:
            payload['reasoning_effort'] = self.reasoning_effort
        return payload

