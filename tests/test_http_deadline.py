"""Real Linux process cleanup, mocked network body; no model/API calls."""
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from src.aligned_client import http_once, RequestFailure


def immediate(request, deadline, sender):
    os.setsid()
    sender.send_bytes(json.dumps({'status': 200, 'body': {'model': 'test-only'}}).encode())
    sender.close()


def delayed(marker, deadline, sender):
    os.setsid()
    time.sleep(0.6)
    Path(marker).write_text('late worker was not terminated', 'utf-8')
    sender.close()


def network_error(request, deadline, sender):
    os.setsid()
    sender.send_bytes(json.dumps({'error_type': 'HTTPError', 'http_status': 429}).encode())
    sender.close()


@unittest.skipUnless(os.name == 'posix', 'Linux-only deadline worker')
class HTTPDeadlineTests(unittest.TestCase):
    def test_success_is_json_and_worker_cleaned(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with patch('src.aligned_client._http_worker', immediate):
            self.assertEqual(http_once(None, 1), (200, {'model': 'test-only'}))
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_deadline_prevents_late_worker_and_next_call_overlap(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / 'late.txt'
            with patch('src.aligned_client._http_worker', delayed):
                with self.assertRaisesRegex(RequestFailure, 'response unknown; never resend'):
                    http_once(str(marker), 0.08)
            with patch('src.aligned_client._http_worker', immediate):
                self.assertEqual(http_once(None, 1)[0], 200)
            time.sleep(0.65)
            self.assertFalse(marker.exists())
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_http_error_has_safe_metadata_and_no_worker(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with patch('src.aligned_client._http_worker', network_error):
            with self.assertRaises(RequestFailure) as caught:
                http_once(None, 1)
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(caught.exception.transport_error_type, 'HTTPError')
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)
