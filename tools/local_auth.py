"""Private, refreshable Cognito login for local testing. Never stores passwords."""

import getpass
import json
import os
import tempfile
import time
from pathlib import Path

import jwt
from botocore.config import Config
from pycognito import Cognito

REGION = "us-west-1"
POOL = "us-west-1_Fz1y1zKFq"
CLIENT = "2m0ep0k9gksk00ml7qtkked0fk"


def token_path() -> Path:
    return Path.home() / ".config/epoch-agent-testing/local-login.json"


def save_tokens(tokens: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(tokens, stream)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def client(**kwargs) -> Cognito:
    # Cognito SRP/refresh uses the public app client, not workstation AWS keys.
    return Cognito(
        POOL,
        CLIENT,
        user_pool_region=REGION,
        boto3_client_kwargs={"aws_access_key_id": "", "aws_secret_access_key": ""},
        botocore_config=Config(
            connect_timeout=10, read_timeout=20, retries={"max_attempts": 0}
        ),
        **kwargs,
    )


def snapshot(user: Cognito, email: str) -> dict:
    if not all((user.id_token, user.access_token, user.refresh_token)):
        raise ValueError(
            "Login requires an additional Cognito challenge; complete it in the UI."
        )
    return {
        "email": email,
        "username": user.username,
        "id_token": user.id_token,
        "access_token": user.access_token,
        "refresh_token": user.refresh_token,
    }


def cached_token(path: Path | None = None, email: str | None = None) -> str:
    path = path or token_path()
    if not path.exists():
        raise ValueError("No local login. Run python -m tools.local_stack first.")
    try:
        tokens = json.loads(path.read_text())
        if email and tokens["email"].casefold() != email.casefold():
            raise PermissionError
        # Expiry only; the services validate the token signature and identity.
        expiry = jwt.decode(tokens["id_token"], options={"verify_signature": False})[
            "exp"
        ]
        if expiry < time.time() + 120:
            user = client(
                **{
                    key: tokens[key]
                    for key in ("username", "id_token", "access_token", "refresh_token")
                }
            )
            user.renew_access_token()
            tokens = snapshot(user, tokens["email"])
            save_tokens(tokens, path)
        return tokens["id_token"]
    except PermissionError:
        raise ValueError(
            "The cached local login belongs to another account; use --login."
        ) from None
    except Exception:  # SDK errors can contain credential-bearing request data.
        raise ValueError(
            "Local login could not be refreshed. Run with --login again."
        ) from None


def login(email: str | None = None, path: Path | None = None) -> str:
    email = email or input("Epoch email: ").strip()
    password = getpass.getpass("Epoch password: ")
    try:
        user = client(username=email)
        user.authenticate(password=password)
        tokens = snapshot(user, email)
        save_tokens(tokens, path or token_path())
    except Exception:  # Never echo the SDK request or password in an error.
        raise ValueError(
            "Epoch login failed or needs an additional challenge. Check the account in the UI."
        ) from None
    finally:
        password = ""
    print(f"Local test login ready: {email}")
    return tokens["id_token"]
