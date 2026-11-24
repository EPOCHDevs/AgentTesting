"""Which Terminal the suite drives: the environment's own, or a local one against its Backend.

`--ui-url http://localhost:3002 --env dev` points the browser at a local Terminal (for
example a Vite dev server of an unmerged branch built against the dev API) while the
Backend reads, the sign-in and the fixtures stay dev's. The browser seeds that origin with
dev's sign-in tokens, so only an origin on this machine may receive them.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from tools.agent_testing import ServiceIdentity, Stack

LOOPBACK = ("localhost", "127.0.0.1", "::1")


def override_origin(url: str, environment: str) -> str:
    """A validated local Terminal origin (scheme, host and port only)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (
        parts.scheme not in ("http", "https")
        or not host
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError(
            "Give a Terminal origin such as http://localhost:3002, without a path, query or credentials."
        )
    if host not in LOOPBACK and not host.endswith(".localhost"):
        raise ValueError(
            f"{host} is not on this machine. The browser is signed in there with the {environment} "
            "login, so --ui-url accepts only localhost, 127.0.0.1, [::1] or *.localhost."
        )
    return f"{parts.scheme}://{parts.netloc}"


def identity(stack: Stack, ui_url: str | None = None) -> ServiceIdentity | dict[str, object]:
    """The attempt's services; an overridden Terminal says so, so it isn't read as the environment's."""
    services: dict[str, object] = dict(stack.identity())
    if ui_url and ui_url.rstrip("/") != stack.ui_url.rstrip("/"):
        services["ui_url"] = ui_url
        services["ui_url_override"] = {
            "replaces": stack.ui_url,
            "backend_and_sign_in_from": stack.environment,
        }
    return services
