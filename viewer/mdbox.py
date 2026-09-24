"""Stream dbox storage records from an archive or an extracted directory.

This parser deliberately avoids reverse engineering the binary Dovecot indexes.
The per-record B field works for the supplied JetBackup sample; absent metadata
is surfaced as Unfiled rather than silently guessing a mailbox.
"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import tarfile
import zlib

HEADER = re.compile(rb"\x01\x02([NP]) +([0-9a-fA-F]{1,16})\r?\n")
FOOTER = b"\n\x01\x03\n"
MAX_RECORD = 256 * 1024 * 1024  # Reject implausible lengths in untrusted backups.


@dataclass(frozen=True)
class Record:
    mailbox: str
    raw: bytes
    source: str


def _root_parts(parts: tuple[str, ...]) -> tuple[str, ...] | None:
    """Recognise account root by the storage/m.* path, independent of tar prefix."""
    if len(parts) >= 3 and parts[-2] == "storage" and re.fullmatch(r"m\.\d+", parts[-1]):
        return parts[:-2]
    return None


def _account_name(root: tuple[str, ...]) -> str:
    return f"{root[-1]}@{root[-2]}" if len(root) >= 2 else "/".join(root)


def discover(path: Path) -> dict[str, dict]:
    """Return account names with storage entries and all named mailbox folders."""
    result: dict[str, dict] = {}
    if path.is_dir():
        entries = ((tuple(p.relative_to(path).parts), p) for p in path.rglob("*"))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:gz") as tf:
            return _discover_entries((tuple(PurePosixPath(m.name).parts), m.name, m.isdir()) for m in tf.getmembers() if m.isfile() or m.isdir())
    else:
        raise ValueError("Choose a .tar.gz backup or an extracted directory.")
    return _discover_entries((parts, str(p), p.is_dir()) for parts, p in entries)


def _discover_entries(entries):
    result: dict[str, dict] = {}
    all_entries = list(entries)
    for parts, name, is_dir in all_entries:
        root = _root_parts(parts)
        if root is not None and not is_dir:
            account = _account_name(root)
            info = result.setdefault(account, {"root": root, "storage": [], "folders": set()})
            info["storage"].append(name)
    for parts, _, _ in all_entries:
        for info in result.values():
            root = info["root"]
            if parts[: len(root) + 1] == root + ("mailboxes",) and len(parts) > len(root) + 1:
                tail = parts[len(root) + 1 :]
                if tail[0] != "dovecot-acl-list":
                    folder = tail[:-1] if tail[-1] == "dbox-Mails" else tail
                    if "dbox-Mails" in folder:
                        folder = folder[: folder.index("dbox-Mails")]
                    if folder and not folder[0].startswith("dovecot"):
                        info["folders"].add("/".join(folder))
    return result


def records(stream, source: str):
    """Yield complete message records, validating framing and gzip streams."""
    first = stream.readline(200)
    if not first.startswith(b"2 "):
        raise ValueError(f"{source}: unsupported dbox storage header")
    while True:
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
        if match[1] == b"N":
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = decompressor.decompress(payload, MAX_RECORD)
            if not decompressor.eof or decompressor.unused_data:
                raise ValueError(f"{source}: incomplete or invalid gzip message")
        else:
            raw = payload
        if stream.read(len(FOOTER)) != FOOTER:
            raise ValueError(f"{source}: missing message footer")
        attributes = {}
        while True:
            field = stream.readline(2048)
            if not field or field in (b"\n", b"\r\n"):
                break
            if field[:1] in (b"B", b"R", b"G", b"V", b"Z"):
                attributes[field[:1]] = field[1:].strip().decode("utf-8", "replace")
        yield Record(attributes.get(b"B") or "Unfiled", raw, source)


def read_account(path: Path, info: dict):
    """Open only selected storage members; never extract tar paths to disk."""
    if path.is_dir():
        for name in sorted(info["storage"]):
            with open(name, "rb") as stream:
                yield from records(stream, name)
    else:
        with tarfile.open(path, "r:gz") as tf:
            for name in sorted(info["storage"]):
                member = tf.getmember(name)
                if not member.isfile():
                    continue
                stream = tf.extractfile(member)
                if stream is None:
                    continue
                with stream:
                    yield from records(stream, name)
