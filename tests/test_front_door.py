import asyncio
import json

import httpx
import pytest

from tools import front_door


@pytest.mark.parametrize(
    "target, platform",
    [
        ("/api/v1/files", True),
        ("/api/v1/files/uploads?x=1", True),
        ("/api/v1/files/datasets/sources/source:ab", True),
        ("/api/v1/space", True),
        ("/api/v1/space/resources", True),
        ("/api/v1/models?limit=1", True),
        ("/api/v1/models/model_" + "a" * 64, True),
        ("/api/v1/models-unrelated", False),
        ("/api/v1/studies/executions/run/model-fits", False),
        ("/internal/document-reads/claim", True),
        ("/internal/document-captures", True),
        ("/internal/dataset-preparations/claim", True),
        ("/internal/web-research/claim", True),
        ("/api/v1/platform/health", True),
        ("http://localhost:9000/api/v1/files/limits", True),
        ("/api/v1/health", False),
        ("/api/v1/discovery/datasets/search", True),
        ("/api/v1/agents/reference-library", True),
        ("/api/v1/agents/permissions", True),
        ("/api/v1/docs/grammar?detail=index", True),
        ("/api/v1/enums", True),
        ("/api/v1/enums/Country", True),
        ("/api/v1/schemas/StudyDefinition", True),
        ("/api/v1/skills/packages/core", True),
        ("/api/v1/metadata/transforms", True),
        ("/api/v1/metadata/assets", True),
        ("/api/v1/metadata/assets/resolve", True),
        ("/api/v1/metadata/countries", True),
        ("/api/v1/metadata/countries/US", True),
        ("/api/v1/metadata/regions/NA", True),
        ("/api/v1/assets/asset-id/logos?listing_catalogue=retained", True),
        ("/api/v1/images/image-id", True),
        ("/api/v1/metadata/AssetClass", False),
        ("/api/v1/assets/asset-id/prices", False),
        ("/api/v1/agents/context", False),
        ("/api/v1/auth/token", False),
        ("/api/v1/internal/document-reads/claim", False),
        ("/API/v1/files", False),
        ("/api/v1/studies?next=/api/v1/files", False),
    ],
)
def test_routes_as_the_deployed_load_balancer(target, platform):
    assert front_door.to_platform(target) is platform


def test_rewrites_heads_to_close_and_keeps_x_user_id_only_from_loopback():
    head = b"GET /x HTTP/1.1\r\nHost: localhost\r\nConnection: keep-alive\r\nKeep-Alive: 5\r\nX-User-Id: u1\r\n\r\n"
    trusted, upgrade = front_door.rewrite(head)
    assert not upgrade
    assert trusted == b"GET /x HTTP/1.1\r\nHost: localhost\r\nX-User-Id: u1\r\nConnection: close\r\n\r\n"
    untrusted, _ = front_door.rewrite(head, trusted=False)
    assert b"x-user-id" not in untrusted.lower()
    socket, upgrade = front_door.rewrite(
        b"GET /ws HTTP/1.1\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n"
    )
    assert upgrade and socket.endswith(b"Upgrade: websocket\r\nConnection: upgrade\r\n\r\n")
    assert front_door.loopback(("127.0.0.1", 5)) and not front_door.loopback(("172.17.0.2", 5))


async def upstream(name: str, seen: list):
    async def handle(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:] if line)
        body = await reader.readexactly(int(headers.get("Content-Length", 0)))
        seen.append((name, lines[0], headers, body))
        if lines[0].startswith("GET /api/v1/files/stream"):
            writer.write(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            for chunk in (b"first", b"second"):
                writer.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                await writer.drain()
            writer.write(b"0\r\n\r\n")
        else:
            payload = json.dumps({"upstream": name, "body": body.decode()}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n%s" % (len(payload), payload))
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "127.0.0.1", 0)


def test_relays_each_request_to_its_service_and_closes_after_it():
    async def scenario():
        seen = []
        backend, platform = await upstream("backend", seen), await upstream("platform", seen)
        ports = [server.sockets[0].getsockname()[1] for server in (backend, platform)]
        door = await asyncio.start_server(
            lambda r, w: front_door.relay(r, w, *ports), "127.0.0.1", 0, limit=front_door.HEAD_LIMIT
        )
        origin = f"http://127.0.0.1:{door.sockets[0].getsockname()[1]}"
        async with httpx.AsyncClient(base_url=origin) as client:
            health = await client.get("/api/v1/health", headers={"X-User-Id": "u1"})
            upload = await client.post("/api/v1/files/uploads", content=b"x" * 200_000)
            stream = await client.get("/api/v1/files/stream")
            space = await client.get("/api/v1/space/browse")
        for server in (door, backend, platform):
            server.close()
        return seen, health, upload, stream, space

    seen, health, upload, stream, space = asyncio.run(scenario())
    assert health.json()["upstream"] == "backend"
    assert health.headers["connection"] == "close"
    assert upload.json() == {"upstream": "platform", "body": "x" * 200_000}
    assert stream.text == "firstsecond"
    assert space.json()["upstream"] == "platform"
    assert [(name, line) for name, line, _, _ in seen] == [
        ("backend", "GET /api/v1/health HTTP/1.1"),
        ("platform", "POST /api/v1/files/uploads HTTP/1.1"),
        ("platform", "GET /api/v1/files/stream HTTP/1.1"),
        ("platform", "GET /api/v1/space/browse HTTP/1.1"),
    ]
    # A loopback client's X-User-Id still reaches Backend; every request asks to close.
    assert seen[0][2]["X-User-Id"] == "u1"
    assert all(headers["Connection"] == "close" for _, _, headers, _ in seen)
