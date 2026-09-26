"""Latest import activity, shared without queueing thousands of GUI events."""
import logging
import threading
import time


def size_text(value):
    for suffix in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or suffix == 'TB':
            return f'{value:.1f} {suffix}'
        value /= 1024


def duration_text(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 60}m {seconds % 60:02d}s' if seconds >= 60 else f'{seconds}s'


class ImportProgress:
    """The GUI polls a copy; disk reads never enqueue a signal per chunk."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        now = clock()
        self.data = dict(phase='Preparing backup', done=0, total=0, unit='bytes',
                         detail='', messages=0, available=0, started=now,
                         phase_started=now, advanced=now, log_time=now)

    def phase(self, text, total=0, unit='bytes'):
        now = self.clock()
        with self.lock:
            self.data.update(phase=text, done=0, total=total, unit=unit,
                             detail='', phase_started=now, advanced=now, log_time=now)
        logging.getLogger('viewer').info('Import phase: %s', text)

    def update(self, done=None, total=None, unit=None, detail=None, messages=None, available=None):
        now = self.clock()
        with self.lock:
            values = dict(done=done, total=total, unit=unit, detail=detail,
                          messages=messages, available=available)
            changed = any(value is not None and self.data[key] != value for key, value in values.items())
            self.data.update({key: value for key, value in values.items() if value is not None})
            if changed:
                self.data['advanced'] = now
            should_log = now - self.data['log_time'] >= 5
            if should_log:
                self.data['log_time'] = now
            snapshot = dict(self.data)
        if should_log:
            logging.getLogger('viewer').info('Import activity: %s', format_progress(snapshot, now)[1])

    def snapshot(self):
        with self.lock:
            return dict(self.data)


def format_progress(data, now=None):
    """Percentages and estimates describe the named stage, not the whole import."""
    now = time.monotonic() if now is None else now
    elapsed = max(0, now - data['started'])
    stage_time = max(0, now - data['phase_started'])
    idle = max(0, now - data['advanced'])
    done, total = data['done'], data['total']
    percent = min(99, int(100 * done / total)) if total > 0 else None
    amount = size_text(done) if data['unit'] == 'bytes' else f'{done:,} {data["unit"]}'
    if total > 0:
        amount += ' / ' + (size_text(total) if data['unit'] == 'bytes' else f'{total:,}')
    parts = [data['phase'] + (f' · {percent}%' if percent is not None else ''), amount,
             f'{data["messages"]:,} emails', f'Elapsed {duration_text(elapsed)}']
    rate = done / stage_time if stage_time else 0
    if rate and data['unit'] == 'bytes':
        parts.append(size_text(rate) + '/s')
    if total > done > 0 and stage_time >= 3 and idle < 10:
        parts.append('Stage remaining ~' + duration_text((total - done) / rate))
    if data['detail']:
        parts.append(data['detail'])
    if idle >= 10:
        parts.append(f'No measurable progress for {duration_text(idle)} — may be waiting for disk or processing a large email')
    detail = '\n'.join(parts)
    short = ' · '.join(parts[:4])
    if idle >= 10:
        short = f'No progress for {duration_text(idle)} · ' + short
    return percent, detail, short
