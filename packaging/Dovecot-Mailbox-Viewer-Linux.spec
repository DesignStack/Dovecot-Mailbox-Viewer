# Keep the operating system's desktop libraries; bundle Python, Qt and the
# small XCB helpers not consistently installed on desktop distributions.
from pathlib import Path

root = Path(SPECPATH).parent
a = Analysis([str(root / 'viewer/app.py')], pathex=[str(root)],
             datas=[(str(root / 'dist/Third-party-notices.zip'), 'notices')],
             binaries=[], hiddenimports=[], hookspath=[], runtime_hooks=[], excludes=[])
a.exclude_system_libraries(['libstdc++*', 'libgcc_s*', 'libxcb*', 'libxkbcommon-x11*', 'libsqlite3*'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name='Dovecot-Mailbox-Viewer', debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Dovecot-Mailbox-Viewer-Linux')
