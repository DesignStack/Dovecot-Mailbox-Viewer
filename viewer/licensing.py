"""Read the licences shipped inside the portable executable, without networking."""

from pathlib import Path
import sys
import zipfile

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


def notice_bundle():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "notices/Third-party-notices.zip"
    return Path(__file__).resolve().parents[1] / "dist/Third-party-notices.zip"


def show_licences(parent):
    bundle = notice_bundle()
    root = Path(__file__).resolve().parents[1]
    if bundle.is_file():
        with zipfile.ZipFile(bundle) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
    else:
        names = ["LICENSE", "THIRD_PARTY_NOTICES.md"]
    names.sort(key=lambda name: (name != "README.txt", name))
    dialog = QDialog(parent)
    dialog.setWindowTitle("Licences and third-party software")
    dialog.resize(820, 600)
    layout = QVBoxLayout(dialog)
    selector = QComboBox()
    selector.addItems(names)
    content = QPlainTextEdit()
    content.setReadOnly(True)
    def select(index):
        try:
            if bundle.is_file():
                with zipfile.ZipFile(bundle) as archive:
                    text = archive.read(names[index]).decode("utf-8", errors="replace")
            else:
                text = (root / names[index]).read_text(encoding="utf-8", errors="replace")
            content.setPlainText(text)
        except (OSError, zipfile.BadZipFile, KeyError):
            content.setPlainText("This notice could not be read. See THIRD_PARTY_NOTICES.md in the source repository.")
    selector.currentIndexChanged.connect(select)
    select(0)
    layout.addWidget(selector)
    layout.addWidget(content)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.exec()
