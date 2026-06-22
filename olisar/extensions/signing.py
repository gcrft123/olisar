"""Ed25519 signing & verification for ``.olx`` extension bundles.

The ``content_hash`` in a bundle only detects *accidental* corruption — anyone who edits
the source can recompute it. A signature binds that hash to a keypair the publisher
controls, so an importer can tell (a) who produced the bundle and (b) that it hasn't been
altered since. There's no central authority in the file-sharing phase, so this is
trust-on-first-use: the public key travels in the bundle, the importer verifies the
signature against it and sees a stable fingerprint to recognise the same publisher again.

Each bot has one signing identity (``SigningIdentity``, created lazily on first export).
The private key never leaves the server. We sign the bundle's ``content_hash`` string,
which already commits to id + version + permissions + source.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import TYPE_CHECKING

log = logging.getLogger("olisar.extensions.signing")

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from olisar.db.models import SigningIdentity

ALGO = "ed25519"


def available() -> bool:
    """Whether the crypto backend is importable (surfaced on /api/health)."""
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
        return True
    except Exception:
        return False


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def fingerprint(public_key_b64: str) -> str:
    """A short, stable id for a public key — what the operator sees ("signed by …")."""
    digest = hashlib.sha256(_b64d(public_key_b64)).hexdigest()
    return "sha256:" + digest[:32]


def generate() -> tuple[str, str]:
    """Make a new keypair → ``(private_b64, public_b64)`` (raw 32-byte keys)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_raw = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return _b64e(priv_raw), _b64e(pub_raw)


def sign(private_key_b64: str, message: str) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.from_private_bytes(_b64d(private_key_b64))
    return _b64e(priv.sign(message.encode("utf-8")))


def verify(public_key_b64: str, message: str, signature_b64: str) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        pub = Ed25519PublicKey.from_public_bytes(_b64d(public_key_b64))
        pub.verify(_b64d(signature_b64), message.encode("utf-8"))
        return True
    except Exception:  # InvalidSignature, malformed key/sig — all mean "not verified"
        return False


def sign_bundle(doc: dict, private_key_b64: str, public_key_b64: str) -> dict:
    """Sign a built bundle in place: signs its ``content_hash`` and embeds the signature,
    public key and algorithm so an importer can verify offline."""
    content_hash = doc.get("content_hash")
    if not content_hash:
        return doc  # nothing to bind a signature to
    doc["signature_algo"] = ALGO
    doc["public_key"] = public_key_b64
    doc["signature"] = sign(private_key_b64, content_hash)
    return doc


def verify_bundle(doc: dict, content_hash: str) -> tuple[str, str | None, str | None]:
    """Check a bundle's signature against the *authoritative* content hash (recomputed by
    the bundle parser, so it already matches the source). Returns
    ``(status, fingerprint, public_key)`` where status is ``unsigned``/``valid``/``invalid``."""
    sig = doc.get("signature")
    pub = doc.get("public_key")
    if not sig or not pub:
        return "unsigned", None, None
    ok = verify(pub, content_hash, sig)
    return ("valid" if ok else "invalid"), (fingerprint(pub) if ok else None), pub


async def ensure_identity(session: "AsyncSession") -> "SigningIdentity":
    """Return this bot's signing identity, creating it on first use."""
    from olisar.db.models import SigningIdentity

    ident = await session.get(SigningIdentity, 1)
    if ident is None:
        priv, pub = generate()
        ident = SigningIdentity(
            id=1, algo=ALGO, private_key=priv, public_key=pub, fingerprint=fingerprint(pub)
        )
        session.add(ident)
        log.info("created signing identity %s", ident.fingerprint)
    return ident


def self_check() -> bool:
    """Generate a keypair and round-trip a signature — surfaced on /api/health."""
    try:
        priv, pub = generate()
        msg = "sha256:probe"
        return verify(pub, msg, sign(priv, msg)) and not verify(pub, "other", sign(priv, msg))
    except Exception:
        log.exception("signing self-check failed")
        return False


__all__ = [
    "ALGO", "available", "fingerprint", "generate", "sign", "verify",
    "sign_bundle", "verify_bundle", "ensure_identity", "self_check",
]
