"""CLI stage messages and an elapsed-time spinner for interactive terminals."""

import os
import sys
from shutil import get_terminal_size
from threading import Event, Lock, Thread
from time import perf_counter


class Progress:
    """Report real stages without inventing a percentage for opaque operations."""

    def __init__(self, stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.interactive = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self._lock = Lock()
        self._stop = Event()
        self._thread = None
        self._message = None
        self._started = 0.0
        self._width = 0
        self._frame = 0

    def __enter__(self):
        if self.interactive:
            self._thread = Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def stage(self, message: str):
        """Finish the previous stage and immediately show the next operation."""
        with self._lock:
            self._complete("done")
            self._message = message
            self._started = perf_counter()
            self._frame = 0
            if self.interactive:
                self._draw()
            else:
                print(f"{message}...", file=self.stream, flush=True)

    def _draw(self):
        elapsed = perf_counter() - self._started
        suffix = f"... ({elapsed:.1f}s)"
        prefix = "|/-\\"[self._frame % 4] + " "
        columns = max(20, get_terminal_size().columns - 1)
        room = max(1, columns - len(prefix) - len(suffix))
        message = self._message
        if len(message) > room:
            message = message[:room - 1] + "…"
        line = prefix + message + suffix
        self.stream.write("\r" + line.ljust(self._width))
        self.stream.flush()
        self._width = len(line)
        self._frame += 1

    def _animate(self):
        while not self._stop.wait(0.1):
            with self._lock:
                if self._message is not None:
                    self._draw()

    def _complete(self, status):
        if self._message is None:
            return
        elapsed = perf_counter() - self._started
        if self.interactive:
            self.stream.write("\r" + " " * self._width + "\r")
            print(f"{self._message}: {status} ({elapsed:.2f}s)", file=self.stream, flush=True)
        else:
            print(f"  {status.capitalize()} ({elapsed:.2f}s)", file=self.stream, flush=True)
        self._message = None
        self._width = 0

    def __exit__(self, exc_type, exc, traceback):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            status = "interrupted" if exc_type is KeyboardInterrupt else "failed" if exc_type else "done"
            self._complete(status)
