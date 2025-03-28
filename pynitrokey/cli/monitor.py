# Copyright 2019 SoloKeys Developers
# Copyright Nitrokey GmbH
# SPDX-License-Identifier: Apache-2.0 OR MIT

import sys
import time

import click
import serial


@click.command()
@click.argument("serial_port")
def monitor(serial_port: str) -> None:
    """Reads Nitrokey  serial output from USB serial port SERIAL_PORT.

    SERIAL-PORT is something like /dev/ttyACM0 or COM10.
    Automatically reconnects. Baud rate is 115200.
    """

    ser = None
    while True:
        try:
            ser = serial.Serial(serial_port, 115200, timeout=0.05)
            break
        except KeyboardInterrupt:
            exit(1)
        except:
            sys.stdout.buffer.write(b".")
            sys.stdout.flush()
        time.sleep(0.5)

    def reconnect() -> serial.Serial:
        while True:
            time.sleep(0.5)
            try:
                ser = serial.Serial(serial_port, 115200, timeout=0.05)
                return ser
            except serial.SerialException:
                sys.stdout.buffer.write(b".")
                sys.stdout.flush()
                pass

    t0 = time.time()
    while True:
        try:
            data = ser.read(1)
            if b"\n" in data:
                t1 = time.time()
                times = "{0:5.3f}".format(t1 - t0)
                data = f"{data.decode()} {times} ".encode()
            sys.stdout.buffer.write(data)
        except KeyboardInterrupt:
            print("\nClosing")
            ser.close()
            return
        except serial.SerialException:
            sys.stdout.buffer.write(b".\n")
            print("reconnecting...")
            # ser = reconnect()
            # ser.close()
            time.sleep(1.0)
            ser = reconnect()
            print("done")
        sys.stdout.flush()
