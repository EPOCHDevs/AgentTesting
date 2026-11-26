"""The local stack's front door: the deployed load balancer's path rules on the local API origin.

At api.<env>.epoch.trade the load balancer sends Files, Space and the Files workers' routes to Epoch
Platform and everything else to Backend (epoch-terraform lib/api-routes.ts). This does the same for
http://localhost:<port>, so the Portal, the agent and AgentTesting keep one API origin.

Each request is relayed on its own upstream connection and both sides are told the connection
closes after it. Bodies, streamed responses and upgrades pass through byte for byte. Backend trusts
an X-User-Id header only from a loopback peer; the header is dropped from any other peer, because
every relayed request reaches Backend from loopback.

Usage: front_door.py <listen port> <backend port> <platform port>
"""

import asyncio
import ipaddress
import re
import sys
from urllib.parse import urlsplit

# Platform's public/reference/discovery/asset-identity paths and Files workers.
# Saved Models are Platform application resources; native execution fit reads
# remain on Backend under /studies/executions/.../model-fits.
PLATFORM_PATHS = (
    "/api/v1/files*",
    "/api/v1/space*",
    "/api/v1/models",
    "/api/v1/models/*",
    # PLATFORM_EARLY_PRESENTATION_PATHS; other execution context reads stay Native.
    "/api/v1/contexts/executions/*/presentation/delivery",
    "/internal/document-*",
    "/internal/dataset-preparations/*",
    "/internal/web-research/*",
    "/api/v1/platform/*",
    "/api/v1/docs/grammar",
    "/api/v1/enums",
    "/api/v1/enums/*",
    "/api/v1/schemas",
    "/api/v1/schemas/*",
    "/api/v1/skills/packages",
    "/api/v1/skills/packages/*",
    "/api/v1/agents/reference-library",
    "/api/v1/agents/permissions",
    "/api/v1/metadata/transforms",
    "/api/v1/discovery/*",
    "/api/v1/metadata/assets",
    "/api/v1/metadata/assets/*",
    "/api/v1/metadata/countries",
    "/api/v1/metadata/countries/*",
    "/api/v1/metadata/regions/*",
    "/api/v1/assets/*/logos",
    "/api/v1/images/*",
)
HEAD_LIMIT = 64 * 1024


def alb_pattern(pattern: str) -> re.Pattern[str]:
    """An ALB path pattern: case-sensitive; `*` matches anything, `/` included; `?` one character."""
    return re.compile(
        "^" + "".join(".*" if c == "*" else "." if c == "?" else re.escape(c) for c in pattern) + "$"
    )


PLATFORM = tuple(alb_pattern(pattern) for pattern in PLATFORM_PATHS)


def to_platform(target: str) -> bool:
    path = urlsplit(target).path if "://" in target else target.split("?", 1)[0]
    return any(pattern.match(path) for pattern in PLATFORM)


def loopback(peer) -> bool:
    try:
        return ipaddress.ip_address(peer[0]).is_loopback
    except (TypeError, ValueError, IndexError):
        return False


def rewrite(head: bytes, *, trusted: bool = True) -> tuple[bytes, bool]:
    """One message head with `Connection: close` (an upgrade keeps `Connection: upgrade`)."""
    first, *fields = head[: -len(b"\r\n\r\n")].split(b"\r\n")
    names = [field.split(b":", 1)[0].strip().lower() for field in fields]
    upgrade = b"upgrade" in names and any(
        name == b"connection" and b"upgrade" in field.lower() for name, field in zip(names, fields)
    )
    dropped = {b"connection", b"keep-alive", b"proxy-connection"}
    if not trusted:
        dropped.add(b"x-user-id")
    kept = [field for name, field in zip(names, fields) if name not in dropped]
    kept.append(b"Connection: upgrade" if upgrade else b"Connection: close")
    return b"\r\n".join([first, *kept]) + b"\r\n\r\n", upgrade


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass


async def respond(upstream: asyncio.StreamReader, client: asyncio.StreamWriter, upgrade: bool) -> None:
    """Relays the response, closing the connection after it unless the protocol switches."""
    while True:
        head = await upstream.readuntil(b"\r\n\r\n")
        status = head.split(b" ", 2)[1:2]
        interim = status and status[0].startswith(b"1") and status[0] != b"101"
        if upgrade and status == [b"101"]:
            client.write(head)
        else:
            client.write(rewrite(head)[0])
        await client.drain()
        if not interim:
            break
    await pipe(upstream, client)


async def relay(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, backend: int, platform: int
) -> None:
    upstream_writer = None
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split(" ")
        if len(parts) != 3:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        port = platform if to_platform(parts[1]) else backend
        request, upgrade = rewrite(head, trusted=loopback(writer.get_extra_info("peername")))
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            return
        upstream_writer.write(request)
        sending = asyncio.create_task(pipe(reader, upstream_writer))
        try:
            await respond(upstream_reader, writer, upgrade)
        finally:
            sending.cancel()
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError, ValueError):
        pass
    finally:
        for stream in (writer, upstream_writer):
            if stream is not None:
                stream.close()


async def main(port: int, backend: int, platform: int) -> None:
    # On every interface, as Backend listens, so the `up` agent's container reaches it.
    server = await asyncio.start_server(
        lambda r, w: relay(r, w, backend, platform), "0.0.0.0", port, limit=HEAD_LIMIT
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main(*(int(value) for value in sys.argv[1:4])))
