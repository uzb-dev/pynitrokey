#! /usr/bin/env python3

# Copyright Nitrokey GmbH
# SPDX-License-Identifier: Apache-2.0 OR MIT

import logging
import subprocess
import threading
import time
from sys import stderr
from typing import Iterable, List, Optional


class ThreadLog(threading.Thread):
    _dmesg_skip_strings = []  # type: ignore

    _write_to_log = False

    def __init__(self, logger: logging.Logger, command: str):
        threading.Thread.__init__(self)
        self.finished = False
        self.logger = logger
        self.command = command
        self.daemon = True
        self.start()
        self.process: Optional[subprocess.Popen[bytes]] = None

    def run(self):
        self.execute(self.command.split())

    @staticmethod
    def _contains(value: str, strings: Iterable[str]) -> bool:
        for s in strings:
            if s in value:
                return True
        return False

    def execute(self, command: List[str]):
        self.process = subprocess.Popen(
            command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )

        # Poll process for new output until finished
        for line in iter(self.process.stdout.readline, ""):  # type: ignore
            if self.finished:
                break
            if not line or self.finished or not self._write_to_log:
                continue
            if self._contains(line, self._dmesg_skip_strings):
                continue
            self.logger.debug(line.strip())

        self.process.wait()
        self.logger.debug("Finished")

    def start_logging(self) -> None:
        self._write_to_log = True

    def __enter__(self):
        time.sleep(1)
        self.start_logging()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.logger.debug("Finishing")
        self.finished = True
        self.process.kill()
        self.process.wait()
        self.join(10)


def test_run() -> None:
    FORMAT = "%(relativeCreated)05d [%(process)x] - %(levelname)s - %(name)s - %(message)s [%(filename)s:%(lineno)d]"
    logging.basicConfig(format=FORMAT, stream=stderr, level=logging.DEBUG)
    logger = logging.getLogger("threadlog")

    try:
        t = ThreadLog(logger, "dmesg -w")
        t.start_logging()
        time.sleep(10)
        t.finished = True
    except (KeyboardInterrupt, SystemExit) as k:
        return


if __name__ == "__main__":
    test_run()
