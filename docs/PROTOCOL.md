# ATEN KVM protocol (technical reference)

This document describes the protocol that `webkvm` speaks with the KVM console of Supermicro
X9 BMCs (ATEN firmware, verified on an X9SRL-F with firmware 3.19). It is meant for people
maintaining or porting the project. All multi-byte numbers are **big-endian** unless stated
otherwise.

## Transport

- The BMC exposes the KVM on **TCP 5900**. The stream is a variant of the RFB (VNC) protocol
  with its own handshake and login, and a proprietary video format.
- There is no transport encryption on this port.

## Handshake and login (TCP 5900)

1. The server sends the banner `RFB 003.008\n`; the client answers with the same banner.
2. The server sends the number of security types and the list. Here it is a single type, **16**.
   The client sends `16`.
3. The server sends **24 bytes** (different every session): they are ignored.
4. The client sends **user (24 bytes) + password (24 bytes)**: the two session strings
   (16 characters) **padded with 0x00 bytes** up to 24. Credentials are not encrypted.
5. The server sends a 32-bit result: `0` = OK. Anything else is followed by a 32-bit length and
   an ASCII error message.
6. The client sends **ClientInit**: 1 byte `0x01` (shared).
7. The server sends **ServerInit**: width+height (2+2, unreliable), 16 bytes of pixel format,
   4 bytes of name length + name; then **4 zero bytes, 4 bytes of session id, 4 bytes of
   flags/privileges**.

The session credentials (user = password = a 16-character token) come from the viewer's session
descriptor (the JNLP file), which is obtained after the web login described below.

## Screen update request

The client asks for an update with the standard RFB **FramebufferUpdateRequest** message:
`03 | incremental(1) | x(2) | y(2) | w(2) | h(2)`. With `incremental=0` it asks for the whole
screen; with `1` only for changes.

## Messages from the server

The client reads 1 type byte, then the body:

- **`0x00` screen update**: 3 bytes skipped, four 16-bit integers (x, y, w, h), one 32-bit
  integer (decoder id, meaningful only on the first update of the session: `0x59` = "Hermon"
  decoder; later it is 0 and must be ignored), one 32-bit mode, one 32-bit payload **length**,
  then the payload.
- **`0x04` cursor**: four 32-bit integers (x, y, w, h), one 32-bit "has bitmap"; if 1, 4 bytes
  (bpp) and a `w*h*2` byte bitmap follow.
- **`0x16` keepalive**: 1 byte; the client answers `0x16 <same byte>`.
- **`0x39` session info**: two 32-bit integers + 256 bytes of text (session id, user, IP).

## Video format (payload of message `0x00`, Hermon decoder)

Header (10 bytes):
- byte 0: **mode** — `1` = full frame; anything else = **tile** update.
- byte 1: **depth** — `0` = 16 bits/pixel (2 bytes); other = 8 bits/pixel (1 byte).
- bytes 2-5: a fixed marker in full frames; the **tile count** (u32) in tile updates.
- bytes 6-9: payload **length**, header included (u32).

Full frame: the pixels follow the 10 header bytes in sequence.

Tile update: one record per tile = **6 header bytes** (byte +4 = row, byte +5 = column, in tile
units) + the pixels of one **16x16** tile.

Pixels:
- 16 bits/pixel: **RGB 5-5-5 little-endian** (bits 10-14 red, 5-9 green, 0-4 blue).
- 8 bits/pixel: RGB 2-2-2.

### Real resolution (important)

The width/height in the message header is **not reliable** (it can claim e.g. 1920x1080 while
the real pixels are 1280x1024). The real resolution comes from the payload:

```
pixels = (payload_length - 10) / bytes_per_pixel
width  = min(declared_width, 1280)     # the firmware caps the capture at 1280
height = pixels / width
```

The BIOS text screen (POST, boot messages, 720x400 text mode) arrives as **752x413** at 16 bit.

### No video signal and video mode changes

- During a reboot (or with the screen off) the BMC answers update requests with `0x00` messages
  carrying **negative sizes** in the header: `0xFD80 x 0xFE20` (= -640x-480) and an **empty
  payload**, in bursts (hundreds per second).
