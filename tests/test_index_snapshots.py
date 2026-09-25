"""Dovecot 7.x snapshot layout and replay ordering against synthetic indexes."""
import struct
import unittest
from viewer.dovecot_index import parse_snapshot, parse_folder, parse_log, SEEN, DELETED
from test_dovecot_index import transaction

GUID1, GUID2 = bytes(range(1, 17)), bytes(range(17, 33))


def snapshot(sequence=4, offset=40):
    header = bytearray(144)
    header[0:2] = bytes([7, 3])
    struct.pack_into('<HII', header, 2, 120, 144, 24)
    header[12] = 1
    struct.pack_into('<I', header, 16, 99)
    struct.pack_into('<I', header, 32, 2)
    struct.pack_into('<III', header, 60, sequence, offset, offset)
    struct.pack_into('<IIHHHH', header, 120, 0, 0, 8, 16, 4, 4)
    header[136:140] = b'guid'
    return bytes(header) + struct.pack('<IB3x', 1, SEEN) + GUID1 + struct.pack('<IB3x', 2, 0) + GUID2


def log(sequence=4, previous=0, previous_offset=0, minor=3):
    header = bytearray(40)
    header[:4] = bytes([1, minor, 40, 0])
    struct.pack_into('<IIII', header, 4, 99, sequence, previous, previous_offset)
    header[32] = 1
    return bytes(header)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_and_tail_replay_skip_already_applied_transactions(self):
        earlier = transaction(4, struct.pack('<IIBB2x', 1, 1, 0, SEEN))
        later = transaction(4, struct.pack('<IIBB2x', 2, 2, SEEN | DELETED, 255))
        data = snapshot(offset=40+len(earlier))
        state = parse_folder({'dovecot.index': data, 'dovecot.index.log': log()+earlier+later})
        self.assertEqual(state[GUID1].flags, SEEN)
        self.assertEqual(state[GUID2].flags, SEEN | DELETED)

    def test_rotated_log_chain_and_protected_expunge(self):
        first = log()+transaction(4, struct.pack('<IIBB2x', 2, 2, SEEN, 0))
        second = log(5, 4, len(first))+transaction(0x10000000 | 0xcd90 | 0x2000, struct.pack('<I', 1)+GUID1)
        state = parse_folder({'dovecot.index': snapshot(), 'dovecot.index.log.2': first, 'dovecot.index.log': second})
        self.assertTrue(state[GUID1].expunged)
        self.assertEqual(state[GUID2].flags, SEEN)
        with self.assertRaisesRegex(ValueError, 'history'):
            parse_folder({'dovecot.index': snapshot(), 'dovecot.index.log': second})

    def test_batched_append_and_guid_records_with_older_log_header(self):
        intro = struct.pack('<IIIHHHH', 0xffffffff, 0, 0, 16, 4, 0, 4)+b'guid'
        data = log(minor=1)+transaction(0x40, intro)
        data += transaction(2, struct.pack('<IB3xIB3x', 1, SEEN, 2, DELETED))
        data += transaction(0x200, struct.pack('<I', 1)+GUID1+struct.pack('<I', 2)+GUID2)
        flags = parse_log(data)
        self.assertEqual(flags[GUID1].flags, SEEN)
        self.assertEqual(flags[GUID2].flags, DELETED)

    def test_bad_lengths_byte_order_unknown_guid_and_wrong_identity_are_rejected(self):
        for data in (snapshot()[:-1], bytes([8])+snapshot()[1:], snapshot()[:12]+b'\0'+snapshot()[13:]):
            with self.assertRaises(ValueError):
                parse_snapshot(data)
        bad_log = bytearray(log())
        struct.pack_into('<I', bad_log, 4, 100)
        with self.assertRaisesRegex(ValueError, 'identities'):
            parse_folder({'dovecot.index': snapshot(), 'dovecot.index.log': bytes(bad_log)})
        with self.assertRaises(ValueError):
            parse_folder({'dovecot.index': snapshot(offset=41), 'dovecot.index.log': log()})
