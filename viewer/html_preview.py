"""Render email HTML locally; request remote images only after explicit action."""

from email.message import Message
from html.parser import HTMLParser

from PySide6.QtCore import QUrl, QByteArray
from PySide6.QtGui import QDesktopServices, QImage, QTextDocument
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import QMessageBox, QTextBrowser


class ImageUrls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            src = dict(attrs).get("src", "")
            if src.lower().startswith(("https://", "http://")) and src not in self.urls:
                self.urls.append(src)


class SafeHtmlPreview(QTextBrowser):
    """QTextBrowser never fetches remote resources unless load_images is used."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.confirm_link)
        self.network = QNetworkAccessManager(self)
        self.html_content = ""
        self.local_images = {}
        self.remote_images = {}
        self.pending = set()
        self.active_replies = set()
        self.generation = 0

    def loadResource(self, kind, url):
        key = url.toString()
        if kind == QTextDocument.ResourceType.ImageResource:
            data = self.local_images.get(key) or self.remote_images.get(key)
            if data:
                return QImage.fromData(data)
        # Deny file and network reads initiated by an untrusted email.
        return QByteArray()

    def display(self, html: str, message: Message):
        self.generation += 1
        for reply in tuple(self.active_replies):
            reply.abort()
        self.active_replies.clear()
        self.html_content = html
        self.local_images = {}
        self.remote_images = {}
        self.pending = set()
        for part in message.walk():
            cid = part.get("Content-ID")
            if cid and part.get_content_maintype() == "image":
                data = part.get_payload(decode=True) or b""
                if len(data) <= 5_000_000:
                    self.local_images["cid:" + cid.strip("<>")] = data
        parser = ImageUrls()
        parser.feed(html)
        self.remote_urls = parser.urls
        self.setHtml(html)

    def load_images(self):
        # Explicit click; cap count and bytes, and never request other resources.
        for url in self.remote_urls[:30]:
            if url in self.remote_images or url in self.pending:
                continue
            self.pending.add(url)
            request = QNetworkRequest(QUrl(url))
            request.setTransferTimeout(10000)
            reply = self.network.get(request)
            self.active_replies.add(reply)
            generation = self.generation
            reply.downloadProgress.connect(lambda received, total, r=reply: r.abort() if received > 5_000_000 else None)
            reply.finished.connect(lambda r=reply, u=url, g=generation: self._image_finished(r, u, g))

    def _image_finished(self, reply, url, generation):
        self.active_replies.discard(reply)
        if generation != self.generation:
            reply.deleteLater()
            return
        self.pending.discard(url)
        if reply.error() == reply.NetworkError.NoError:
            data = bytes(reply.readAll())
            if len(data) <= 5_000_000 and not QImage.fromData(data).isNull():
                self.remote_images[url] = data
                self.setHtml(self.html_content)
        reply.deleteLater()

    def confirm_link(self, url):
        if url.scheme() in ("https", "http"):
            if QMessageBox.question(self, "Open external link?", f"Open this link in your browser?\n{url.toString()}") == QMessageBox.StandardButton.Yes:
                QDesktopServices.openUrl(url)
