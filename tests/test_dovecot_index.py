"""Small transaction logs exercise GUID matching and flag changes."""
import struct
import unittest
from viewer.dovecot_index import parse_log, SEEN, DELETED


def transaction(kind, payload):
    size = (len(payload) + 8 + 3) // 4
    encoded = bytes((0x80 | (size >> shift & 0x7f)) for shift in (21, 14, 7, 0))
    return encoded + struct.pack('<I', kind) + payload.ljust(size * 4 - 8, b'\0')


class IndexTests(unittest.TestCase):
    def test_flags_and_expunge(self):
        header = bytearray(40)
        header[0:4] = b'\x01\x03\x28\x00'
        header[32] = 1
        guid = bytes(range(16))
        ext = struct.pack('<IIIHHHH', 0xffffffff, 0, 0, 16, 1, 1, 4) + b'guid'
        data = bytes(header) + transaction(0x40, ext)
        data += transaction(0x02, struct.pack('<IB3x', 7, 0))
        data += transaction(0x40, struct.pack('<IIIHHHH', 0, 0, 0, 16, 1, 1, 0))
        data += transaction(0x200, struct.pack('<I', 7) + guid)
        data += transaction(0x04, struct.pack('<IIBB2x', 7, 7, SEEN | DELETED, 0))
        data += transaction(0x04, struct.pack('<IIBB2x', 7, 7, 0, DELETED))
        data += transaction(0x01, struct.pack('<II', 7, 7))
        status = parse_log(data)[guid]
        self.assertEqual(status.flags, SEEN)
        self.assertTrue(status.expunged)
