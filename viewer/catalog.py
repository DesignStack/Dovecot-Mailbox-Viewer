"""Private, rebuildable SQLite catalogue for local searches and previews."""

from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import sqlite3
import json
import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime, parseaddr

SCHEMA_VERSION = 3
SORTS = {
    "date_desc": ("Newest first", "sent_timestamp DESC"),
    "date_asc": ("Oldest first", "sent_timestamp ASC"),
    "sender_asc": ("Sender A–Z", "sender_sort ASC"),
    "sender_desc": ("Sender Z–A", "sender_sort DESC"),
    "subject_asc": ("Subject A–Z", "subject_sort ASC"),
    "subject_desc": ("Subject Z–A", "subject_sort DESC"),
}


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
        self.path = file
        file.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(file)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS folders(name TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, folder TEXT NOT NULL, sender TEXT, recipients TEXT,
                subject TEXT, date TEXT, body TEXT, has_attachment INTEGER,
                raw BLOB NOT NULL, source TEXT NOT NULL, status INTEGER, expunged INTEGER DEFAULT 0);
            CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
                subject, sender, recipients, body, content='messages', content_rowid='id');
        """)

        # Old caches remain readable to the cache manager, but are rebuilt once
        # before reuse so dates and conversation links are complete.
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info(messages)')}
        for name, kind in dict(status="INTEGER", expunged="INTEGER DEFAULT 0",
                sent_timestamp="REAL", sent_day="TEXT", sender_sort="TEXT", subject_sort="TEXT",
                thread_key="TEXT").items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {kind}")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS thread_nodes(token TEXT PRIMARY KEY, thread_key TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS messages_date ON messages(sent_timestamp);
            CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_key);
            CREATE INDEX IF NOT EXISTS messages_folder ON messages(folder);
            CREATE INDEX IF NOT EXISTS thread_groups ON thread_nodes(thread_key);
        """)

    def reset(self):
        self.conn.executescript("""DELETE FROM message_search; DELETE FROM messages;
            DELETE FROM folders; DELETE FROM metadata; DELETE FROM thread_nodes;""")

    def metadata(self):
        return dict(self.conn.execute("SELECT key, value FROM metadata"))

    def reusable(self, fingerprint):
        meta = self.metadata()
        return (meta.get('fingerprint') == fingerprint and meta.get('complete') == '1'
                and meta.get('schema') == str(SCHEMA_VERSION))

    def finish(self, fingerprint, **metadata):
        metadata.update(fingerprint=fingerprint, complete='1', schema=str(SCHEMA_VERSION),
                        indexed_at=datetime.now(timezone.utc).isoformat())
        self.conn.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                              [(key, str(value)) for key, value in metadata.items()])
        self.commit()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    def add_folder(self, name):
        self.conn.execute("INSERT OR IGNORE INTO folders VALUES (?)", (name,))

    def _thread(self, message, fallback):
        # References establish relationships even when parents are missing or
        # arrive later. Subject-only grouping would merge unrelated newsletters.
        own = re.findall(r"<[^<>\s]+>", str(message.get("Message-ID", "")))[:1]
        ancestors = re.findall(r"<[^<>\s]+>", str(message.get("References", "")))[:100]
        ancestors += re.findall(r"<[^<>\s]+>", str(message.get("In-Reply-To", "")))[:1]
        tokens = list(dict.fromkeys(own + ancestors)) or [fallback]
        groups = set()
        for token in tokens:
            row = self.conn.execute("SELECT thread_key FROM thread_nodes WHERE token=?", (token,)).fetchone()
            if row:
                groups.add(row[0])
        key = min(groups) if groups else tokens[0]
        for other in groups - {key}:
            self.conn.execute("UPDATE messages SET thread_key=? WHERE thread_key=?", (key, other))
            self.conn.execute("UPDATE thread_nodes SET thread_key=? WHERE thread_key=?", (key, other))
        self.conn.executemany("INSERT OR REPLACE INTO thread_nodes VALUES (?, ?)", [(t, key) for t in tokens])
        return key

    def add(self, record, status=None, folder=None):
        msg, plain, html, attachments = describe(record.raw)
        body = plain or html_to_text(html)
        sender, subject = _text_header(msg.get("From")), _text_header(msg.get("Subject"))
        timestamp = day = None
        try:
            sent = parsedate_to_datetime(str(msg.get("Date", "")))
            day = sent.date().isoformat()
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=timezone.utc)
            timestamp = sent.timestamp()
        except (ValueError, TypeError, IndexError, OverflowError, OSError):
            pass
        # Local identities must not accidentally thread two messages without IDs.
        fallback = "local:" + str(self.conn.execute("SELECT coalesce(max(id),0)+1 FROM messages").fetchone()[0])
        thread = self._thread(msg, fallback)
        values = (folder or record.mailbox, sender,
                  ", ".join(_text_header(msg.get(k)) for k in ("To", "Cc", "Bcc") if msg.get(k)),
                  subject, _text_header(msg.get("Date")), body, int(bool(attachments)),
                  record.raw, record.source, status.flags if status else None,
                  int(status.expunged) if status else 0, timestamp, day,
                  (parseaddr(sender)[0] or parseaddr(sender)[1] or sender).casefold(), subject.casefold(), thread)
        self.add_folder(values[0])
        cursor = self.conn.execute("""INSERT INTO messages
            (folder,sender,recipients,subject,date,body,has_attachment,raw,source,status,expunged,
             sent_timestamp,sent_day,sender_sort,subject_sort,thread_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        self.conn.execute("INSERT INTO message_search(rowid,subject,sender,recipients,body) VALUES (?,?,?,?,?)",
                          (cursor.lastrowid, subject, sender, values[2], body))

    def commit(self):
        self.conn.commit()

    def folders(self):
        return self.conn.execute("""SELECT folders.name, COUNT(messages.id) AS count
            FROM folders LEFT JOIN messages ON messages.folder=folders.name
            GROUP BY folders.name ORDER BY folders.name COLLATE NOCASE""").fetchall()

    @staticmethod
    def _filters(folder=None, query="", sender="", subject="", attachments=False, after="", before=""):
        clauses, args = ["1=1"], []
        for column, value in (("folder", folder), ("sender", sender.strip()), ("subject", subject.strip())):
            if value:
                clauses.append(f"{column} {'=' if column == 'folder' else 'LIKE'} ?")
                args.append(value if column == 'folder' else f"%{value}%")
        if attachments:
            clauses.append("has_attachment=1")
        for op, value in ((">=", after), ("<=", before)):
            if value:
                clauses.append(f"sent_day {op} ?")
                args.append(value)
        tokens = re.findall(r"\w+", query, re.UNICODE)
        if tokens:
            clauses.append("id IN (SELECT rowid FROM message_search WHERE message_search MATCH ?)")
            args.append(" AND ".join('"' + token + '"' for token in tokens))
        return " AND ".join(clauses), args

    def matching_count(self, *, conversations=False, **filters):
        where, args = self._filters(**filters)
        column = "DISTINCT coalesce(thread_key, 'local:' || id)" if conversations else '*'
        return self.conn.execute(f"SELECT count({column}) FROM messages WHERE {where}", args).fetchone()[0]

    def messages(self, folder=None, query="", sender="", subject="", attachments=False,
                 after="", before="", *, sort="date_desc", limit=200, offset=0, conversations=False):
        where, args = self._filters(folder, query, sender, subject, attachments, after, before)
        order = SORTS.get(sort, SORTS['date_desc'])[1]
        # Invalid/missing Date headers sort last in either date direction.
        order = ("sent_timestamp IS NULL, " if sort.startswith('date_') else '') + order + ", id DESC"
        columns = "id, folder, sender, subject, date, substr(body,1,500) AS body, has_attachment, status, expunged, thread_key"
        if conversations:
            sql = f"""WITH matches AS (SELECT *,
                row_number() OVER (PARTITION BY coalesce(thread_key,'local:'||id)
                    ORDER BY sent_timestamp IS NULL, sent_timestamp DESC, id DESC) AS position,
                count(*) OVER (PARTITION BY coalesce(thread_key,'local:'||id)) AS thread_count
                FROM messages WHERE {where})
                SELECT {columns}, thread_count FROM matches WHERE position=1 ORDER BY {order}"""
        else:
            sql = f"SELECT {columns}, 1 AS thread_count FROM messages WHERE {where} ORDER BY {order}"
        sql += " LIMIT ? OFFSET ?"
        return self.conn.execute(sql, args + [max(1, min(1000, int(limit))), max(0, int(offset))]).fetchall()

    def conversation(self, ident):
        return self.conn.execute("""SELECT id, sender, subject, date, folder FROM messages
            WHERE thread_key=(SELECT thread_key FROM messages WHERE id=?) OR id=?
            ORDER BY sent_timestamp IS NULL, sent_timestamp ASC, id ASC""", (ident, ident)).fetchall()

    def message(self, ident):
        return self.conn.execute("SELECT * FROM messages WHERE id=?", (ident,)).fetchone()

    def close(self):
        self.conn.close()


def cache_path(source: Path, account: str):
    import os
    root = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".cache") / "DesignStack" / "DovecotMailboxViewer"
    key = hashlib.sha256((str(source.resolve()) + account).encode()).hexdigest()[:20]
    return root / f"{key}.sqlite3"


def source_fingerprint(source: Path, info: dict, check=lambda: None) -> str:
    """Fast source identity; folder index changes invalidate the cache too."""
    if source.is_file():
        stat = source.stat()
        files = [(str(source.resolve()), stat.st_size, stat.st_mtime_ns)]
    else:
        files = []
        for p in source.rglob('*'):
            check()
            if p.is_file():
                stat = p.stat()
                files.append((str(p.relative_to(source)), stat.st_size, stat.st_mtime_ns))
        files.sort()
    return hashlib.sha256(json.dumps([SCHEMA_VERSION, info['root'], files]).encode()).hexdigest()
