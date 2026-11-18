from __future__ import annotations

from typing import TYPE_CHECKING

from winrt.windows.storage.streams import DataReader, DataWriter, InputStreamOptions

from phonebridge.protocol import StreamClosedError

if TYPE_CHECKING:
    from winrt.windows.networking.sockets import StreamSocket


class WinRtStream:
    """Byte stream over a connected WinRT RFCOMM socket."""

    def __init__(self, socket: StreamSocket) -> None:
        self._socket = socket
        self._reader = DataReader(socket.input_stream)
        self._reader.input_stream_options = InputStreamOptions.PARTIAL
        self._writer = DataWriter(socket.output_stream)

    async def read_exact(self, size: int) -> bytes:
        out = bytearray()
        try:
            while len(out) < size:
                loaded = await self._reader.load_async(size - len(out))
                if loaded == 0:
                    raise StreamClosedError("the phone closed the connection")
                out += bytes(self._reader.read_buffer(loaded))
        except OSError as exc:
            raise StreamClosedError(str(exc)) from exc
        return bytes(out)

    async def write(self, data: bytes) -> None:
        try:
            self._writer.write_bytes(data)
            await self._writer.store_async()
        except OSError as exc:
            raise StreamClosedError(str(exc)) from exc

    async def close(self) -> None:
        self._socket.close()
