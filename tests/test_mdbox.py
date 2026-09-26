"""Small synthetic tests; never commit customer email to the repository."""

from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import gzip
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from viewer.catalog import Catalogue
from viewer.mdbox import discover, read_account, records, Record


def storage_record(payload, *, compressed=False, folder="INBOX"):
    """A normal v2 record, including Dovecot's eight reserved header bytes."""
    stored = gzip.compress(payload) if compressed else payload
    return (b"\x01\x02N " + b" " * 8 + b" " + f"{len(stored):016x}".encode()
            + b"\n" + stored + b"\n\x01\x03\nB" + folder.encode() + b"\n\n")


def storage_file(*messages):
    return b"2 M1e C00000000\n" + b"".join(messages)


class StorageTests(unittest.TestCase):
    def test_compressed_mailbox_record(self):
        payload = b"From: Test <test@example.com>\nSubject: Hello\n\nBody text\n"
        stream = BytesIO(storage_file(storage_record(payload, compressed=True)))
        result = list(records(stream, "synthetic"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].mailbox, "INBOX")
        self.assertEqual(result[0].raw, payload)

    def test_uncompressed_normal_record_preserves_mime_bytes(self):
        # N means normal, not compressed. Attachments and CRLF must stay intact.
        message = EmailMessage()
        message["Subject"] = "Uncompressed attachment"
        message.set_content("Plain message body")
        message.add_attachment(b"\x00\xff\x1f\x8b", maintype="application",
                               subtype="octet-stream", filename="sample.bin")
        payload = message.as_bytes().replace(b"\n", b"\r\n")
        result = list(records(BytesIO(storage_file(storage_record(payload))), "m.1"))
        self.assertEqual(result[0].raw, payload)
        self.assertEqual(result[0].mailbox, "INBOX")

    def test_mixed_compression_in_either_order(self):
        first = b"Subject: First\n\nFirst body\n"
        second = b"Subject: Second\n\nSecond body\n"
        for compressed_first in (False, True):
            with self.subTest(compressed_first=compressed_first):
                data = storage_file(
                    storage_record(first, compressed=compressed_first),
                    storage_record(second, compressed=not compressed_first, folder="Sent"))
                result = list(records(BytesIO(data), "m.1"))
                self.assertEqual([r.raw for r in result], [first, second])
                self.assertEqual([r.mailbox for r in result], ["INBOX", "Sent"])

    def test_mixed_mailbox_in_folder_and_gzip_archive(self):
        messages = [b"Subject: Plain\n\nPlain body\n", b"Subject: Gzip\n\nGzip body\n"]
        data = storage_file(storage_record(messages[0]),
                            storage_record(messages[1], compressed=True))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "backup"
            storage = folder / "email/example.com/test/storage/m.1"
            storage.parent.mkdir(parents=True)
            storage.write_bytes(data)
            archive = root / "backup.tar.gz"
            with tarfile.open(archive, "w:gz") as output:
                output.add(folder, arcname="backup")
            archive_before = archive.read_bytes()
            for source in (folder, archive):
                with self.subTest(source=source.name):
                    info = discover(source)["test@example.com"]
                    result = list(read_account(source, info))
                    self.assertEqual([r.raw for r in result], messages)
                    self.assertEqual(result[-1].bytes_done, result[-1].bytes_total)
            self.assertEqual(storage.read_bytes(), data)
            self.assertEqual(archive.read_bytes(), archive_before)

    def test_invalid_gzip_is_not_treated_as_plain_mail(self):
        compressed = gzip.compress(b"Subject: Test\n\nBody\n")
        corrupt_crc = compressed[:-8] + bytes([compressed[-8] ^ 1]) + compressed[-7:]
        for payload in (b"\x1f\x8bnot-gzip", compressed[:-4], corrupt_crc,
                        compressed + b"trailing data"):
            with self.subTest(payload=payload[:12]):
                data = storage_file(storage_record(payload))
                with self.assertRaisesRegex(ValueError, "m.1: .*gzip"):
                    list(records(BytesIO(data), "m.1"))

    def test_gzip_expansion_limit(self):
        with patch("viewer.mdbox.MAX_RECORD", 128):
            for size in (128, 129):
                data = storage_file(storage_record(b"x" * size, compressed=True))
                with self.subTest(size=size):
                    if size == 128:
                        self.assertEqual(list(records(BytesIO(data), "m.1"))[0].raw,
                                         b"x" * size)
                    else:
                        with self.assertRaisesRegex(ValueError, "m.1: .*exceeds"):
                            list(records(BytesIO(data), "m.1"))

    def test_search_and_empty_folder(self):
        message = EmailMessage()
        message["From"] = "Person <person@example.com>"
        message["To"] = "Recipient <recipient@example.com>"
        message["Subject"] = "Test enquiry"
        message.set_content("Unique searchable body")
        with tempfile.TemporaryDirectory() as temp:
            catalogue = Catalogue(Path(temp) / "index.sqlite3")
            catalogue.add_folder("Archive")
            catalogue.add(Record("INBOX", message.as_bytes(), "synthetic"))
            catalogue.commit()
            self.assertEqual(len(catalogue.messages("INBOX", "searchable")), 1)
            self.assertEqual(len(catalogue.messages("Archive")), 0)
            self.assertEqual(len(catalogue.messages(None, "missing")), 0)
            self.assertEqual(len(catalogue.messages(None, sender="person@example.com")), 1)
            self.assertEqual(len(catalogue.messages(None, attachments=True)), 0)
            catalogue.close()


if __name__ == "__main__":
    unittest.main()
