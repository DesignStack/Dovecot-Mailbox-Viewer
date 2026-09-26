"""Assemble a reproducible x86-64 AppImage from the PyInstaller directory."""

from pathlib import Path
from urllib.request import urlopen
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_assets import copy_notices, download_source
from viewer.version import __version__

RUNTIME_COMMIT = '75849dce7cc37e4319b633df1f116ca895c71a12'
TOOLS = {
    'appimagetool': ('https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage',
                    'a6d71e2b6cd66f8e8d16c37ad164658985e0cf5fcaa950c90a482890cb9d13e0'),
    'runtime': ('https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64',
                '1cc49bcf1e2ccd593c379adb17c9f85a36d619088296504de95b1d06215aebbf'),
}
SOURCES = [
    (f'https://github.com/AppImage/type2-runtime/archive/{RUNTIME_COMMIT}.tar.gz',
     f'appimage-runtime-src-{RUNTIME_COMMIT}.tar.gz', 'b7af4960da4b90364e935a3281d04fad6560da4813c012414fa2f738291ad443'),
    ('https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz',
     'fuse-src-3.15.0.tar.xz', '70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0'),
    ('https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz',
     'squashfuse-src-0.5.2.tar.gz', 'db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3'),
]


def zip_notices():
    notices = ROOT / 'build/notices'
    with zipfile.ZipFile(ROOT / 'dist/Third-party-notices.zip', 'w', zipfile.ZIP_DEFLATED) as output:
        for path in sorted(notices.rglob('*')):
            if path.is_file():
                output.write(path, path.relative_to(notices).as_posix())


def prepare():
    tools = ROOT / 'build/appimage-tools'
    tools.mkdir(parents=True, exist_ok=True)
    for name, (url, checksum) in TOOLS.items():
        download_source(url, tools / name, checksum)
        (tools / name).chmod(0o755)
    notices = ROOT / 'build/notices'
    manifest_path = notices / 'DEPENDENCIES.json'
    manifest = json.loads(manifest_path.read_text())
    for url, name, checksum in SOURCES:
        item = download_source(url, ROOT / 'dist' / name, checksum)
        copy_notices(ROOT / 'dist' / name, notices / 'AppImage-runtime')
        manifest['sources'].append(item)
    manifest['appimage_runtime'] = {'commit': RUNTIME_COMMIT, 'sha256': TOOLS['runtime'][1]}
    manifest['appimagetool'] = {'sha256': TOOLS['appimagetool'][1]}
    # Retain notices for the runtime's permissively licensed support libraries.
    for name, url in {
        'mimalloc-LICENSE.txt': 'https://raw.githubusercontent.com/microsoft/mimalloc/main3/LICENSE',
        'musl-COPYRIGHT.txt': 'https://git.musl-libc.org/cgit/musl/plain/COPYRIGHT',
        'zstd-LICENSE.txt': 'https://raw.githubusercontent.com/facebook/zstd/dev/LICENSE',
        'zlib-LICENSE.txt': 'https://raw.githubusercontent.com/madler/zlib/develop/LICENSE',
    }.items():
        with urlopen(url, timeout=60) as response:
            (notices / 'AppImage-runtime' / name).write_bytes(response.read())
    manifest['target'] = 'Linux x86-64; glibc 2.35 or newer; X11/XWayland desktop'
    # Preserve the copyright notices for system libraries selected by the spec.
    copyrights = notices / 'Linux-system-libraries'
    copyrights.mkdir(exist_ok=True)
    for package in ('libstdc++6', 'libgcc-s1', 'libxcb-cursor0', 'libxcb-icccm4', 'libxcb-image0',
                    'libxcb-keysyms1', 'libxcb-render-util0', 'libxcb-util1', 'libxcb-xinerama0',
                    'libxcb-xkb1', 'libxkbcommon-x11-0'):
        source = Path('/usr/share/doc') / package / 'copyright'
        if not source.is_file():
            raise RuntimeError(f'Missing system library notice: {package}')
        shutil.copyfile(source, copyrights / f'{package}.txt')
    common = Path('/usr/share/common-licenses')
    for name in ('GPL-3', 'LGPL-2.1', 'LGPL-3'):
        shutil.copyfile(common / name, copyrights / f'{name}.txt')
    manifest['system_packages'] = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Package}=${Version}\n', 'libstdc++6', 'libgcc-s1', 'libxcb*', 'libxkbcommon*'],
        text=True).splitlines()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    (notices / 'APPIMAGE-REBUILDING.txt').write_text(
        'The AppImage type-2 runtime is MIT licensed and statically links libfuse (LGPLv2.1),\n'
        'squashfuse and its supporting libraries. Runtime and library source archives accompany\n'
        'this release. The runtime source contains BUILD.md, build scripts and the libfuse patch.\n'
        'To replace libfuse, rebuild the runtime with your changes using those instructions,\n'
        'then pass it to appimagetool --runtime-file when rebuilding the AppImage.\n'
        'There is no signature or activation check preventing modified versions from running.\n'
        'The Linux package uses the system glibc and desktop libraries; see docs/linux.md.\n')
    zip_notices()


def package():
    appdir = ROOT / 'build/Dovecot-Mailbox-Viewer.AppDir'
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(ROOT / 'dist/Dovecot-Mailbox-Viewer-Linux', appdir / 'usr/bin', symlinks=True)
    shutil.copyfile(ROOT / 'packaging/dovecot-mailbox-viewer.desktop', appdir / 'dovecot-mailbox-viewer.desktop')
    shutil.copyfile(ROOT / 'assets/dovecot-mailbox-viewer.svg', appdir / 'dovecot-mailbox-viewer.svg')
    (appdir / 'AppRun').write_text(
        '#!/bin/sh\nset -eu\napp_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        'unset PYTHONHOME PYTHONPATH\n'
        'export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"\n'
        'exec "$app_dir/usr/bin/Dovecot-Mailbox-Viewer" "$@"\n')
    (appdir / 'AppRun').chmod(0o755)
    tools = ROOT / 'build/appimage-tools'
    environment = {**os.environ, 'ARCH': 'x86_64', 'VERSION': __version__, 'APPIMAGE_EXTRACT_AND_RUN': '1'}
    output = ROOT / 'dist/Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage'
    subprocess.run([str(tools / 'appimagetool'), '--runtime-file', str(tools / 'runtime'),
                    '--no-appstream', str(appdir), str(output)], env=environment, check=True)
    output.chmod(0o755)


if __name__ == '__main__':
    {'prepare': prepare, 'package': package}[sys.argv[1]]()
