"""Spawn a linear process tree of `depth` levels and record every pid.

Used by the integration test that proves `kill_tree` leaves nothing behind.
Each level waits briefly before spawning the next one, so the supervisor has
time to attach the Job Object (Windows) or process group (POSIX) first.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time


def main() -> None:
    depth = int(sys.argv[1])
    out = sys.argv[2]
    with open(out, "a", encoding="utf-8") as handle:
        handle.write(f"{os.getpid()}\n")
        handle.flush()
    time.sleep(0.6)
    if depth > 1:
        subprocess.Popen(  # noqa: S603
            [sys.executable, __file__, str(depth - 1), out],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    while True:
        time.sleep(0.25)


if __name__ == "__main__":
    main()
