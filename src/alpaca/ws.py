"""A WebSocket client on the standard library alone (RFC 6455): what the
plugin streams Alpaca's market data over.

The SDK carries no WebSocket client (the ruling of 2026-10-07: typed
operations only), and this plugin takes no third-party dependency, so this
is the client: asyncio's streams, `ssl`'s default context for `wss://`, and
the protocol's few parts a client needs -- the opening handshake checked
against its key, frames masked as a client must, text and binary messages
put together from their fragments, a ping answered with a pong, a close
answered and said. Nothing here knows Alpaca; stream.py speaks its protocol
over it.

A message past `max_message` bytes, a frame a server must not send (masked,
or a control frame fragmented or too long), or a handshake that is not one
closes the connection with `Closed`, naming why; so does the server closing
it, or the connection breaking. The caller reconnects.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import os
import ssl
import struct
import urllib.parse
from collections.abc import Mapping

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
#: The largest message taken, in bytes: far over any of Alpaca's.
MAX_MESSAGE = 4 * 1024 * 1024
#: How long the opening handshake may take, in seconds.
HANDSHAKE_SECONDS = 15.0

CONTINUATION, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class Closed(Exception):
    """The connection is over: the server closed it, it broke, or what it
    sent was not WebSocket. Its message says which."""


class WebSocket:
    """One open connection: `recv` a message, `send` one, `close`."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        max_message: int = MAX_MESSAGE,
    ) -> None:
        self.reader, self.writer = reader, writer
        self.max_message = max_message
        self.closed = False

    async def send(self, text: str) -> None:
        """One text message, in one frame, masked."""
        await self._frame(TEXT, text.encode("utf-8"))

    async def recv(self) -> str:
        """The next message, put together from its fragments; a ping is
        answered and a pong passed over on the way. Raises `Closed`."""
        parts: list[bytes] = []
        size = 0
        kind = 0
        while True:
            fin, opcode, payload = await self._read_frame()
            if opcode == PING:
                await self._frame(PONG, payload)
                continue
            if opcode == PONG:
                continue
            if opcode == CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else 1005
                reason = payload[2:].decode("utf-8", errors="replace")
                with contextlib.suppress(Exception):
                    await self._frame(CLOSE, payload[:2])
                await self._drop()
                raise Closed(f"the server closed the connection: {code} {reason}".strip())
            if opcode in (TEXT, BINARY):
                if parts:
                    await self._fail("a new message began before the last one ended")
                kind = opcode
            elif opcode == CONTINUATION:
                if not parts and not kind:
                    await self._fail("a continuation with no message to continue")
            else:
                await self._fail(f"an opcode no server sends: {opcode:#x}")
            size += len(payload)
            if size > self.max_message:
                await self._fail(f"a message past {self.max_message} bytes")
            parts.append(payload)
            if fin:
                data = b"".join(parts)
                if kind == BINARY:
                    return data.decode("utf-8", errors="replace")
                try:
                    return data.decode("utf-8")
                except UnicodeDecodeError:
                    await self._fail("a text message that is not UTF-8")

    async def close(self, code: int = 1000) -> None:
        """Say the connection is closing, and close it."""
        if self.closed:
            return
        with contextlib.suppress(Exception):
            await self._frame(CLOSE, struct.pack("!H", code))
        await self._drop()

    # ── Frames ─────────────────────────────────────────────────────────

    async def _frame(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise Closed("the connection is closed")
        mask = os.urandom(4)
        length = len(payload)
        head = bytes([0x80 | opcode])
        if length < 126:
            head += bytes([0x80 | length])
        elif length < 1 << 16:
            head += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            head += bytes([0x80 | 127]) + struct.pack("!Q", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self.writer.write(head + mask + masked)
            await self.writer.drain()
        except (OSError, RuntimeError) as broken:
            await self._drop()
            raise Closed(f"the connection broke: {broken}") from None

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        try:
            first, second = await self.reader.readexactly(2)
            fin, opcode = bool(first & 0x80), first & 0x0F
            if first & 0x70:
                await self._fail("a reserved bit set with no extension agreed")
            if second & 0x80:
                await self._fail("a masked frame from the server")
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", await self.reader.readexactly(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", await self.reader.readexactly(8))
            if opcode >= CLOSE and (not fin or length > 125):
                await self._fail("a control frame fragmented or past 125 bytes")
            if length > self.max_message:
                await self._fail(f"a frame past {self.max_message} bytes")
            payload = await self.reader.readexactly(length) if length else b""
        except (asyncio.IncompleteReadError, OSError) as broken:
            await self._drop()
            raise Closed(f"the connection broke: {type(broken).__name__}") from None
        return fin, opcode, payload

    async def _fail(self, why: str) -> None:
        await self.close(1002)
        raise Closed(f"not WebSocket: {why}")

    async def _drop(self) -> None:
        self.closed = True
        with contextlib.suppress(Exception):
            self.writer.close()
            await asyncio.wait_for(self.writer.wait_closed(), 2)


def accept_for(key: str) -> str:
    """What a server answers a key with (RFC 6455, 4.2.2)."""
    return base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()


async def connect(
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    ssl_context: ssl.SSLContext | None = None,
    max_message: int = MAX_MESSAGE,
    timeout: float = HANDSHAKE_SECONDS,
) -> WebSocket:
    """Open `url` (`ws://` or `wss://`) and complete the opening handshake.
    Raises `Closed` where it cannot, naming why."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        raise ValueError(f"not a WebSocket URL: {url!r}")
    secure = parsed.scheme == "wss"
    port = parsed.port or (443 if secure else 80)
    context = (ssl_context or ssl.create_default_context()) if secure else None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, port, ssl=context), timeout
        )
    except (OSError, TimeoutError, ssl.SSLError) as failed:
        raise Closed(f"could not connect to {parsed.hostname}: {failed}") from None
    key = base64.b64encode(os.urandom(16)).decode()
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    host = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    lines = [
        f"GET {target} HTTP/1.1",
        f"Host: {host}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        *(f"{name}: {value}" for name, value in (headers or {}).items()),
    ]
    socket = WebSocket(reader, writer, max_message)
    try:
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout)
    except (OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
        await socket._drop()
        raise Closed(f"the handshake did not complete: {type(err).__name__}") from None
    status, *fields = head.decode("latin-1").split("\r\n")
    answered = {}
    for line in fields:
        name, _, value = line.partition(":")
        if name:
            answered[name.strip().lower()] = value.strip()
    parts = status.split(" ", 2)
    if len(parts) < 2 or parts[1] != "101":
        await socket._drop()
        raise Closed(f"the server refused the upgrade: {status.strip()[:120]}")
    if (
        answered.get("upgrade", "").lower() != "websocket"
        or "upgrade" not in answered.get("connection", "").lower()
        or answered.get("sec-websocket-accept") != accept_for(key)
    ):
        await socket._drop()
        raise Closed("the server's handshake did not answer this client's key")
    return socket
