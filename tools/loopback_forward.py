"""Relay a loopback port to another host, from a container sharing the agent's network.

The agent's document and web workers accept plain HTTP only from a loopback Backend
origin. Inside the `up` container the local Backend is the Docker host, so this
listens on 127.0.0.1:<port> and relays each connection to <host>:<port>.

Usage: loopback_forward.py <port> <host>
"""

import asyncio
import sys


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        pass


async def main(port: int, host: str) -> None:
    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(
            pipe(reader, upstream_writer), pipe(upstream_reader, writer), return_exceptions=True
        )
        for stream in (writer, upstream_writer):
            stream.close()

    server = await asyncio.start_server(relay, "127.0.0.1", port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
