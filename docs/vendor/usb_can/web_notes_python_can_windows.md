# Web notes: python-can interfaces relevant on Windows

Collected 2026-10-01. Summaries of the python-can 4.6.1 documentation (LGPL-3.0 project; docs quoted briefly).

## Built-in interfaces (python-can 4.6.1)

URL: https://python-can.readthedocs.io/en/stable/interfaces.html

The built-in `interface=` names are: canalystii, cantact, etas, gs_usb, iscan, ixxat, kvaser, neousys, neovi,
nican, nixnet, pcan, robotell, seeedstudio, serial, slcan, socketcan (Linux only), socketcand, systec,
usb2can, vector, and also virtual and udp_multicast.

- The RobStride "AT" framing (`41 54 ... 0D 0A`) is **not** one of them. python-can's `serial` interface uses
  its own 0xAA-framed format, which is different.

## gs_usb (candleLight / Geschwister Schneider). Used by the RobStride USB-CANHUB (VID 1d50 / PID 606f)

URL: https://python-can.readthedocs.io/en/stable/interfaces/gs_usb.html

- Platforms: "Windows, Linux and Mac."
- Install: `pip install "python-can[gs-usb]"`. It needs a pyusb backend such as libusb. The docs say that on
  Windows "a tool such as Zadig can be used to set the USB device driver to `libusbK`".
- Usage: `can.Bus(interface="gs_usb", channel=0, bitrate=250000)`, or select by `bus=dev.bus, address=dev.address`.
- Supported bitrates: 10K, 20K, 50K, 83.333K, 100K, 125K, 250K, 500K, 800K, 1M.
- "Message filtering is not supported" (the device has no hardware filters).

## Plugin interfaces

URL: https://python-can.readthedocs.io/en/stable/plugin-interface.html

- `python-can-candle` (https://github.com/BIRLab/python-can-candle): "A full-featured driver for candleLight".
- `python-can-cvector`, `python-can-remote` (CAN over network bridge).
- A third-party interface registers through the `can.interface` entry point. We could publish our own
  RobStride-AT adapter the same way.

## slcan (CANable slcan firmware, many CH340/STM32 dongles)

URL: https://python-can.readthedocs.io/en/stable/interfaces/slcan.html

- The Windows channel looks like `COM4`. It can end with `@115200` to set the tty baudrate. The default
  `tty_baudrate` is 115200.
- `sleep_after_open` defaults to 2 s. The serial `timeout` defaults to 0.001 s.

## Python time.sleep on Windows

URL: https://docs.python.org/3/library/time.html#time.sleep

- "On Windows 10 and newer the implementation uses a high-resolution timer which provides resolution of 100
  nanoseconds." Changed in 3.11: "On Windows, a waitable timer is now used." Before 3.11, sleep granularity
  was about 1 to 15.6 ms.

## Serial latency background

URL: https://heavydeck.net/blog/measuring-serial-port-latency/

- Single-byte loopback on a PC: FTDI FT232RL is about 15-16 ms by default (latency timer 16 ms) and about 1 ms
  with the latency timer set to 1 ms. CP210x is about 8 ms. A motherboard COM port at 115200 is about 0.5 ms.
  No CH340 figure was published. We have to measure it ourselves.
