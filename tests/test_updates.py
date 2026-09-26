"""Check version ordering and asynchronous checks against a local HTTP server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest

from PySide6.QtWidgets import QApplication
from viewer.updates import MAX_RESPONSE, UpdateChecker, release_version, version_tuple


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/slow':
            time.sleep(.4)
        status = 503 if self.path == '/error' else 200
        self.send_response(status)
        self.end_headers()
        data = (b' ' * (MAX_RESPONSE + 100) if self.path == '/large' else json.dumps({
            'tag_name': 'v0.10.0', 'draft': False, 'prerelease': False,
            'assets': [{'name': name, 'state': 'uploaded'} for name in
                       ('Dovecot-Mailbox-Viewer-Windows.exe', 'Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage')],
        }).encode())
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_versions_and_release_download_are_validated(self):
        self.assertGreater(version_tuple('v0.10.0'), version_tuple('0.2.0'))
        for payload in ({'tag_name': 'v0.2.1-beta'}, {'tag_name': 'v0.2.1', 'assets': []},
                        {'tag_name': 'v0.2.1', 'prerelease': True}):
            with self.assertRaises(ValueError):
                release_version(payload)

    def test_success_failures_timeout_and_response_limit(self):
        for endpoint, succeeds in (('/latest', True), ('/error', False), ('/slow', False), ('/large', False)):
            with self.subTest(endpoint=endpoint):
                checker = UpdateChecker(endpoint=f'http://127.0.0.1:{self.server.server_port}{endpoint}',
                                        deadline_ms=100 if endpoint == '/slow' else 2000)
                results, errors = [], []
                checker.checked.connect(results.append)
                checker.failed.connect(errors.append)
                checker.start()
                deadline = time.monotonic() + 4
                while not results and not errors and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.005)
                self.assertIsNone(checker.reply)
                if succeeds:
                    self.assertEqual(results, ['0.10.0'])
                    self.assertEqual(errors, [])
                else:
                    self.assertEqual(results, [])
                    self.assertEqual(len(errors), 1)
                checker.deleteLater()

    def test_linux_requires_its_own_uploaded_download(self):
        payload = {'tag_name': 'v0.5.0', 'assets': [
            {'name': 'Dovecot-Mailbox-Viewer-Windows.exe', 'state': 'uploaded'}]}
        self.assertEqual(release_version(payload, 'win32'), '0.5.0')
        with self.assertRaises(ValueError):
            release_version(payload, 'linux')
        payload['assets'].append({'name': 'Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage', 'state': 'uploaded'})
        self.assertEqual(release_version(payload, 'linux'), '0.5.0')

    def test_cancel_does_not_report_a_spurious_failure(self):
        checker = UpdateChecker(endpoint=f'http://127.0.0.1:{self.server.server_port}/slow')
        errors = []
        checker.failed.connect(errors.append)
        checker.start()
        checker.cancel()
        self.app.processEvents()
        self.assertIsNone(checker.reply)
        self.assertEqual(errors, [])
        checker.deleteLater()
