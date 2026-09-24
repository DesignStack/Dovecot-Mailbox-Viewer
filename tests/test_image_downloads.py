"""Real HTTP regression tests without contacting third-party email trackers."""
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

from PySide6.QtCore import QBuffer, QCoreApplication, QEvent, QIODevice, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtWidgets import QApplication

from viewer.app import Window
from viewer.html_preview import SafeHtmlPreview, resource_key


class ImageDownloads(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        image = QImage(24, 24, QImage.Format.Format_RGB32)
        image.fill(QColor('#fc12b6'))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        cls.png = bytes(buffer.data())
        cls.hits = []
        cls.slow_started = threading.Event()
        cls.release_slow = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.hits.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/Template%20Images/logo.png?x=1&y=2')
                    self.end_headers()
                    return
                if self.path == '/slow':
                    cls.slow_started.set()
                    cls.release_slow.wait(2)
                if self.path == '/missing':
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(cls.png)))
                self.end_headers()
                try:
                    if self.path == '/drip':
                        # Regular data defeats an inactivity-only timeout.
                        for byte in cls.png:
                            self.wfile.write(bytes([byte]))
                            self.wfile.flush()
                            time.sleep(0.04)
                    else:
                        self.wfile.write(cls.png)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.release_slow.set()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def tearDown(self):
        # Dispose Qt objects on the GUI thread, before the HTTP server thread can
        # become the thread that triggers Python's cyclic garbage collector.
        for widget in QApplication.topLevelWidgets():
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_until(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate(), 'Image operation did not complete')
        self.app.processEvents()

    def test_encoded_url_and_redirect_replace_cached_placeholder(self):
        preview = SafeHtmlPreview()
        preview.resize(360, 240)
        preview.show()
        urls = [self.base + '/Template%20Images/logo.png?x=1&y=2', self.base + '/redirect']
        preview.display(''.join(f'<img src="{u.replace("&", "&amp;")}" width="24" height="24">' for u in urls), EmailMessage())
        self.app.processEvents()
        before = len(self.hits)
        self.assertEqual(preview.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(urls[0])).pixelColor(0, 0).alpha(), 0)
        self.assertEqual(len(self.hits), before)
        preview.load_images()
        self.wait_until(lambda: not preview.pending)
        self.assertFalse(preview.failed_images)
        self.assertEqual(len(preview.remote_images), 2)
        for url in urls:
            result = preview.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(url))
            self.assertEqual(result.pixelColor(0, 0).name(), '#fc12b6')
        # Check visible pixels too: cached image bytes alone do not prove repaint.
        rendered = preview.viewport().grab().toImage()
        self.assertTrue(any(rendered.pixelColor(x, y).name() == '#fc12b6'
                            for x in range(0, 120, 2) for y in range(0, 100, 2)))
        preview.display(f'<img src="{urls[0]}">', EmailMessage())
        self.assertFalse(preview.remote_images)
        self.assertEqual(preview.document().resource(QTextDocument.ResourceType.ImageResource, QUrl(urls[0])).pixelColor(0, 0).alpha(), 0)
        preview.close()

    def test_failure_stays_visible_and_retry_and_menu_use_same_handler(self):
        window = Window()
        window.show()
        url = self.base + '/missing'
        window.preview.display(f'<img src="{url}">', EmailMessage())
        self.assertTrue(window.image_notice.isVisible())
        window.images_action.trigger()
        self.wait_until(lambda: not window.preview.pending)
        self.assertTrue(window.image_notice.isVisible())
        self.assertIn('could not', window.notice_text.text())
        self.assertIn('Try again', window.images_link.text())
        previous = self.hits.count('/missing')
        window.images_link.linkActivated.emit('download')
        self.wait_until(lambda: not window.preview.pending)
        self.assertEqual(self.hits.count('/missing'), previous + 1)
        window.close()

    def test_switching_message_cancels_pending_image(self):
        preview = SafeHtmlPreview()
        self.release_slow.clear()
        self.slow_started.clear()
        url = self.base + '/slow'
        preview.display(f'<img src="{url}">', EmailMessage())
        preview.load_images()
        self.wait_until(self.slow_started.is_set)
        preview.display('<p>Another email</p>', EmailMessage())
        self.release_slow.set()
        self.wait_until(lambda: not preview.active_replies)
        self.assertEqual(preview.remote_urls, [])
        self.assertFalse(preview.remote_images)
        self.assertFalse(preview.failed_images)
        preview.close()

    def test_total_deadline_stops_dripping_response_but_keeps_loaded_image(self):
        window = Window()
        window.show()
        window.preview.image_deadline_ms = 700
        fast = self.base + '/image.png'
        drip = self.base + '/drip'
        window.preview.display(f'<img src="{fast}"><img src="{drip}">', EmailMessage())
        started = time.monotonic()
        window.images_action.trigger()
        self.assertIn('Stop', window.images_link.text())
        self.wait_until(lambda: not window.preview.pending)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn(fast, window.preview.remote_images)
        self.assertIn('timed out', window.preview.failed_images[drip])
        self.assertIn('1 of 2', window.notice_text.text())
        self.assertIn('timed out', window.notice_text.text())
        self.assertIn('Try again', window.images_link.text())
        self.assertFalse(window.preview.active_replies)
        window.close()

    def test_deadline_and_stop_work_before_response_headers(self):
        window = Window()
        window.show()
        self.release_slow.clear()
        self.slow_started.clear()
        url = self.base + '/slow'
        window.preview.image_deadline_ms = 300
        window.preview.display(f'<img src="{url}">', EmailMessage())
        window.images_action.trigger()
        self.wait_until(self.slow_started.is_set)
        self.wait_until(lambda: not window.preview.pending)
        self.assertIn('timed out', window.notice_text.text())
        # A retry gets its own deadline, and Stop must cancel immediately.
        window.preview.image_deadline_ms = 20_000
        window.images_link.linkActivated.emit('download')
        self.assertTrue(window.preview.pending)
        window.images_link.linkActivated.emit('stop')
        self.assertFalse(window.preview.pending)
        self.assertIn('stopped', window.notice_text.text())
        self.release_slow.set()
        window.close()
