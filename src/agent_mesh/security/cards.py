"""Signed agent cards.

Uses a2a-sdk's own signer and verifier (`a2a.utils.signing`), which implement the A2A spec's
JWS over the RFC 8785 canonical card. Callers verify before trusting anything on the card and
fail closed: an unsigned card, an unknown `kid` or a signature by the wrong principal is refused.
"""

from __future__ import annotations

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0
from a2a.utils.signing import (
    ProtectedHeader,
    SignatureVerificationError,
    create_agent_card_signer,
    create_signature_verifier,
)
from jwt import PyJWK

from agent_mesh.security.keys import ALGORITHM, KeyUse, SigningKey, TrustStore, kid_for
from agent_mesh.security.tokens import TokenError

BEARER_SECURITY_SCHEME_NAME = "meshBearer"
"""Key of the bearer scheme in the card's `security_schemes`; `AuthInterceptor` asks for it."""


def build_card(
    *, name: str, description: str, url: str, version: str, skills: list[AgentSkill]
) -> AgentCard:
    """An unsigned card that declares JSON-RPC over HTTP and a JWT bearer requirement."""
    card = AgentCard(
        name=name,
        description=description,
        version=version,
        supported_interfaces=[
            AgentInterface(
                url=url, protocol_binding="JSONRPC", protocol_version=PROTOCOL_VERSION_1_0
            )
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )
    card.security_schemes[BEARER_SECURITY_SCHEME_NAME].CopyFrom(
        SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                scheme="Bearer",
                bearer_format="JWT",
                description="ES256 JWT minted per request by the calling agent; aud = this URL.",
            )
        )
    )
    requirement = SecurityRequirement()
    requirement.schemes[BEARER_SECURITY_SCHEME_NAME].SetInParent()
    card.security_requirements.append(requirement)
    return card


def sign_card(card: AgentCard, key: SigningKey) -> AgentCard:
    """Return `card` with a JWS signature by `key` appended."""
    header: ProtectedHeader = {"kid": key.kid, "alg": ALGORITHM, "jku": None, "typ": "JOSE"}
    return create_agent_card_signer(key.private_pem, header)(card)


def verify_card(card: AgentCard, *, expected_principal: str, trust: TrustStore) -> None:
    """Raise `TokenError` unless `card` carries a valid signature by `expected_principal`."""
    expected_kid = kid_for(expected_principal, KeyUse.CARD)

    def key_provider(kid: str | None, _jku: str | None) -> PyJWK:
        """Resolve only the expected principal's card key; `jku` is ignored, never fetched."""
        if kid != expected_kid:
            raise TokenError(f"card signed by {kid!r}, expected {expected_kid!r}")
        key = trust.get(kid)
        if key is None:
            raise TokenError("card signing key not trusted")
        return key

    try:
        create_signature_verifier(key_provider, [ALGORITHM])(card)
    except SignatureVerificationError as exc:
        raise TokenError(f"card signature invalid: {exc}") from exc