- When the signal comes back it sends **only the tiles that changed against its own last
  image**, not a full frame: a client that does not clear the screen shows the new screen drawn
  over the old one. The gateway therefore blanks its framebuffer while there is no signal and
  sends a **non-incremental** request as soon as it comes back (and at every resolution change).

## Input to the console

- **Keyboard** (client message `0x04`): `tag | 0x00 | state(1: down/up) | 2 zero bytes |
  code(u32) | 9 zero bytes`. The **code is a USB HID usage code**, NOT an X11 keysym. Modifiers
  (Shift, Ctrl, Alt) arrive as separate keys and are translated to HID codes as well.
- **Mouse** (client message `0x05`, plain mode): `tag | 0x00 | buttons(1) | x(u16) | y(u16) |
  11 zero bytes`. **Absolute** coordinates in video pixels. There is also an encrypted branch
  (type `0x01`, AES-128 with a fixed key) that webkvm does not use.

## Web login and session token (HTTP :80)

Implemented in `webkvm/bmc/web_login.py`:

1. `POST /cgi/login.cgi` with `name`, `pwd` and `check=00`. On the **X9SRL-F with firmware 3.19
   the fields are plain text** (verified): the answer carries two `Set-Cookie` headers, the first
   with an empty `SID` and the second with the real SID (16 characters), plus a page redirecting
   to `url_redirect.cgi?url_name=mainmenu`. With base64 fields this firmware refuses the login;
   other firmware wants base64, so webkvm tries both forms.
2. `GET /cgi/url_redirect.cgi?url_name=ikvm&url_type=jwsk` with the `SID` cookie **and the
   cookies the web interface sets from JavaScript** (`langSetFlag=0; language=English;
   mainpage=remote; subpage=man_ikvm`) plus the header
   `Referer: .../cgi/url_redirect.cgi?url_name=man_ikvm`. With the `SID` alone firmware 3.19
   answers **500 Internal Server Error** (verified). The answer is the JNLP descriptor; its
   `<argument>` values are, in order: host, session user, session password (16-character token),
   name, KVM port (5900), virtual media port (623), ...
3. When the KVM session ends: `GET /cgi/logout.cgi`, so the BMC does not run out of web sessions.

## Viewer side (webkvm's RFB server)

- **Security**: the internal listener (127.0.0.1, used by the WebSocket bridge) has no
  authentication; the external one uses **VNC auth** (type 2, DES of the challenge,
  `webkvm/kvm/vncauth.py`). If the BMC does not answer, the reason reaches the viewer in the
  SecurityResult (RFB 3.8).
- **Pixel format**: BGRX 32 bpp internally. `SetPixelFormat` is honoured for 32 bpp true-colour
  formats whose shifts are multiples of 8 (e.g. noVNC's RGBX). Other formats close the
  connection with a message in the log.
- **Encodings**: Raw; the DesktopSize pseudo-encoding (-223) for resolution changes.
- **QEMU Extended Key Event** (-258): if the viewer announces it in `SetEncodings`, the server
  acknowledges it with a -258 rectangle in the first FramebufferUpdate. From then on the viewer
  sends message `255/0` (`down u16, keysym u32, XT scancode u32`; extended keys `E0 xx` as
  `0x80|xx`). The scancode is translated to HID with the `XT_HID` table, independent of the
  keyboard layout; without it the keysym is used.
- **Several viewers**: one queue of changed rectangles per viewer, and one reader thread (input)
  plus one writer thread (updates) each, so input is never blocked while waiting for a screen
  change.

## Robustness notes

- The RFB rectangle counter towards the viewer is 16 bits: when updates pile up (no viewer
  reading) they are collapsed into a single full-screen rectangle.
- Tiles sit on a 16x16 grid rounded up: rectangles must be **clipped** at the framebuffer edges,
  or the viewer rejects them ("rectangle too big").
- The ATEN KVM service on these BMCs sometimes stops accepting connections on port 5900 after
  many sessions opened and closed in a short time. A BMC reset (web interface → Maintenance →
  Unit Reset) brings it back; the host server is not affected.
