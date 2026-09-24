"""Private, rebuildable SQLite catalogue for local searches and previews."""

from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import sqlite3


class _PlainText(HTMLParser):
    """Extract searchable text without rendering untrusted HTML or fetching URLs."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _PlainText()
    parser.feed(html)
    return "".join(parser.parts)


def _text_header(value):
    try:
        return str(make_header(decode_header(str(value or ""))))
    except (ValueError, UnicodeError):
        return str(value or "")


def describe(raw: bytes):
    message = BytesParser(policy=policy.default).parsebytes(raw)
    text = []
    html = []
    attachments = []
    for part in message.walk():
        if part.is_multipart():
            continue
        if part.get_filename() or part.get_content_disposition() == "attachment":
            attachments.append(_text_header(part.get_filename() or "Attachment"))
            continue
        if part.get_content_type() in ("text/plain", "text/html"):
            try:
                content = part.get_content()
            except (ValueError, LookupError, UnicodeError, KeyError):
                content = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            (html if part.get_content_type() == "text/html" else text).append(str(content))
    return message, "\n".join(text), "\n".join(html), attachments


class Catalogue:
    def __init__(self, file: Path):
        file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(file)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS folders(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, folder TEXT NOT NULL, sender TEXT, recipients TEXT,
                subject TEXT, date TEXT, body TEXT, has_attachment INTEGER,
                raw BLOB NOT NULL, source TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
                subject, sender, recipients, body, content='messages', content_rowid='id');
        """)

    def reset(self):
        self.conn.executescript("DELETE FROM message_search; DELETE FROM messages; DELETE FROM folders;")

    def add_folder(self, name):
        self.conn.execute("INSERT OR IGNORE INTO folders VALUES (?)", (name,))

    def add(self, record):
        msg, plain, html, attachments = describe(record.raw)
        body = plain or html_to_text(html)
        values = (record.mailbox, _text_header(msg.get("From")),
                  _text_header(msg.get("To")), _text_header(msg.get("Subject")),
                  _text_header(msg.get("Date")), body, int(bool(attachments)),
                  record.raw, record.source)
        self.add_folder(record.mailbox)
        cursor = self.conn.execute("""INSERT INTO messages
            (folder,sender,recipients,subject,date,body,has_attachment,raw,source)
            VALUES (?,?,?,?,?,?,?,?,?)""", values)
        self.conn.execute("INSERT INTO message_search(rowid,subject,sender,recipients,body) VALUES (?,?,?,?,?)",
                          (cursor.lastrowid, values[3], values[1], values[2], body))

    def commit(self):
        self.conn.commit()

    def folders(self):
        return self.conn.execute("""SELECT folders.name, COUNT(messages.id) AS count
            FROM folders LEFT JOIN messages ON messages.folder=folders.name
            GROUP BY folders.name ORDER BY folders.name COLLATE NOCASE""").fetchall()

    def messages(self, folder=None, query="", sender="", subject="", attachments=False,
                 after="", before=""):
        sql = "SELECT id, folder, sender, subject, date, body, has_attachment FROM messages WHERE 1=1"
        args = []
        if folder:
            sql += " AND folder=?"; args.append(folder)
        if sender.strip():
            sql += " AND sender LIKE ?"; args.append(f"%{sender.strip()}%")
        if subject.strip():
            sql += " AND subject LIKE ?"; args.append(f"%{subject.strip()}%")
        if attachments:
            sql += " AND has_attachment=1"
        # Email Date headers are not normalised in the catalogue; apply date
        # filters after retrieving candidate rows with parsed UTC dates below.
        if query.strip():
            # Quote user text to avoid FTS operators and punctuation errors.
            tokens = __import__("re").findall(r"\w+", query, __import__("re").UNICODE)
            if tokens:
                sql += " AND id IN (SELECT rowid FROM message_search WHERE message_search MATCH ?)"
                args.append(" AND ".join('"' + token.replace('"', '""') + '"' for token in tokens))
        sql += " ORDER BY id DESC"
        if not (after or before):
            sql += " LIMIT 5000"
        rows = self.conn.execute(sql, args).fetchall()
        if after or before:
            from email.utils import parsedate_to_datetime
            from datetime import date
            def in_range(row):
                try:
                    sent = parsedate_to_datetime(row["date"]).date()
                    return (not after or sent >= date.fromisoformat(after)) and (not before or sent <= date.fromisoformat(before))
                except (TypeError, ValueError, IndexError):
                    return False
            rows = [row for row in rows if in_range(row)]
        return rows[:5000]

    def message(self, ident):
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (ident,)).fetchone()

    def close(self):
        self.conn.close()


def cache_path(source: Path, account: str):
    import os
    root = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".cache") / "DesignStack" / "DovecotMailboxViewer"
    key = hashlib.sha256((str(source.resolve()) + account).encode()).hexdigest()[:20]
    return root / f"{key}.sqlite3"
