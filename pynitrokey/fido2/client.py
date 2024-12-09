# -*- coding: utf-8 -*-
#
# Copyright 2019 SoloKeys Developers
#
# Licensed under the Apache License, Version 2.0, <LICENSE-APACHE or
# http://apache.org/licenses/LICENSE-2.0> or the MIT license <LICENSE-MIT or
# http://opensource.org/licenses/MIT>, at your option. This file may not be
# copied, modified, or distributed except according to those terms.

import base64
import binascii
import hashlib
import json
import secrets
import struct
import sys
import tempfile
import time
from typing import Any, Callable, List, Optional, Tuple, Union

from fido2.client import Fido2Client
from fido2.cose import ES256, EdDSA
from fido2.ctap import CtapError
from fido2.ctap1 import Ctap1
from fido2.ctap2.base import Ctap2
from fido2.ctap2.pin import ClientPin
from fido2.hid import CTAPHID, CtapHidDevice, open_device
from fido2.webauthn import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialCreationOptions,
    PublicKeyCredentialParameters,
    PublicKeyCredentialRpEntity,
    PublicKeyCredentialType,
    PublicKeyCredentialUserEntity,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from intelhex import IntelHex

import pynitrokey.exceptions
import pynitrokey.fido2 as nkfido2
from pynitrokey import helpers
from pynitrokey.fido2.commands import SoloBootloader, SoloExtension
from pynitrokey.helpers import local_critical, local_print


