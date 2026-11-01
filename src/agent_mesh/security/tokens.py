"""Short-lived JWTs for A2A requests and for human confirmations.

Request tokens follow the card's HTTP bearer security scheme (A2A spec, section 4.5): the
caller signs a token per request with `iss` = caller, `aud` = the callee's URL, `sub` = the end
user the call is made for, `exp` <= 60 s and a `jti` the callee refuses to see twice.

`sub` is how the customer identity crosses the mesh. Specialists read it from the verified token,
never from the prompt or from model output, which is what stops one customer reading another's
orders even if the model is tricked.

Confirmation tokens bind a human "yes" to one exact action through `action_hash`, the same idea
as an AP2 closed mandate bound to its checkout by hash.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable
from typing import Any

import jwt
from pydantic import BaseModel

from agent_mesh.security.keys import ALGORITHM, SigningKey, TrustStore, issuer_of

CONFIRMATION_AUDIENCE = "agent-mesh:confirmation"
"""Not a URL, so a confirmation token can never pass as a request token, and the other way round."""


class TokenError(Exception):
    """Any reason to refuse a token. The message is safe to log, never to return verbatim."""


class ReplayCache:
    """Remembers `jti` values until their token expires. In-memory: one process per agent."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._seen: dict[str, float] = {}
        self._clock = clock

    def check_and_add(self, jti: str, exp: float) -> None:
        """Refuse a `jti` already seen, then remember it until `exp`."""
        now = self._clock()
        self._seen = {k: v for k, v in self._seen.items() if v > now}
        if jti in self._seen:
            raise TokenError("replayed token")
        self._seen[jti] = exp


def mint_token(
    key: SigningKey,
    *,
    audience: str,
    subject: str,
    ttl_seconds: int,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a token for one call. `extra_claims` adds claims such as `action_hash`."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer_of(key.kid),
        "sub": subject,
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    claims.update(extra_claims or {})
    return jwt.encode(claims, key.private_pem, algorithm=ALGORITHM, headers={"kid": key.kid})


def verify_token(
    token: str,
    *,
    audience: str,
    trust: TrustStore,
    allowed_issuers: frozenset[str],
    replay_cache: ReplayCache | None = None,
) -> dict[str, Any]:
    """Return verified claims or raise `TokenError`.

    The key is looked up by `kid` and its owner must equal `iss`, so one principal's key can never
    vouch for another principal.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError("malformed token") from exc
    kid = header.get("kid")
    public = trust.get(kid)
    if public is None or kid is None:
        raise TokenError("unknown signing key")
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=public,
            algorithms=[ALGORITHM],
            audience=audience,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "jti"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(f"invalid token: {type(exc).__name__}") from exc
    if claims["iss"] != issuer_of(kid) or claims["iss"] not in allowed_issuers:
        raise TokenError("issuer not allowed")
    if replay_cache is not None:
        replay_cache.check_and_add(str(claims["jti"]), float(claims["exp"]))
    return claims


def action_hash(action: BaseModel) -> str:
    """SHA-256 of the action's canonical JSON. Any change to the action breaks the confirmation."""
    return hashlib.sha256(action.model_dump_json().encode()).hexdigest()


def mint_confirmation(key: SigningKey, *, subject: str, action: BaseModel, ttl_seconds: int) -> str:
    """Record a customer's "yes" to one exact action, signed by the coordinator."""
    return mint_token(
        key,
        audience=CONFIRMATION_AUDIENCE,
        subject=subject,
        ttl_seconds=ttl_seconds,
        extra_claims={"action_hash": action_hash(action)},
    )


def verify_confirmation(
    token: str,
    *,
    subject: str,
    action: BaseModel,
    trust: TrustStore,
    allowed_issuers: frozenset[str],
    replay_cache: ReplayCache,
) -> None:
    """Raise `TokenError` unless the token confirms exactly `action` for exactly `subject`."""
    claims = verify_token(
        token,
        audience=CONFIRMATION_AUDIENCE,
        trust=trust,
        allowed_issuers=allowed_issuers,
        replay_cache=replay_cache,
    )
    if claims["sub"] != subject:
        raise TokenError("confirmation issued for another customer")
    if claims.get("action_hash") != action_hash(action):
        raise TokenError("confirmation does not match the action")
