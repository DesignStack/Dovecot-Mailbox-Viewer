"""Local reading preferences. Remote image consent always stays per message."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QComboBox, QCheckBox, QDialogButtonBox, QLabel, QSpinBox
from viewer.catalog import SORTS

DEFAULTS = dict(sort='date_desc', page_size=200, conversations=False, highlight=True,
                plain_text=False, zoom=100, remember_layout=True, remember_recent=True)


def read_preferences(settings):
    result = {}
    for key, default in DEFAULTS.items():
        value = settings.value('preferences/' + key, default)
        if isinstance(default, bool):
            value = str(value).lower() in ('true', '1')
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = default
        result[key] = value
    if result['sort'] not in SORTS:
        result['sort'] = 'date_desc'
    if result['page_size'] not in (100, 200, 500):
        result['page_size'] = 200
    result['zoom'] = max(60, min(200, result['zoom']))
    return result


def write_preferences(settings, values):
    for key in DEFAULTS:
        settings.setValue('preferences/' + key, values[key])
    settings.sync()


class SettingsDialog(QDialog):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setMinimumWidth(420)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.sort = QComboBox()
        for key, (label, _) in SORTS.items():
            self.sort.addItem(label, key)
        self.sort.setCurrentIndex(self.sort.findData(values['sort']))
        self.page = QComboBox()
        for size in (100, 200, 500):
            self.page.addItem(str(size), size)
        self.page.setCurrentIndex(self.page.findData(values['page_size']))
        self.zoom = QSpinBox()
        self.zoom.setRange(60, 200)
        self.zoom.setSingleStep(10)
        self.zoom.setSuffix('%')
        self.zoom.setValue(values['zoom'])
        form.addRow('Default sorting', self.sort)
        form.addRow('Emails per page', self.page)
        form.addRow('Reading zoom', self.zoom)
        self.checks = {}
        for key, label in [('conversations', 'Group messages into conversations'), ('highlight', 'Highlight search matches'),
                           ('plain_text', 'Prefer plain text emails'), ('remember_layout', 'Remember window and panel sizes'),
                           ('remember_recent', 'Remember recently opened backups')]:
            box = QCheckBox(label)
            box.setChecked(values[key])
            self.checks[key] = box
            form.addRow(box)
        outer.addLayout(form)
        note = QLabel('Remote images stay blocked until you choose Download images for an email.\n'
                      'All preferences are stored on this computer.')
        note.setWordWrap(True)
        outer.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self):
        return dict(sort=self.sort.currentData(), page_size=self.page.currentData(), zoom=self.zoom.value(),
                    **{key: box.isChecked() for key, box in self.checks.items()})
