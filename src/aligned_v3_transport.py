"""Observed HTTPS send boundary, JSON-only IPC, killable Linux worker."""
import http.client
import json
import multiprocessing
import os
import time
import urllib.error
import urllib.request
from .aligned_client import ENDPOINT, NoRedirect, _stop_worker


class TransportFailure(Exception):
    def __init__(self, metadata):
        self.metadata = metadata
        super().__init__(metadata['classification'])


def network_worker(packet, deadline, sender):
    state = {'phase': 'not_connected', 'request_started': False}
    def phase(name):
        state['phase'] = name
        sender.send_bytes(json.dumps({'event': 'phase', 'phase': name}).encode())
    class Connection(http.client.HTTPSConnection):
        def connect(self):
            self.inside_connect = True
            phase('connecting')
            try:
                super().connect()
                phase('connected')
            finally:
                self.inside_connect = False
        def send(self, data):
            if self.sock is None and self.auto_open:
                self.connect()
            if not getattr(self, 'inside_connect', False):
                # Conservative: mark before sendall, even if it writes zero.
                state['request_started'] = True
                phase('application_request_started')
            return super().send(data)
    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, request):
            return self.do_open(Connection, request, context=self._context)
    try:
        os.setsid()
        if packet['url'] != ENDPOINT:
            raise ValueError('EndpointChanged')
        request = urllib.request.Request(packet['url'], data=packet['data'].encode(), headers=packet['headers'])
        with urllib.request.build_opener(NoRedirect(), Handler()).open(request, timeout=deadline) as response:
            phase('response_headers_received')
            if response.geturl() != ENDPOINT:
                raise ValueError('EndpointChanged')
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError('ResponseSizeExceeded')
            body = {'event': 'result', 'status': response.status, 'body': json.loads(raw),
                    'phase': state['phase'], 'request_not_sent': False, 'response_known': True}
    except BaseException as error:
        reason = getattr(error, 'reason', None)
        known_http = isinstance(error, urllib.error.HTTPError)
        body = {'event': 'failure', 'transport_error_type': type(error).__name__,
                'reason_type': type(reason).__name__ if reason is not None else None,
                'reason_errno': getattr(reason, 'errno', None), 'phase': state['phase'],
                'request_not_sent': not state['request_started'] and not known_http,
                'response_known': known_http, 'http_status': error.code if known_http else None}
        body['classification'] = ('http_response_failure' if known_http else
                                  'confirmed_pre_request_failure' if body['request_not_sent'] else
                                  'transport_response_unknown')
    try:
        sender.send_bytes(json.dumps(body).encode())
    finally:
        sender.close()


def http_once_v3(request, deadline):
    if os.name != 'posix':
        raise RuntimeError('Linux network process required')
    # Spawn avoids fork-with-reader-threads inherited from the sandbox client.
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    packet = {'url': request.full_url, 'data': request.data.decode(), 'headers': dict(request.header_items())}
    process = context.Process(target=network_worker, args=(packet, deadline, sender))
    started = time.monotonic()
    process.start()
    sender.close()
    events = []
    try:
        while True:
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0 or not receiver.poll(remaining):
                # Last observed connecting phase is NOT enough to prove unsent:
                # an unconsumed send event may be in the pipe at the deadline.
                raise TransportFailure({'classification': 'deadline_response_unknown',
                                        'request_not_sent': False, 'response_known': False,
                                        'phase': events[-1]['phase'] if events else 'unobserved', 'events': events})
            try:
                body = json.loads(receiver.recv_bytes(24_000_000))
            except (EOFError, OSError, ValueError) as error:
                raise TransportFailure({'classification': 'worker_response_unknown',
                                        'request_not_sent': False, 'response_known': False,
                                        'transport_error_type': type(error).__name__, 'events': events}) from None
            if body['event'] == 'phase':
                events.append({'phase': body['phase'], 'seconds': round(time.monotonic() - started, 4)})
                continue
            body['events'] = events
            if body['event'] == 'failure':
                raise TransportFailure(body)
            if body['event'] != 'result':
                raise ValueError('Invalid network worker protocol')
            return body['status'], body['body'], {'events': events, 'response_known': True, 'request_not_sent': False}
    finally:
        receiver.close()
        _stop_worker(process)
