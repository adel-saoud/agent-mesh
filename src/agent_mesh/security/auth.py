"""Both sides of the card's bearer security scheme, built on the libraries' own hooks.

Server side: Starlette's `AuthenticationMiddleware` runs `MeshTokenBackend`, which verifies the
request JWT before any A2A handler runs. a2a-sdk's default call-context builder already copies
Starlette's `request.auth` into `ServerCallContext.state["auth"]`, so the verified claims reach
the agent executor with no custom builder. Only the agent card and the health check are public.

Client side: a2a-sdk's `AuthInterceptor` asks a `CredentialService` for a credential for each
security scheme the callee's card declares. `MeshTokenCredentials` mints a fresh token per call,
with `aud` and `sub` taken from the call's `ClientCallContext.state`.
"""

from __future__ import annotations

import logging
from typing import Any

from a2a.client import ClientCallContext, CredentialService
from a2a.server.context import ServerCallContext
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    SimpleUser,
)
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response

from agent_mesh.config import HEALTH_PATH
from agent_mesh.security.cards import BEARER_SECURITY_SCHEME_NAME
from agent_mesh.security.keys import SigningKey, TrustStore
from agent_mesh.security.tokens import ReplayCache, TokenError, mint_token, verify_token

logger = logging.getLogger(__name__)

PUBLIC_PATHS = frozenset({AGENT_CARD_WELL_KNOWN_PATH, HEALTH_PATH})
"""Reachable without a token: callers must read the signed card before they can call."""

AUDIENCE_STATE_KEY = "audience"
SUBJECT_STATE_KEY = "subject"
"""`ClientCallContext.state` keys `MeshTokenCredentials` reads: callee URL and end customer."""


class MeshAuthCredentials(AuthCredentials):
    """Starlette credentials that also carry the verified JWT claims."""

    def __init__(self, claims: dict[str, Any]) -> None:
        super().__init__(["mesh"])
        self.claims = claims


class MeshTokenBackend(AuthenticationBackend):
    """Verifies the per-request ES256 token: signature, audience, expiry, issuer, replay."""

    def __init__(
        self,
        *,
        audience: str,
        trust: TrustStore,
        allowed_issuers: frozenset[str],
        replay_cache: ReplayCache | None = None,
    ) -> None:
        self._audience = audience
        self._trust = trust
        self._allowed_issuers = allowed_issuers
        self._replay_cache = replay_cache or ReplayCache()

    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[MeshAuthCredentials, SimpleUser] | None:
        """Verified claims and the caller as user; None (anonymous) only for public paths."""
        if conn.url.path in PUBLIC_PATHS:
            return None
        scheme, _, token = conn.headers.get("authorization", "").partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise TokenError("missing bearer token")
            claims = verify_token(
                token,
                audience=self._audience,
                trust=self._trust,
                allowed_issuers=self._allowed_issuers,
                replay_cache=self._replay_cache,
            )
        except TokenError as exc:
            logger.warning("request refused: %s", exc)
            raise AuthenticationError("invalid_token") from exc
        return MeshAuthCredentials(claims), SimpleUser(str(claims["iss"]))


def unauthorized_response(_conn: HTTPConnection, _exc: AuthenticationError) -> Response:
    """RFC 6750 response. The reason stays in the server log, never in the reply."""
    return JSONResponse(
        {"error": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
    )


def verified_claims(context: ServerCallContext | None) -> dict[str, Any]:
    """Verified claims for the current call. Raises if authentication did not run."""
    credentials = None if context is None else context.state.get("auth")
    if not isinstance(credentials, MeshAuthCredentials):
        raise PermissionError("call reached the executor without verified claims")
    return credentials.claims


class MeshTokenCredentials(CredentialService):
    """Mints one short-lived token per call for the card's `meshBearer` scheme."""

    def __init__(self, key: SigningKey, *, ttl_seconds: int) -> None:
        self._key = key
        self._ttl_seconds = ttl_seconds

    async def get_credentials(
        self, security_scheme_name: str, context: ClientCallContext | None
    ) -> str | None:
        """A fresh token for our bearer scheme; None for any other scheme the card declares."""
        if security_scheme_name != BEARER_SECURITY_SCHEME_NAME or context is None:
            return None
        return mint_token(
            self._key,
            audience=context.state[AUDIENCE_STATE_KEY],
            subject=context.state[SUBJECT_STATE_KEY],
            ttl_seconds=self._ttl_seconds,
        )


def client_call_context(*, audience: str, subject: str) -> ClientCallContext:
    """The client call context `MeshTokenCredentials` needs for one call."""
    return ClientCallContext(state={AUDIENCE_STATE_KEY: audience, SUBJECT_STATE_KEY: subject})
