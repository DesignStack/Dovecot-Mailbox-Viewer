"""Cooperative cancellation shared by archive discovery and indexing."""
from threading import Event


class Cancelled(Exception):
    """The user stopped a background operation; this is not an import error."""


class Cancellation:
    def __init__(self):
        self.event = Event()

    def cancel(self):
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise Cancelled()


class CheckedReader:
    """Check cancellation during tarfile's decompression/skip reads too."""
    def __init__(self, stream, check, progress=None, total=0):
        self.stream, self.check = stream, check
        self.progress, self.total, self.done = progress, total, 0

    def read(self, size=-1):
        self.check()
        data = self.stream.read(size)
        self.done += len(data)
        if self.progress:
            self.progress(self.done, self.total)
        return data

