"""Combine checked platform artifacts without silently overwriting shared sources."""

from pathlib import Path
import hashlib
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.publish_release import verified_assets
from scripts.prepare_release import prepare


def combine():
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    (ROOT / 'build').mkdir(exist_ok=True)
    hashes = {}
    for platform in ('Windows', 'Linux'):
        folder = ROOT / 'artifacts' / platform
        checksums = (folder / 'dist/SHA256SUMS.txt').read_text().splitlines()
        for line in checksums:
            checksum, name = line.split('  ', 1)
            if Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('Invalid artifact name')
            if name in hashes and hashes[name] != checksum:
                raise ValueError(f'Conflicting platform source archive: {name}')
            hashes[name] = checksum
            source = folder / 'dist' / name
            # Linux does not re-upload the shared Qt/PySide archives.
            if source.is_file():
                with source.open('rb') as stream:
                    if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                        raise ValueError(f'Artifact checksum mismatch: {name}')
                if not (output / name).exists():
                    shutil.copyfile(source, output / name)
        shutil.copyfile(folder / 'build/smoke-test.json', ROOT / f'build/smoke-test-{platform}.json')
    (output / 'SHA256SUMS.txt').write_text(''.join(f'{value}  {name}\n' for name, value in hashes.items()))
    verified_assets(ROOT)
    prepare()


if __name__ == '__main__':
    combine()