class NKFido2Client:
    def __init__(self) -> None:
        self.origin = "https://example.org"
        self.host = "example.org"
        self.user_id = b"they"
        self.exchange = self.exchange_hid
        self.do_reboot = True

    def use_u2f(self) -> None:
        self.exchange = self.exchange_u2f

    def use_hid(self) -> None:
        self.exchange = self.exchange_hid

    def set_reboot(self, val: bool) -> None:
        """option to reboot after programming"""
        self.do_reboot = val

    def reboot(self) -> None:
        """option to reboot after programming"""
        try:
            self.exchange(SoloBootloader.reboot)
        except OSError:
            pass

    def find_device(
        self,
        dev: Optional[CtapHidDevice] = None,
        solo_serial: Optional[str] = None,
        pin: Optional[str] = None,
    ) -> CtapHidDevice:

        devices = []
        found_dev: Optional[CtapHidDevice] = None

        if dev is None:
            if solo_serial is not None:
                if solo_serial.startswith("device="):
                    solo_serial = solo_serial.split("=")[1]
                    found_dev = open_device(solo_serial)
                else:
                    devices = list(CtapHidDevice.list_devices())
                    devices = [
                        d for d in devices if d.descriptor.serial_number == solo_serial
                    ]
            else:
                devices = list(CtapHidDevice.list_devices())
            if len(devices) > 1:
                raise pynitrokey.exceptions.NonUniqueDeviceError
            if len(devices) > 0:
                found_dev = devices[0]
        if dev is None and found_dev is None:
            raise RuntimeError("No FIDO device found")

        self.dev = found_dev if found_dev else dev
        assert isinstance(self.dev, CtapHidDevice)
        self.ctap1 = Ctap1(self.dev)

        try:
            self.ctap2: Optional[Ctap2] = Ctap2(self.dev)
        except CtapError as e:
            self.ctap2 = None

        if self.exchange == self.exchange_hid:
            self.send_data_hid(CTAPHID.INIT, b"\x11\x11\x11\x11\x11\x11\x11\x11")

        return self.dev

    @staticmethod
    def format_request(cmd: int, addr: int = 0, data: bytes = b"A" * 16) -> bytes:
        # not sure why this is here?
        # arr = b"\x00" * 9
        packed_addr = struct.pack("<L", addr)
        packed_cmd = struct.pack("B", cmd)
        length = struct.pack(">H", len(data))

        return packed_cmd + packed_addr[:3] + SoloBootloader.TAG + length + data

    def send_only_hid(self, cmd: int, data: bytes = b"A" * 16) -> None:
        if not isinstance(data, bytes):
            data = struct.pack("%dB" % len(data), *[ord(x) for x in data])
        assert isinstance(self.dev, CtapHidDevice)
        self.dev.call(0x80 | cmd, bytearray(data))

    def send_data_hid(self, cmd: int, data: bytes = b"A" * 16) -> bytes:
        if not isinstance(data, bytes):
            data = struct.pack("%dB" % len(data), *[ord(x) for x in data])
        with helpers.Timeout(1.0) as event:
            assert isinstance(self.dev, CtapHidDevice)
            return self.dev.call(cmd, data, event=event)

    def exchange_hid(self, cmd: int, addr: int = 0, data: bytes = b"A" * 16) -> bytes:
        req = NKFido2Client.format_request(cmd, addr, data)

        data = self.send_data_hid(SoloBootloader.HIDCommandBoot, req)

        ret = data[0]
        if ret != CtapError.ERR.SUCCESS:
            raise CtapError(ret)

        return data[1:]

    def exchange_u2f(self, cmd: int, addr: int = 0, data: bytes = b"A" * 16) -> bytes:
        appid = b"A" * 32
        chal = b"B" * 32

        req = NKFido2Client.format_request(cmd, addr, data)

        res = self.ctap1.authenticate(chal, appid, req)

        ret = res.signature[0]
        if ret != CtapError.ERR.SUCCESS:
            raise CtapError(ret)

        return res.signature[1:]

    def exchange_fido2(self, cmd: int, addr: int = 0, data: bytes = b"A" * 16) -> bytes:
        chal = b"B" * 32

        req = NKFido2Client.format_request(cmd, addr, data)

        assert isinstance(self.ctap2, Ctap2)
        assertion = self.ctap2.get_assertion(
            self.host, chal, [{"id": req, "type": "public-key"}]
        )

        res = assertion
        ret = res.signature[0]
        if ret != CtapError.ERR.SUCCESS:
            raise RuntimeError("Device returned non-success code %02x" % (ret,))

        return res.signature[1:]

    def bootloader_version(self) -> Tuple[int, int, int]:
        data = self.exchange(SoloBootloader.version)
        if len(data) > 2:
            return (data[0], data[1], data[2])
        return (0, 0, data[0])

    def solo_version(self) -> Union[bytes, Tuple[int, int, int]]:
        try:
            return self.send_data_hid(0x61, b"")
        except CtapError:
            data = self.exchange(SoloExtension.version)
            return (data[0], data[1], data[2])

    def write_flash(self, addr: int, data: bytes) -> None:
        self.exchange(SoloBootloader.write, addr, data)

    def boot_pubkey(self) -> bytes:
        return self.exchange(SoloBootloader.boot_pubkey)

    def get_rng(self, num: int = 0) -> bytes:
        ret = self.send_data_hid(SoloBootloader.HIDCommandRNG, struct.pack("B", num))
        return ret

    def get_status(self, num: int = 0) -> bytes:
        ret = self.send_data_hid(SoloBootloader.HIDCommandStatus, struct.pack("B", num))
        # print(ret[:8])
        return ret

    def verify_flash(self, sig: bytes) -> None:
        """
        Tells device to check signature against application.  If it passes,
        the application will boot.
        Exception raises if signature fails.
        """
        self.exchange(SoloBootloader.done, 0, sig)

    def wink(self) -> None:
        self.send_data_hid(CTAPHID.WINK, b"")

    def reset(self) -> None:
        assert isinstance(self.ctap2, Ctap2)
        self.ctap2.reset()

    def enter_bootloader(self) -> None:
        """
        If Nitrokey is configured as Nitrokey hacker or something similar,
        this command will tell the token to boot directly to the bootloader
        so it can be reprogrammed
        """
        if self.exchange != self.exchange_hid:
            self.send_data_hid(CTAPHID.INIT, b"\x11\x11\x11\x11\x11\x11\x11\x11")
        self.send_data_hid(SoloBootloader.HIDCommandEnterBoot, b"")

    def enter_bootloader_or_die(self) -> None:
        try:
            self.enter_bootloader()
        # except OSError:
        #     pass
        except CtapError as e:
            if e.code == CtapError.ERR.INVALID_COMMAND:
                print(
                    "Could not switch into bootloader mode.  Please hold down the button for 2s while you plug token in."
                )
                sys.exit(1)
            else:
                raise (e)

    def is_bootloader(self) -> bool:
        try:
            self.bootloader_version()
            return True
        except CtapError as e:
            if e.code == CtapError.ERR.INVALID_COMMAND:
                pass
            else:
                raise (e)
        except Exception as e:
            # exception during bootloader version check, assume no bootloader
            # local_print("could not get bootloader version")
            pass
        return False

    def program_file(self, name: str) -> bytes:
        def parseField(f: str) -> bytes:
            return base64.b64decode(helpers.from_websafe(f).encode())

        def isCorrectVersion(current: Tuple[int, int, int], target: str) -> bool:
            """current is tuple (x,y,z).  target is string '>=x.y.z'.
            Return True if current satisfies the target expression.
            """
            if "=" in target:
                target_toks = target.split("=")
                assert target_toks[0] in [">", "<"]
                target_num_toks = [int(x) for x in target_toks[1].split(".")]
                assert len(target_num_toks) == 3
                comp = target_toks[0] + "="
            else:
                assert target[0] in [">", "<"]
                target_num_toks = [int(x) for x in target[1:].split(".")]
                comp = target[0]
            target_num = (
                (target_num_toks[0] << 16)
                | (target_num_toks[1] << 8)
                | (target_num_toks[2] << 0)
            )
            current_num = (current[0] << 16) | (current[1] << 8) | (current[2] << 0)
            return eval(str(current_num) + comp + str(target_num))  # type: ignore [no-any-return]

        firmware_file_data = None
        if name.lower().endswith(".json"):
            firmware_file_data = json.loads(open(name, "r").read())
            fw = parseField(firmware_file_data["firmware"])
            sig = None

            if "versions" in firmware_file_data:
                current = (0, 0, 0)
                try:
                    current = self.bootloader_version()
                except CtapError as e:
                    if e.code == CtapError.ERR.INVALID_COMMAND:
                        pass
                    else:
                        raise (e)
                # for v in firmware_file_data["versions"]:
                #     if not isCorrectVersion(current, v):
                #         print("using signature version", v)
                #         sig = parseField(firmware_file_data["versions"][v]["signature"])
                #         break
                sig = parseField(firmware_file_data["versions"][">2.5.3"]["signature"])

                if sig is None:
                    raise RuntimeError(
                        "Improperly formatted firmware file.  Could not match version."
                    )
            else:
                sig = parseField(firmware_file_data["signature"])

            ih = IntelHex()
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(fw)
            tmp.seek(0)
            tmp.close()
            ih.fromfile(tmp.name, format="hex")
        else:
            if not name.lower().endswith(".hex"):
                print('Warning, assuming "%s" is an Intel Hex file.' % name)
            sig = None
            ih = IntelHex()
            ih.fromfile(name, format="hex")

        if self.exchange == self.exchange_hid:
            chunk = 2048
        else:
            chunk = 240

        seg = ih.segments()[0]
        size = seg[1] - seg[0]
        total = 0
        t1 = time.time() * 1000
        print("erasing firmware...")
        for i in range(seg[0], seg[1], chunk):
            s = i
            ext = min(i + chunk, seg[1])
            data = ih.tobinarray(start=i, size=ext - s)
            self.write_flash(i, data)
            total += chunk
            progress = total / float(size) * 100
            sys.stdout.write("updating firmware %.2f%%...\r" % progress)
        sys.stdout.write("updated firmware 100%             \r\n")
        t2 = time.time() * 1000
        print("time: %.2f s" % ((t2 - t1) / 1000.0))

        if sig is None:
            sig = b"A" * 64

        success = False
        if self.do_reboot:
            try:
                print("bootloader is verifying signature...")
                print(f"Trying with {sig.hex()}")
                self.verify_flash(sig)
                print("...pass!")
                success = True
            except CtapError as e:
                if e.code != 0x27:
                    raise
                print("...error!")

        if not success:
            msg = """Bootloader reports failure in the signature verification. If your device is staying in the
            bootloader mode after reinserting it into the USB port, please execute the following to make it work again:

            # Download the compressed firmware file for Nitrokey FIDO2 128 v2.4.1 and extract it
            wget https://github.com/Nitrokey/nitrokey-fido2-firmware/releases/download/2.4.1.nitrokey/nitrokey-fido2-firmware-2.4.1-128kB-app-signed.zip
            unzip nitrokey-fido2-firmware-2.4.1-128kB-app-signed.zip
            # Run the update process again with the just downloaded firmware
            nitropy fido2 util program bootloader nitrokey-fido2-firmware-2.4.1-128kB-app-signed.json
            """
            local_critical(msg, support_hint=False)

        return sig

    def check_only(self, name: str) -> None:
        # FIXME refactor
        # copy from program_file
        if name.lower().endswith(".json"):
            data = json.loads(open(name, "r").read())
            fw = base64.b64decode(helpers.from_websafe(data["firmware"]).encode())
            sig = base64.b64decode(helpers.from_websafe(data["signature"]).encode())
            ih = IntelHex()
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp.write(fw)
            tmp.seek(0)
            tmp.close()
            ih.fromfile(tmp.name, format="hex")
        else:
            if not name.lower().endswith(".hex"):
                print('Warning, assuming "%s" is an Intel Hex file.' % name)
            sig = None
            ih = IntelHex()
            ih.fromfile(name, format="hex")

        if sig is None:
            sig = b"A" * 64

        if self.do_reboot:
            self.verify_flash(sig)
