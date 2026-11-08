"""Private, refreshable Cognito login for test stacks. Never stores passwords."""

import getpass
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
from botocore.config import Config
from pycognito import Cognito

REGION = "us-west-1"
POOL = "us-west-1_Fz1y1zKFq"
CLIENT = "2m0ep0k9gksk00ml7qtkked0fk"
LOCAL_LOGIN = "python -m tools.local_stack --login"


@dataclass(frozen=True)
class UserPool:
    """A Cognito user pool and its public app client; neither is a secret."""

    pool_id: str
    client_id: str

    @property
    def region(self) -> str:
        return self.pool_id.partition("_")[0]


LOCAL = UserPool(POOL, CLIENT)


def token_path(name: str = "local") -> Path:
    return Path.home() / ".config/epoch-agent-testing" / f"{name}-login.json"


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


def client(pool: UserPool = LOCAL, **kwargs) -> Cognito:
    # Cognito SRP/refresh uses the public app client, not workstation AWS keys.
    return Cognito(
        pool.pool_id,
        pool.client_id,
        user_pool_region=pool.region,
        boto3_client_kwargs={"aws_access_key_id": "", "aws_secret_access_key": ""},
        botocore_config=Config(
            connect_timeout=10, read_timeout=20, retries={"max_attempts": 0}
        ),
        **kwargs,
    )


def snapshot(user: Cognito, email: str, pool: UserPool = LOCAL) -> dict:
    if not all((user.id_token, user.access_token, user.refresh_token)):
        raise ValueError(
            "Login requires an additional Cognito challenge; complete it in the UI."
        )
    tokens = {
        "email": email,
        "username": user.username,
        "id_token": user.id_token,
        "access_token": user.access_token,
        "refresh_token": user.refresh_token,
    }
    if pool != LOCAL:  # Refresh must use the pool that issued the login.
        tokens |= {"user_pool_id": pool.pool_id, "client_id": pool.client_id}
    return tokens


def cached_token(
    path: Path | None = None, email: str | None = None, login_command: str = LOCAL_LOGIN
) -> str:
    path = path or token_path()
    if not path.exists():
        raise ValueError(f"No test login. Run {login_command} first.")
    try:
        tokens = json.loads(path.read_text())
        if email and tokens["email"].casefold() != email.casefold():
            raise PermissionError
        # Expiry only; the services validate the token signature and identity.
        expiry = jwt.decode(tokens["id_token"], options={"verify_signature": False})[
            "exp"
        ]
        if expiry < time.time() + 120:
            pool = (
                UserPool(tokens["user_pool_id"], tokens["client_id"])
                if "user_pool_id" in tokens
                else LOCAL
            )
            user = client(
                pool=pool,
                **{
                    key: tokens[key]
                    for key in ("username", "id_token", "access_token", "refresh_token")
                }
            )
            user.renew_access_token()
            tokens = snapshot(user, tokens["email"], pool)
            save_tokens(tokens, path)
        return tokens["id_token"]
    except PermissionError:
        raise ValueError(
            f"The cached login belongs to another account; run {login_command} again."
        ) from None
    except Exception:  # SDK errors can contain credential-bearing request data.
        raise ValueError(
            f"The test login could not be refreshed. Run {login_command} again."
        ) from None


def login(
    email: str | None = None, path: Path | None = None, pool: UserPool = LOCAL
) -> str:
    email = email or input("Epoch email: ").strip()
    password = getpass.getpass("Epoch password: ")
    try:
        user = client(pool=pool, username=email)
        user.authenticate(password=password)
        tokens = snapshot(user, email, pool)
        save_tokens(tokens, path or token_path())
    except Exception:  # Never echo the SDK request or password in an error.
        raise ValueError(
            "Epoch login failed or needs an additional challenge. Check the account in the UI."
        ) from None
    finally:
        password = ""
    print(f"Test login ready: {email}")
    return tokens["id_token"]
