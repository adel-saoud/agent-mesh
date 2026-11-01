"""Per-agent signing keys and the shared JWKS the mesh trusts.

Each principal gets two ES256 keys with separate purposes, so a leaked request-token key cannot
forge agent cards and the other way round:

- `<name>:card` signs the agent card (A2A spec section on card signatures).
- `<name>:token` signs short-lived request tokens and confirmation tokens.

The demo writes all public keys to one `jwks.json` that every service reads. In production each
issuer would serve its own JWKS over HTTPS and verifiers would fetch and cache it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWK
from jwt.algorithms import ECAlgorithm

ALGORITHM = "ES256"
"""ECDSA P-256: small keys and signatures, supported by PyJWT and the a2a-sdk card signer."""
JWKS_FILENAME = "jwks.json"
"""Public keys of every principal, inside `keys_dir`; the only key file a verifier reads."""


class KeyUse(StrEnum):
    """What a key may sign. Part of the key id, so a verifier can refuse the wrong kind."""

    CARD = "card"
    TOKEN = "token"  # noqa: S105 - a key purpose label, not a secret


def kid_for(principal: str, use: KeyUse) -> str:
    """Key id `<principal>:<use>`, the `kid` header on every JWS the mesh signs."""
    return f"{principal}:{use.value}"


def issuer_of(kid: str) -> str:
    """The principal a key id belongs to. Verifiers bind `iss` to this to stop key confusion."""
    return kid.rsplit(":", 1)[0]


@dataclass(frozen=True)
class SigningKey:
    """A private key with its id, ready for PyJWT and the a2a-sdk card signer."""

    kid: str
    private_pem: bytes


def _private_key_path(keys_dir: Path, kid: str) -> Path:
    return keys_dir / f"{kid.replace(':', '.')}.pem"


def generate_keys(keys_dir: Path, principals: list[str]) -> None:
    """Create missing key pairs and rewrite the JWKS. Existing private keys are never replaced."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    public_jwks: list[dict[str, Any]] = []
    for principal in principals:
        for use in KeyUse:
            kid = kid_for(principal, use)
            path = _private_key_path(keys_dir, kid)
            if not path.exists():
                key = ec.generate_private_key(ec.SECP256R1())
                path.write_bytes(
                    key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption(),
                    )
                )
                path.chmod(0o600)
            private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(private_key, ec.EllipticCurvePrivateKey):
                raise TypeError(f"{path} is not an EC private key")
            jwk = dict(ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True))
            jwk.update({"kid": kid, "alg": ALGORITHM, "use": "sig"})
            public_jwks.append(jwk)
    (keys_dir / JWKS_FILENAME).write_text(json.dumps({"keys": public_jwks}, indent=2) + "\n")


def load_signing_key(keys_dir: Path, principal: str, use: KeyUse) -> SigningKey:
    """Load one of the principal's own private keys."""
    kid = kid_for(principal, use)
    return SigningKey(kid=kid, private_pem=_private_key_path(keys_dir, kid).read_bytes())


class TrustStore:
    """Public keys the mesh accepts, looked up by `kid`."""

    def __init__(self, jwks: dict[str, Any]) -> None:
        self._keys = {k["kid"]: PyJWK(k, algorithm=ALGORITHM) for k in jwks.get("keys", [])}

    @classmethod
    def from_dir(cls, keys_dir: Path) -> TrustStore:
        """Load the JWKS that `generate_keys` wrote."""
        return cls(json.loads((keys_dir / JWKS_FILENAME).read_text()))

    def get(self, kid: str | None) -> PyJWK | None:
        """The public key for `kid`, or None, which every caller treats as a refusal."""
        return None if kid is None else self._keys.get(kid)
