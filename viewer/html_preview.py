"""Render email HTML locally; fetch images only after the user's explicit click."""
from email.message import Message
from html.parser import HTMLParser
from html import escape
import logging

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QImageReader, QTextDocument
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMessageBox, QTextBrowser
from viewer.version import __version__
from viewer.catalog import html_to_text

MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGES = 30
IMAGE_DEADLINE_MS = 20_000
LOGGER = logging.getLogger("viewer")
MAX_PREVIEW_CHARS = 500_000


class PreviewComplexity(HTMLParser):
    """Keep pathological table layouts out of Qt's synchronous text renderer."""
    def __init__(self):
        super().__init__()
        self.tags = self.cells = self.depth = self.max_depth = 0

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        self.cells += tag in ('td', 'th')
        if tag == 'table':
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

    def handle_endtag(self, tag):
        if tag == 'table':
            self.depth = max(0, self.depth - 1)


def preview_html(html):
    parser = PreviewComplexity()
    parser.feed(html[:MAX_PREVIEW_CHARS])
    if (len(html) <= MAX_PREVIEW_CHARS and parser.tags <= 10_000
            and parser.cells <= 2000 and parser.max_depth <= 10):
        return html, False
    # Only the preview is shortened. Search and .eml export retain the original.
    plain = html_to_text(html[:MAX_PREVIEW_CHARS])[:200_000]
    return ("<p style='background:#fff4d5;padding:10px'>Simplified preview: this email's HTML "
            "is unusually large or complex. Export the original .eml to view its full layout.</p>"
            "<pre style='white-space:pre-wrap'>" + escape(plain) + '</pre>'), True


def resource_key(url) -> str:
    """Use the same encoded key for HTML URLs, Qt callbacks and HTTP responses.

    QUrl.toString() decodes spaces, so comparing it with an HTML src containing
    %20 breaks lookups even after the image has downloaded successfully.
    """
    return bytes(QUrl(url).toEncoded()).decode("ascii")


class ImageUrls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            src = dict(attrs).get("src", "")
            if src.startswith("//"):
                src = "https:" + src
            if src.lower().startswith(("https://", "http://")):
                key = resource_key(src)
                if key not in self.urls:
                    self.urls.append(key)


