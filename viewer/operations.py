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
    def __init__(self, stream, check):
        self.stream, self.check = stream, check

    def read(self, size=-1):
        self.check()
        return self.stream.read(size)
