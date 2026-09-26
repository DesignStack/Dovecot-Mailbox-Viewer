"""Stream dbox storage records from an archive or an extracted directory.

This module handles message framing; binary status indexes are read separately.
The per-record B field supplies a fallback folder when no reliable GUID mapping
is available. Missing metadata is surfaced as Unfiled rather than guessed.
"""

from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import re
import tarfile
import zlib
from viewer.operations import CheckedReader

HEADER = re.compile(rb"\x01\x02([NP]) +([0-9a-fA-F]{1,16})\r?\n")
FOOTER = b"\n\x01\x03\n"
MAX_RECORD = 256 * 1024 * 1024  # Reject implausible lengths in untrusted backups.
GZIP_MAGIC = b"\x1f\x8b"


@dataclass(frozen=True)
class Record:
    mailbox: str
    raw: bytes
    source: str
    bytes_done: int = 0
    bytes_total: int = 0
    guid: bytes | None = None


def _root_parts(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Recognise account root by the storage/m.* path, independent of tar prefix."""
    if len(parts) >= 3 and parts[-2] == "storage" and re.fullmatch(r"m\.\d+", parts[-1]):
        return parts[:-2]
    return None


def _account_name(root: tuple[str, ...]) -> str:
    return f"{root[-1]}@{root[-2]}" if len(root) >= 2 else "/".join(root)


def discover(path: Path, check=lambda: None) -> dict[str, dict]:
    """Scan paths off the GUI thread, sequentially for compressed archives."""
    if path.is_dir():
        def entries():
            for p in path.rglob("*"):
                check()
                if p.is_file() or p.is_dir():
                    yield tuple(p.relative_to(path).parts), str(p), p.is_dir(), p.stat().st_size
        return _discover_entries(entries())
    with path.open('rb') as raw, tarfile.open(fileobj=CheckedReader(raw, check), mode='r|gz') as tf:
        return _discover_entries((tuple(PurePosixPath(m.name).parts), m.name, m.isdir(), m.size)
                                 for m in tf if m.isfile() or m.isdir())


def _discover_entries(entries):
    result = {}
    all_entries = list(entries)
    for parts, name, is_dir, size in all_entries:
        root = _root_parts(parts)
        if root is not None and not is_dir:
            account = _account_name(root)
            info = result.setdefault(account, {"root": root, "storage": [], "sizes": {}, "folders": set()})
            info["storage"].append(name)
            info["sizes"][name] = size
    for parts, _, is_dir, _ in all_entries:
        for info in result.values():
            root = info["root"]
            if parts[:len(root)+1] != root + ("mailboxes",):
                continue
            tail = parts[len(root)+1:]
            marker = next((i for i, part in enumerate(tail) if part.casefold() in ('dbox-mails', 'dbox-mail')), None)
            folder = tail[:marker] if marker is not None else (tail if is_dir else ())
            if folder and not folder[0].startswith('dovecot'):
                info["folders"].add('/'.join(folder))
    return result


def _decode_payload(payload: bytes, source: str) -> bytes:
    """Detect gzip per message; normal dbox records may also be uncompressed.

    Dovecot's N record type means "normal", not "compressed". Like Dovecot's
    compression detector, inspect the payload signature instead. A corrupt gzip
    stream must raise an error, never be silently indexed as plain email.
    """
    if not payload.startswith(GZIP_MAGIC):
        return payload
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        # One extra byte distinguishes an oversized message from an exact fit.
        raw = decompressor.decompress(payload, MAX_RECORD + 1)
    except zlib.error as exc:
        raise ValueError(f"{source}: invalid gzip message") from exc
    if len(raw) > MAX_RECORD:
        raise ValueError(f"{source}: gzip message exceeds the {MAX_RECORD}-byte limit")
    if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
        raise ValueError(f"{source}: incomplete or invalid gzip message")
    return raw


def records(stream, source: str, check=lambda: None):
    """Yield complete message records, validating framing and gzip streams."""
    first = stream.readline(200)
    if not first.startswith(b"2 "):
        raise ValueError(f"{source}: unsupported dbox storage header")
    while True:
        check()
        line = stream.readline(200)
        if not line:
            break
        if not line.strip():
            continue
        match = HEADER.fullmatch(line)
        if not match:
            raise ValueError(f"{source}: invalid message header at offset {stream.tell() - len(line)}")
        length = int(match[2], 16)
        if not 0 < length <= MAX_RECORD:
            raise ValueError(f"{source}: invalid record length {length}")
        payload = stream.read(length)
        if len(payload) != length:
            raise ValueError(f"{source}: truncated message")
        raw = _decode_payload(payload, source)
        if stream.read(len(FOOTER)) != FOOTER:
            raise ValueError(f"{source}: missing message footer")
        attributes = {}
        while True:
            field = stream.readline(2048)
            if not field or field in (b"\n", b"\r\n"):
                break
            if field[:1] in (b"B", b"R", b"G", b"V", b"Z"):
                attributes[field[:1]] = field[1:].strip().decode("utf-8", "replace")
        guid_text = attributes.get(b"G", "")
        try:
            guid = bytes.fromhex(guid_text) if len(guid_text) == 32 else None
        except ValueError:
            guid = None
        yield Record(attributes.get(b"B") or "Unfiled", raw, source, guid=guid)


def read_account(path: Path, info: dict, check=lambda: None):
    """Read tar members in physical order to avoid repeated gzip decompression."""
    completed = 0
    if path.is_dir():
        total = sum(Path(name).stat().st_size for name in info["storage"])
        for name in sorted(info["storage"]):
            check()
            with open(name, "rb") as stream:
                for record in records(stream, name, check):
                    yield replace(record, bytes_done=completed + stream.tell(), bytes_total=total)
            completed += Path(name).stat().st_size
    else:
        names = set(info['storage'])
        total = sum(info.get('sizes', {}).values())
        with path.open('rb') as raw, tarfile.open(fileobj=CheckedReader(raw, check), mode='r|gz') as tf:
            for member in tf:
                check()
                if not member.isfile() or member.name not in names:
                    continue
                with tf.extractfile(member) as stream:
                    for record in records(stream, member.name, check):
                        yield replace(record, bytes_done=completed + stream.tell(), bytes_total=max(total, member.size))
                completed += member.size
