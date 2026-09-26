"""Conservative reader for Dovecot 7.x indexes and 1.x transaction logs.

Layouts follow Dovecot's lib-index/mail-index.h, mail-index-private.h,
mail-index-map.c and mail-transaction-log.h at https://github.com/dovecot/core.
Only little-endian GUID layouts are interpreted; missing/ambiguous state stays
unknown. This module never writes to a Dovecot file.
"""
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from struct import unpack_from
import logging
import tarfile
from viewer.operations import CheckedReader

SEEN = 0x08
DELETED = 0x04
MAX_INDEX = 64 * 1024 * 1024


@dataclass(frozen=True)
class Status:
    uid: int
    flags: int
    expunged: bool = False


@dataclass
class IndexState:
    flags: dict = field(default_factory=dict)
    guids: dict = field(default_factory=dict)
    expunged: set = field(default_factory=set)
    extensions: list = field(default_factory=list)
    indexid: int = 0
    sequence: int = 0
    offset: int = 0

    def statuses(self):
        result, ambiguous = {}, set()
        for uid, guid in self.guids.items():
            if uid not in self.flags or not any(guid):
                continue
            if guid in result:
                ambiguous.add(guid)
            result[guid] = Status(uid, self.flags[uid], uid in self.expunged)
        return {guid: status for guid, status in result.items() if guid not in ambiguous}


def parse_snapshot(data):
    """Load base flags and the GUID extension from a validated 7.x snapshot."""
    if len(data) < 120 or data[0] != 7 or data[12] != 1:
        raise ValueError('Unsupported Dovecot index version or byte order')
    base, header, record_size = unpack_from('<HII', data, 2)
    count = unpack_from('<I', data, 32)[0]
    if (base < 120 or base > header or header > len(data) or record_size < 8
            or header + count * record_size > len(data)):
        raise ValueError('Truncated Dovecot index')
    state = IndexState(indexid=unpack_from('<I', data, 16)[0],
                       sequence=unpack_from('<I', data, 60)[0],
                       offset=unpack_from('<I', data, 68)[0])
    offset = (base + 7) & ~7
    guid_offset = None
    while offset < header:
        if offset + 16 > header:
            raise ValueError('Truncated index extension')
        size, reset, rec_offset, rec_size, align, name_size = unpack_from('<IIHHHH', data, offset)
        name_end = offset + 16 + name_size
        next_offset = offset + ((16 + name_size + 7) & ~7) + ((size + 7) & ~7)
        if name_end > header or next_offset > header or not name_size:
            raise ValueError('Invalid index extension length')
        name = data[offset + 16:name_end]
        state.extensions.append((name, rec_size, reset))
        if name == b'guid':
            if rec_size != 16 or rec_offset < 8 or rec_offset + 16 > record_size:
                raise ValueError('Unsupported GUID extension')
            guid_offset = rec_offset
        offset = next_offset
    if guid_offset is None:
        raise ValueError('Index has no GUID extension')
    previous = 0
    for offset in range(header, header + count * record_size, record_size):
        uid = unpack_from('<I', data, offset)[0]
        if uid <= previous:
            raise ValueError('Invalid index UID order')
        previous = uid
        state.flags[uid] = data[offset + 4]
        state.guids[uid] = data[offset + guid_offset:offset + guid_offset + 16]
    return state


def _log_header(data):
    if len(data) < 24 or data[0] != 1 or data[1] > 3:
        raise ValueError('Unsupported Dovecot transaction log version')
    header = unpack_from('<H', data, 2)[0]
    if header < 24 or header > len(data):
        raise ValueError('Invalid transaction log header')
    # 1.0/1.1 predate explicit compatibility flags. Newer logs require LE.
    if data[1] >= 2 and (header < 40 or data[32] != 1):
        raise ValueError('Unsupported transaction log byte order')
    return header, *unpack_from('<IIII', data, 4)


def _transactions(data, start=None):
    header = _log_header(data)[0]
    offset = header
    start = header if start is None else start
    if not header <= start <= len(data):
        raise ValueError('Invalid snapshot log offset')
    while offset < len(data):
        if offset + 8 > len(data) or any(byte < 128 for byte in data[offset:offset + 4]):
            raise ValueError('Truncated transaction header')
        size = 0
        for byte in data[offset:offset + 4]:
            size = (size << 7) | (byte & 127)
        size *= 4
        if size < 8 or offset + size > len(data) or offset < start < offset + size:
            raise ValueError('Invalid transaction size or snapshot boundary')
        kind = unpack_from('<I', data, offset + 4)[0] & 0x0fffffff
        if kind & 0xcd90 == 0xcd90:
            kind &= ~0xcd90  # Protected expunge marker, not an operation type.
        if offset >= start:
            payload = data[offset + 8:offset + size]
            if kind == 0x80000 and (len(payload) != 4 or offset + unpack_from('<I', payload)[0] > len(data)):
                raise ValueError('Incomplete transaction boundary')
            yield kind, payload
        offset += size


