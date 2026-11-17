"""OS-enforced single-worker ownership while actions are process-local."""

import os


class ProcessLease:
    def __init__(self, path):
        self.file = open(path, "a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("JARVIS runtime already owned by another process; use one worker") from None

    def close(self):
        self.file.close()
