"""Sorting, complete pagination, conversations and incremental cache semantics."""
from email.message import EmailMessage
from pathlib import Path
import tempfile
import unittest
from viewer.catalog import Catalogue
from viewer.mdbox import Record
from viewer.dovecot_index import Status, SEEN


def mail(subject, date='', sender='Example <a@example.com>', ident='', refs='', folder='INBOX'):
    msg = EmailMessage()
    msg['Subject'], msg['From'], msg['To'] = subject, sender, 'You <you@example.com>'
    if date:
        msg['Date'] = date
    if ident:
        msg['Message-ID'] = f'<{ident}>'
    if refs:
        msg['References'] = refs
    msg.set_content('The launch plan is ready. Search this body.')
    msg.add_alternative('<p>The <b>launch plan</b> is ready. Search this body.</p>', subtype='html')
    return Record(folder, msg.as_bytes(), 'synthetic')


class BrowsingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Catalogue(Path(self.temp.name) / 'index.sqlite3')

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_real_date_sort_timezone_invalid_dates_and_filters(self):
        self.db.add(mail('Zulu', 'Fri, 25 Sep 2026 10:00:00 +0200', 'Zed <z@example.com>'))
        self.db.add(mail('Alpha', 'Fri, 25 Sep 2026 09:00:00 +0000', 'alice <a@example.com>'))
        self.db.add(mail('Missing', '', 'Bob <b@example.com>'))
        self.db.add(mail('Old', 'Thu, 24 Sep 2026 23:00:00 -0100', 'Chris <c@example.com>'))
        self.db.commit()
        subjects = lambda **kw: [r['subject'] for r in self.db.messages(**kw)]
        self.assertEqual(subjects(), ['Alpha', 'Zulu', 'Old', 'Missing'])
        self.assertEqual(subjects(sort='date_asc'), ['Old', 'Zulu', 'Alpha', 'Missing'])
        self.assertEqual(subjects(sort='sender_asc'), ['Alpha', 'Missing', 'Old', 'Zulu'])
        self.assertEqual(subjects(sort='subject_desc'), ['Zulu', 'Old', 'Missing', 'Alpha'])
        self.assertEqual(subjects(after='2026-09-25', before='2026-09-25'), ['Alpha', 'Zulu'])
        self.assertEqual(self.db.matching_count(after='2026-09-25', before='2026-09-25'), 2)
        self.assertEqual(subjects(sort='DROP TABLE messages'), subjects())

    def test_pagination_can_reach_every_record_beyond_5000(self):
        self.db.conn.executemany('INSERT INTO messages(folder,subject,raw,source) VALUES (?,?,?,?)',
                                [('INBOX', f'Message {i}', b'raw', 'synthetic') for i in range(5107)])
        self.db.commit()
        ids = []
        for offset in range(0, self.db.matching_count(), 200):
            ids.extend(row['id'] for row in self.db.messages(limit=200, offset=offset))
        self.assertEqual(len(ids), 5107)
        self.assertEqual(len(set(ids)), 5107)
        self.assertEqual(self.db.messages(offset=5107), [])

    def test_threads_merge_late_parent_and_bridge_but_never_subject_alone(self):
        self.db.add(mail('Plan', ident='child@x', refs='<parent@x>', folder='Sent'))
        self.db.add(mail('Plan', ident='other@x'))
        self.db.add(mail('Plan', ident='separate@x', refs='<ancestor@x>'))
        self.assertEqual(self.db.matching_count(conversations=True), 3)
        self.db.add(mail('Re: Plan', ident='parent@x', refs='<ancestor@x>'))
        self.db.add(mail('Plan'))
        self.db.add(mail('Plan'))
        self.db.commit()
        self.assertEqual(self.db.matching_count(conversations=True), 4)
        self.assertEqual({r['id'] for r in self.db.conversation(1)}, {1, 3, 4})
        grouped = self.db.messages(conversations=True, folder='INBOX')
        self.assertEqual(sorted(r['thread_count'] for r in grouped), [1, 1, 1, 2])
        # Threads survive closing/reopening the database and new imports.
        path = self.db.path
        self.db.close()
        self.db = Catalogue(path)
        self.db.add(mail('Another reply', ident='new@x', refs='<child@x>'))
        self.assertEqual(len(self.db.conversation(1)), 4)

    def test_cache_schema_and_authoritative_folder(self):
        self.db.add(mail('Moved', folder='Old'), Status(1, SEEN), folder='Archive')
        self.db.finish('fingerprint')
        self.assertTrue(self.db.reusable('fingerprint'))
        self.assertFalse(self.db.reusable('changed'))
        self.assertEqual(self.db.messages()[0]['folder'], 'Archive')
        self.db.conn.execute("UPDATE metadata SET value='2' WHERE key='schema'")
        self.assertFalse(self.db.reusable('fingerprint'))
        self.db.reset()
        self.assertEqual(self.db.count(), 0)
        self.assertFalse(self.db.reusable('fingerprint'))
