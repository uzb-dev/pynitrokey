# Copyright Nitrokey GmbH
# SPDX-License-Identifier: Apache-2.0 OR MIT

from typing import Tuple

class CardConnection:
    def connect(self) -> None: ...
    def transmit(self, data: list[int]) -> Tuple[list[int], int, int]: ...
