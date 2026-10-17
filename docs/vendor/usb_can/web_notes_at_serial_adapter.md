# Web notes: RobStride / CyberGear USB-CAN "AT" serial adapter

Collected 2026-10-01 for `docs/research/can_transport.md`. These are short summaries and quotes, not full
copies. Each section starts with its source URL. The original documents belong to their authors.

---

## 1. Kimzae/cybergear (GitHub, tutorial README)

URL: https://github.com/Kimzae/cybergear

- CH340 USB-CAN adapter, serial **921600 bps**, CAN **1 Mbps**.
- Frame (README, Korean): `'A' 'T' | ID 4 bytes | data length 1 byte | data 0~8 bytes | '\r' '\n'`
- ID encoding: `(29-bit CAN ID << 3) | 0x04`
- 29-bit ID layout: [28:24] communication type, [23:8] data area 2 (host ID / torque), [7:0] motor ID.
- The README documents no configuration commands (AT+...).

## 2. dawnmainak/Robstride_Control_Gui (GitHub, Python GUI)

URLs:
- https://github.com/dawnmainak/Robstride_Control_Gui
- https://raw.githubusercontent.com/dawnmainak/Robstride_Control_Gui/main/robstride_gui/transport.py
- https://raw.githubusercontent.com/dawnmainak/Robstride_Control_Gui/main/robstride_gui/protocol.py

No license file was visible when these notes were collected, so only the facts are summarized here:

- `DEFAULT_SERIAL_BAUD = 921600`.
- Encode: `b"AT" + uint32_be((ext_id << 3) | 0x04) + dlc + data + b"\r\n"`. The docstring says the encoding was
  "verified against the known-good frames in the vendor motor_zero.py".
- Decode: find `b"AT"`, read the 4-byte big-endian id field and the dlc, then `ext_id = id_field >> 3`. If
  dlc > 8 it resyncs, and it keeps any partial tail for the next read. It does **not** check the `\r\n` tail.
- Transport comments, quoted:
  - "Do NOT send an 'AT+AT' handshake: on the official RobStride USB-CAN module that command switches it into
    AT *command* mode, which stops it forwarding CAN frames"
  - "The module powers up ready to pass 'AT'-framed CAN frames through, exactly like the vendor GUI, which
    sends no handshake. Just clear any stale bytes."
  - "CH340 USB-serial adapters intermittently raise 'device reports readiness to read but returned no data'
    while the motor draws current. It is a transient glitch, not a disconnect: retry within the deadline"
- The GUI has a `Transport` abstraction with a `SerialATTransport` and a `SocketCANTransport`.

## 3. Generic "USB转CAN" AT-command modules (YourCee / WitMotion-like), secondary info

Search-result snippets. The CSDN pages themselves returned HTTP 521, so these points are unverified:

- https://blog.csdn.net/weixin_44024557/article/details/117674473
- https://blog.csdn.net/qq_42250136/article/details/134357324

- On these generic modules, `AT+CG` enters configuration mode, `AT+USART_PARAM` queries/sets the UART, and
  `AT+AT` returns to "AT mode" for sending CAN frames.
- For 1 Mbps CAN they recommend a UART rate of 921600 or 1M to avoid losses.
- **Caution:** item 2 reports that `AT+AT` stops forwarding on the RobStride module. The command set of the
  RobStride module is not officially documented. Do not send configuration commands from our tool.

## 4. Product pages (RobStride USB-CAN module)

- https://www.seeedstudio.com/Robostride-CAN-USB-Driver-Board-p-6708.html : "Powered by a GD32 MCU and CH340
  driver", USB Type-C, 3-pin terminal block, supports the official control software.
- https://openelab.io/products/robstride-can-to-usb-debugging-module : "CH340 for USB-to-serial conversion",
  "GD32 series microcontroller", "Supports high-speed UART transfer up to 921600 bps", "AT command mode",
  "firmware OTA upgrade".

## 5. CyberGear lineage

- https://github.com/belovictor/cybergear-docs/blob/main/instructionmanual/instructionmanual.md (a translation
  of the Xiaomi CyberGear manual) says: "The can to USB tool recommends using YourCee's USB-CAN module. The frame
  header corresponding to the serial port protocol is 41 54 and the frame tail is 0D 0A." It uses the same
  29-bit ID bit fields (`id:8, data:16, mode:5, res:3`) as RobStride.
- https://github.com/jgillick/python-cybergear (MIT) has no built-in transport. Its example uses python-can
  `interface='slcan', bitrate=1000000`.
