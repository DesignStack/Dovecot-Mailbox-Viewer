"""Print email headers and the visible body without fetching any extra resources."""
from html import escape
from pathlib import Path
import os
import tempfile

from PySide6.QtCore import QByteArray, QMarginsF
from PySide6.QtGui import QImage, QPageLayout, QPageSize, QTextCursor, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from viewer.html_preview import resource_key


class MailPrintDocument(QTextDocument):
    def __init__(self, preview, headers, attachments=()):
        super().__init__()
        # Copy consented resources now; printing never starts a network request.
        self.images = {**preview.local_images, **preview.remote_images}
        self.placeholder = QImage(preview.placeholder)
        self.setDefaultFont(preview.font())
        cursor = QTextCursor(self)
        subject = headers.get('Subject') or '(No subject)'
        header = f'<h2>{escape(str(subject))}</h2>'
        for key in ('From', 'To', 'Cc', 'Date'):
            if headers.get(key):
                header += f'<p><b>{key}:</b> {escape(str(headers[key]))}</p>'
        if attachments:
            header += '<p><b>Attachments:</b> ' + escape(', '.join(attachments)) + '</p>'
        if any(url not in preview.remote_images for url in preview.remote_urls):
            header += '<p><i>Remote images not downloaded are omitted.</i></p>'
        cursor.insertHtml(header + '<hr>')
        cursor.insertBlock()
        cursor.insertHtml(preview.document().toHtml())

    def loadResource(self, kind, url):
        if kind == QTextDocument.ResourceType.ImageResource:
            key = resource_key(url)
            if key.startswith('//'):
                key = 'https:' + key
            return self.images.get(key, self.placeholder)
        return QByteArray()


def make_printer(subject, *, pdf=False):
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    if pdf:
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    # Email HTML uses CSS pixels (96 per inch). Keep their physical size when
    # printing; PDF text remains vector text rather than a raster screenshot.
    printer.setResolution(96)
    printer.setDocName(subject or 'Email')
    printer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.A4),
                                     QPageLayout.Orientation.Portrait,
                                     QMarginsF(14, 14, 14, 14), QPageLayout.Unit.Millimeter))
    return printer


def print_document(document, printer):
    # Supplying the printable page size avoids QTextDocument's extra default
    # two-centimetre margin on top of the margins already set on the printer.
    document.setDocumentMargin(0)
    document.documentLayout().setPaintDevice(printer)
    document.setPageSize(printer.pageRect(QPrinter.Unit.DevicePixel).size())
    document.print_(printer)


def save_pdf(document, destination, subject='Email'):
    """Finish a temporary PDF before replacing the chosen destination."""
    destination = Path(destination)
    fd, name = tempfile.mkstemp(prefix='.mail-pdf-', suffix='.pdf', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        printer = make_printer(subject, pdf=True)
        printer.setOutputFileName(str(temporary))
        print_document(document, printer)
        with temporary.open('rb') as stream:
            if stream.read(5) != b'%PDF-' or temporary.stat().st_size < 100:
                raise OSError('PDF creation failed. Please choose another destination.')
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