def _replay(data, state, start=None):
    extension = None
    for kind, payload in _transactions(data, start):
        if kind == 0x40:
            if len(payload) < 20:
                raise ValueError('Truncated extension introduction')
            ext_id, reset, _, record_size, _, _, name_size = unpack_from('<IIIHHHH', payload)
            if ext_id == 0xffffffff:
                name = payload[20:20 + name_size]
                if len(name) != name_size or not name:
                    raise ValueError('Invalid extension name')
                existing = next((i for i, ext in enumerate(state.extensions) if ext[0] == name), None)
                extension = len(state.extensions) if existing is None else existing
                if existing is None:
                    state.extensions.append((name, record_size, reset))
            else:
                extension = ext_id
            if extension >= len(state.extensions):
                raise ValueError('Log requires a missing index extension')
            name, old_size, old_reset = state.extensions[extension]
            if name == b'guid' and (record_size != 16 or old_size != 16 or old_reset != reset):
                raise ValueError('GUID extension was resized or reset')
        elif kind == 0x80 and extension is not None and state.extensions[extension][0] == b'guid':
            raise ValueError('GUID extension reset is not supported')
        elif kind == 0x200 and extension is not None and state.extensions[extension][0] == b'guid':
            if len(payload) % 20:
                raise ValueError('Truncated GUID extension record')
            for offset in range(0, len(payload), 20):
                state.guids[unpack_from('<I', payload, offset)[0]] = payload[offset + 4:offset + 20]
        elif kind == 0x02:
            if len(payload) % 8:
                raise ValueError('Truncated append record')
            for offset in range(0, len(payload), 8):
                uid = unpack_from('<I', payload, offset)[0]
                state.flags[uid] = payload[offset + 4]
        elif kind in (0x04, 0x01):
            stride = 12 if kind == 0x04 else 8
            if len(payload) % stride:
                raise ValueError('Truncated UID range')
            for offset in range(0, len(payload), stride):
                first, last = unpack_from('<II', payload, offset)
                if last < first:
                    raise ValueError('Invalid UID range')
                # Iterate known records, not an untrusted potentially enormous range.
                for uid in state.flags:
                    if first <= uid <= last:
                        if kind == 0x01:
                            state.expunged.add(uid)
                        else:
                            state.flags[uid] = (state.flags[uid] & ~payload[offset + 9]) | payload[offset + 8]
        elif kind == 0x2000:
            if len(payload) % 20:
                raise ValueError('Truncated GUID expunge')
            for offset in range(0, len(payload), 20):
                uid = unpack_from('<I', payload, offset)[0]
                guid = payload[offset + 4:offset + 20]
                if uid not in state.guids or state.guids[uid] == guid:
                    state.guids[uid] = guid
                    state.expunged.add(uid)
    return state


def parse_log(data):
    return _replay(data, IndexState()).statuses()


def parse_folder(files):
    state = parse_snapshot(files['dovecot.index']) if files.get('dovecot.index') else IndexState()
    logs = [(name, data, _log_header(data)) for name, data in files.items() if name != 'dovecot.index']
    logs.sort(key=lambda item: item[2][2])
    for _, data, (header, indexid, sequence, previous, previous_offset) in logs:
        if state.indexid and indexid != state.indexid:
            raise ValueError('Index and log identities differ')
        if state.sequence and sequence < state.sequence:
            continue
        if state.sequence and sequence > state.sequence:
            if (previous, previous_offset) != (state.sequence, state.offset):
                raise ValueError('Missing transaction log history')
            start = header
        else:
            start = state.offset or header
        _replay(data, state, start)
        state.indexid, state.sequence, state.offset = indexid, sequence, len(data)
    return state.statuses()


def read_statuses(source: Path, info: dict, check=lambda: None, progress=None):
    """Join GUIDs to folder state; never guess for duplicate GUIDs or bad indexes."""
    root = tuple(info['root']) + ('mailboxes',)
    wanted = {'dovecot.index', 'dovecot.index.log', 'dovecot.index.log.2'}
    folders = {}
    unreadable = set()

    def accept(name, size, read):
        parts = PurePosixPath(name).parts
        if (parts[:len(root)] != root or len(parts) < len(root)+3
                or parts[-2].casefold() not in ('dbox-mails', 'dbox-mail') or parts[-1] not in wanted):
            return
        folder = '/'.join(parts[len(root):-2])
        if size > MAX_INDEX:
            unreadable.add(folder)
            logging.getLogger('viewer').warning('Index exceeds supported size: %s', name)
            return
        folders.setdefault(folder, {})[parts[-1]] = read()

    if source.is_dir():
        for number, path in enumerate((source / Path(*root)).rglob('dovecot.index*'), 1):
            if progress:
                progress(number, 0, unit='indexes')
            check()
            if path.is_file():
                accept(path.relative_to(source).as_posix(), path.stat().st_size, path.read_bytes)
    else:
        with source.open('rb') as raw, tarfile.open(fileobj=CheckedReader(raw, check, progress, source.stat().st_size), mode='r|gz') as archive:
            for member in archive:
                check()
                if member.isfile():
                    accept(member.name, member.size, lambda: archive.extractfile(member).read())
    result, ambiguous = {}, set()
    for number, (folder, files) in enumerate(folders.items(), 1):
        check()
        if progress:
            progress(detail=f"Interpreting folder index {number} of {len(folders)}")
        if folder in unreadable:
            continue
        try:
            for guid, status in parse_folder(files).items():
                if guid in result:
                    ambiguous.add(guid)
                result[guid] = (folder, status)
        except ValueError as exc:
            logging.getLogger('viewer').warning('Cannot interpret folder indexes (%s): %s', folder, exc)
    return {guid: status for guid, status in result.items() if guid not in ambiguous}

