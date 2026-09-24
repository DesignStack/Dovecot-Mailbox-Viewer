"""Small synthetic tests; never commit customer email to the repository."""

from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import gzip
import tempfile
import unittest

from viewer.catalog import Catalogue
from viewer.mdbox import records, Record


class StorageTests(unittest.TestCase):
    def test_compressed_mailbox_record(self):
        payload = b"From: Test <test@example.com>\nSubject: Hello\n\nBody text\n"
        compressed = gzip.compress(payload)
        record = (b"\x01\x02N " + f"{len(compressed):016X}".encode() + b"\n" +
                  compressed + b"\n\x01\x03\nBINBOX\n\n")
        stream = BytesIO(b"2 M1e C00000000\n" + record)
        result = list(records(stream, "synthetic"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].mailbox, "INBOX")
        self.assertEqual(result[0].raw, payload)

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
