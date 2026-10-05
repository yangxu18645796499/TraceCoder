import http.client
import json
import urllib.error
import urllib.request
import unittest
from unittest.mock import patch
from src.aligned_client import ENDPOINT
from src.aligned_v3_transport import network_worker


class Sender:
    def __init__(self): self.records = []
    def send_bytes(self, value): self.records.append(json.loads(value))
    def close(self): pass


class V3TransportTests(unittest.TestCase):
    def run_failure(self, after_connect):
        sender = Sender()
        class Socket:
            def sendall(self, data): raise http.client.RemoteDisconnected()
        def connect(connection):
            if not after_connect:
                raise ConnectionRefusedError(111, 'test refused')
            connection.sock = Socket()
        def do_open(factory, request, **kwargs):
            connection = factory('opencode.ai')
            connection.send(b'POST test')
        class Opener:
            def __init__(self, handler): self.handler = handler
            def open(self, request, **kwargs): return self.handler.https_open(request)
        with patch('src.aligned_v3_transport.os.setsid'), \
             patch('urllib.request.build_opener', side_effect=lambda redirect, handler: Opener(handler)), \
             patch('http.client.HTTPSConnection.connect', connect), \
             patch('urllib.request.AbstractHTTPHandler.do_open', side_effect=do_open):
            network_worker({'url': ENDPOINT, 'data': '{}', 'headers': {}}, 90, sender)
        return sender.records[-1]
    def test_connect_failure_is_proven_pre_request(self):
        result = self.run_failure(False)
        self.assertTrue(result['request_not_sent'])
        self.assertEqual(result['classification'], 'confirmed_pre_request_failure')
    def test_send_failure_is_conservatively_unknown(self):
        result = self.run_failure(True)
        self.assertFalse(result['request_not_sent'])
        self.assertEqual(result['classification'], 'transport_response_unknown')
