"""Read GUID and system flag changes from Dovecot index transaction logs.

Only validated log layouts are applied. Unknown or incomplete state remains unknown;
index cache files contain MIME metadata and cannot establish message flags.
"""
from dataclasses import dataclass
from pathlib import Path
from struct import unpack_from
import logging
import tarfile

SEEN = 0x08
DELETED = 0x04


@dataclass(frozen=True)
class Status:
    uid: int
    flags: int
    expunged: bool = False


def _transactions(data: bytes):
    if len(data) < 40 or data[0] != 1 or data[1] != 3:
        raise ValueError('Unsupported Dovecot transaction log version')
    header = unpack_from('<H', data, 2)[0]
    if header < 40 or header > len(data):
        raise ValueError('Invalid transaction log header')
    offset = header
    while offset < len(data):
        if offset + 8 > len(data) or any(byte < 128 for byte in data[offset:offset + 4]):
            raise ValueError('Truncated transaction header')
        size = 0
        for byte in data[offset:offset + 4]:
            size = (size << 7) | (byte & 127)
        size *= 4
        if size < 8 or offset + size > len(data):
            raise ValueError('Invalid transaction size')
        yield unpack_from('<I', data, offset + 4)[0] & 0xffff, data[offset + 8:offset + size]
        offset += size


def parse_log(data: bytes) -> dict[bytes, Status]:
    """Replay append, flag and expunge operations, joining UIDs to GUIDs."""
    flags = {}
    guids = {}
    expunged = set()
    extension = None
    guid_extension = None
    extension_number = 0
    for kind, payload in _transactions(data):
        if kind == 0x40 and len(payload) >= 20:  # EXT_INTRO
            ext_id, _, _, record_size, _, _, name_size = unpack_from('<IIIHHHH', payload)
            if ext_id == 0xffffffff:
                name = payload[20:20 + name_size]
                if len(name) != name_size:
                    raise ValueError('Truncated extension name')
                extension = extension_number
                extension_number += 1
                if name == b'guid' and record_size == 16:
                    guid_extension = extension
            else:
                extension = ext_id
        elif kind == 0x200 and extension == guid_extension and guid_extension is not None:
            if len(payload) < 20:
                raise ValueError('Truncated GUID extension record')
            uid = unpack_from('<I', payload)[0]
            guids[uid] = payload[4:20]
        elif kind == 0x02:  # APPEND: base record is 8 bytes
            if len(payload) < 8:
                raise ValueError('Truncated append record')
            uid = unpack_from('<I', payload)[0]
            flags[uid] = payload[4]
        elif kind == 0x04:  # FLAG_UPDATE: UID range and add/remove masks
            if len(payload) % 12:
                raise ValueError('Truncated flag update')
            for offset in range(0, len(payload), 12):
                first, last = unpack_from('<II', payload, offset)
                if last < first or last - first > 1_000_000:
                    raise ValueError('Invalid flag UID range')
                for uid in range(first, last + 1):
                    if uid in flags:
                        flags[uid] = (flags[uid] | payload[offset + 8]) & ~payload[offset + 9]
        elif kind == 0x01:  # EXPUNGE (including protected expunge)
            if len(payload) % 8:
                raise ValueError('Truncated expunge record')
            for offset in range(0, len(payload), 8):
                first, last = unpack_from('<II', payload, offset)
                if last < first or last - first > 1_000_000:
                    raise ValueError('Invalid expunge UID range')
                expunged.update(range(first, last + 1))
        elif kind == 0x2000:  # EXPUNGE_GUID
            if len(payload) % 20:
                raise ValueError('Truncated GUID expunge')
            for offset in range(0, len(payload), 20):
                uid = unpack_from('<I', payload, offset)[0]
                guids.setdefault(uid, payload[offset + 4:offset + 20])
                expunged.add(uid)
    return {guid: Status(uid, flags[uid], uid in expunged)
            for uid, guid in guids.items() if uid in flags}


def read_statuses(source: Path, info: dict) -> dict[bytes, tuple[str, Status]]:
    """Read account folder logs; ambiguous GUIDs are omitted."""
    root = info['root']
    prefix = '/'.join(root + ('mailboxes',)) + '/'
    result = {}
    if source.is_dir():
        members = ((p.relative_to(source).as_posix(), p.read_bytes()) for p in source.rglob('dovecot.index.log'))
    else:
        def archive_members():
            with tarfile.open(source, 'r:gz') as archive:
                for member in archive:
                    if member.isfile() and member.name.endswith('/dovecot.index.log'):
                        stream = archive.extractfile(member)
                        if stream:
                            yield member.name, stream.read()
        members = archive_members()
    for name, data in members:
        if not name.startswith(prefix) or not name.endswith('/dbox-Mails/dovecot.index.log'):
            continue
        folder = name[len(prefix):-len('/dbox-Mails/dovecot.index.log')]
        try:
            for guid, status in parse_log(data).items():
                if guid in result:
                    result.pop(guid)
                else:
                    result[guid] = (folder, status)
        except ValueError as exc:
            logging.getLogger('viewer').warning('Cannot interpret %s: %s', name, exc)
    return result
