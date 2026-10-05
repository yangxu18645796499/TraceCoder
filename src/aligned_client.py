"""Go-only client, one explicit HTTP attempt, durable ledger before networking."""
from __future__ import annotations
import datetime
import json
import os
from pathlib import Path
import queue
import threading
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


def http_once(request, deadline):
    replies = queue.Queue(maxsize=1)
    def execute():
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=deadline) as response:
                if response.geturl() != ENDPOINT:
                    raise RequestFailure('EndpointChanged')
                replies.put((response.status, response.read(4_000_001), None))
        except BaseException as error:
            replies.put((None, None, error))
    threading.Thread(target=execute, daemon=True).start()
    try:
        status, raw, error = replies.get(timeout=deadline)
    except queue.Empty:
        raise RequestFailure('RequestDeadlineExceeded; response unknown; never resend') from None
    if error:
        raise error
    if len(raw) > 4_000_000:
        raise RequestFailure('ResponseSizeExceeded')
    return status, json.loads(raw)


class GoClient:
    def __init__(self, directory, *, transport=http_once, max_tokens=2048, timeout=90):
        self.directory = Path(directory)
        self.transport = transport
        self.max_tokens, self.timeout = max_tokens, timeout

    def call(self, call_id, messages, session_id):
        if not isinstance(call_id, str) or not call_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in call_id):
            raise ValueError('Unsafe call_id')
        directory = self.directory / call_id
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / 'result.json').exists():
            result = json.loads((directory / 'result.json').read_text('utf-8'))
            if result.get('request_sha256') != sha256(json.dumps(self.payload(messages), sort_keys=True)):
                raise RequestFailure('ResumeRequestMismatch')
            if result['status'] != 'ok':
                raise RequestFailure(result['status'])
            return result
        secret = os.environ.get('OPENCODE_API', '').strip()
        if not secret:
            raise RequestFailure('OPENCODE_API missing; no attempt made')
        payload = self.payload(messages)
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
            if isinstance(error, RequestFailure):
                result['error_status'] = str(error).replace(secret, '[REDACTED]')[:500]
            if isinstance(error, urllib.error.HTTPError):
                result['http_status'] = error.code
        result.update(seconds=round(time.monotonic()-started, 4), finished_utc=utc())
        persist(directory / 'result.json', result)
        if result['status'] != 'ok':
            raise RequestFailure(result.get('error_status', result['error_type']))
        return result

    def payload(self, messages):
        return {'model': MODEL, 'messages': messages, 'temperature': 0,
                'max_tokens': self.max_tokens, 'stream': False}