def decode_image(data):
    """Validate compressed size and dimensions before allocating an image."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 25_000_000:
        return QImage()
    return reader.read()


class SafeHtmlPreview(QTextBrowser):
    """Block automatic resource loads and keep image consent per message."""
    images_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.confirm_link)
        self.network = QNetworkAccessManager(self)
        self.html_content = ""
        self.local_images = {}
        self.remote_images = {}
        self.remote_urls = []
        self.failed_images = {}
        self.pending = set()
        self.active_replies = set()
        self.generation = 0
        self.image_deadline_ms = IMAGE_DEADLINE_MS
        self.placeholder = QImage(1, 1, QImage.Format.Format_ARGB32)
        self.placeholder.fill(0)

    def loadResource(self, kind, url):
        if kind == QTextDocument.ResourceType.ImageResource:
            key = resource_key(url)
            # Protocol-relative URLs use HTTPS when explicitly downloaded.
            if key.startswith("//"):
                key = "https:" + key
            image = self.local_images.get(key) or self.remote_images.get(key)
            return image if image is not None else self.placeholder
        # Also deny local file reads and external CSS initiated by email HTML.
        return QByteArray()

    def display(self, html: str, message: Message):
        self.generation += 1
        for reply in tuple(self.active_replies):
            reply.abort()
        self.active_replies.clear()
        html, simplified = preview_html(html)
        if simplified:
            LOGGER.warning('Using simplified HTML preview (size/layout limit)')
        self.html_content = html
        self.local_images = {}
        self.remote_images = {}
        self.failed_images = {}
        self.pending = set()
        for part in message.walk():
            cid = part.get("Content-ID")
            if cid and part.get_content_maintype() == "image":
                image = decode_image(part.get_payload(decode=True) or b"")
                if not image.isNull():
                    self.local_images[resource_key("cid:" + cid.strip("<>"))] = image
        parser = ImageUrls()
        parser.feed(html)
        self.remote_urls = parser.urls
        # A new document drops Qt's resource cache. Images allowed for a previous
        # email must never bypass the notice when the next email has the same URL.
        document = QTextDocument(self)
        document.setDefaultFont(self.font())
        document.setDocumentMargin(24)
        previous = self.document()
        self.setDocument(document)
        # Documents explicitly parented to the browser otherwise survive every
        # message switch, retaining their text, layout and decoded image cache.
        try:
            previous.deleteLater()
        except RuntimeError:
            pass  # Qt already deleted its initial, internally owned document.
        self.setHtml(html)
        self.images_changed.emit()

    def load_images(self):
        for url in self.remote_urls[:MAX_IMAGES]:
            if url in self.remote_images or url in self.pending:
                continue
            self.failed_images.pop(url, None)
            self.pending.add(url)
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(15000)
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"Dovecot-Mailbox-Viewer/{__version__}")
            request.setRawHeader(b"Accept", b"image/png,image/jpeg,image/gif,image/webp,image/*;q=0.8")
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            reply = self.network.get(request)
            self.active_replies.add(reply)
            data = bytearray()
            generation = self.generation
            reply.readyRead.connect(lambda r=reply, b=data: self._read_image(r, b))
            reply.finished.connect(lambda r=reply, u=url, g=generation, b=data: self._image_finished(r, u, g, b))
            # Qt's transfer timeout measures inactivity, not total duration.
            # Start this deadline now so redirects, DNS and queued requests are
            # also bounded. Parenting it to the reply prevents stale callbacks.
            deadline = QTimer(reply)
            deadline.setSingleShot(True)
            deadline.timeout.connect(lambda r=reply, g=generation: self._image_timed_out(r, g))
            reply.finished.connect(deadline.stop)
            deadline.start(self.image_deadline_ms)
        self.images_changed.emit()

    def _image_timed_out(self, reply, generation):
        if generation == self.generation and reply in self.active_replies:
            reply.setProperty("downloadError", "Image download timed out")
            reply.abort()

    def cancel_images(self):
        """Stop pending requests while retaining images that already loaded."""
        for reply in tuple(self.active_replies):
            reply.setProperty("downloadError", "Image download stopped")
            reply.abort()

    @staticmethod
    def _read_image(reply, data):
        # Drain in bounded pieces so large/chunked responses cannot grow without a limit.
        remaining = MAX_IMAGE_BYTES + 1 - len(data)
        if remaining > 0:
            data.extend(bytes(reply.read(remaining)))
        if len(data) > MAX_IMAGE_BYTES or reply.bytesAvailable() > 0:
            reply.abort()

    def _image_finished(self, reply, url, generation, data):
        self.active_replies.discard(reply)
        if generation != self.generation:
            reply.deleteLater()
            return
        self.pending.discard(url)
        error = reply.property("downloadError") or reply.errorString()
        image = QImage()
        if reply.error() == QNetworkReply.NetworkError.NoError:
            self._read_image(reply, data)
            image = decode_image(data)
            error = "Image is too large or is not a supported image" if image.isNull() else ""
        if not image.isNull():
            self.remote_images[url] = image
            # Replace the placeholder already cached by QTextDocument and repaint
            # without resetting scroll position or the user's text selection.
            document = self.document()
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(url), image)
            document.addResource(QTextDocument.ResourceType.ImageResource, QUrl(url.removeprefix("https:")), image)
            document.markContentsDirty(0, document.characterCount())
            self.viewport().update()
        else:
            self.failed_images[url] = error
            # Host only: signed URLs and tracking tokens should not enter logs.
            LOGGER.warning("Image download failed from %s: %s", QUrl(url).host(),
                           error.replace(reply.url().toString(), "[image URL]"))
        reply.deleteLater()
        self.images_changed.emit()

    def confirm_link(self, url):
        if url.scheme() in ("https", "http"):
            if QMessageBox.question(self, "Open external link?", f"Open this link in your browser?\n{url.toString()}") == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(url)
