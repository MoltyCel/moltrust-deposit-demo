"""The two simulated banks, the two evidence sources, and the pinned trust list.

Nothing here is a real institution and nothing here is a secret. The signing keys are
derived from a published string, so every run on every machine gets the same keys and a
third party can rebuild the trust list from this file alone. That is the point: the
receipts must be checkable without asking us for anything.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Any, Dict

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from moltrust_enforce.evidence import (
    evidence_payload_bytes,
    make_envelope,
    make_statement,
    pae,
)

EVIDENCE_PAYLOAD_TYPE = "application/vnd.moltrust.aer-evidence+json"

# The two simulated banks. Rates are integers in basis points (415 = 4.15 %), balances are
# ISO 4217 minor units, as everywhere in Verifiable Intent.
BANKS = {
    "sim-bank-nord": {
        "name": "Simulated Bank Nord",
        "apy_bp": 180,
        "insurance_scheme": "SIM-DGS-A",
    },
    "sim-bank-sued": {
        "name": "Simulated Bank Sued",
        "apy_bp": 425,
        "insurance_scheme": "SIM-DGS-B",
    },
}

# Who may say what. A rate observation signed by the insurance source is not a rate.
SOURCES = ("sim-rates", "sim-insurance")


def source_key(name: str) -> Ed25519PrivateKey:
    """Deterministic demo key for an evidence source. Public derivation, no secret."""
    seed = hashlib.sha256(b"moltrust:deposit-demo-source:" + name.encode("utf-8")).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def source_id(name: str) -> str:
    return f"did:moltrust:{name}"


def public_key_b64(name: str) -> str:
    raw = source_key(name).public_key().public_bytes_raw()
    return base64.b64encode(raw).decode("ascii")


def trust_list() -> Dict[str, Any]:
    """The list the verifier brings along. Pinned, never fetched."""
    return {
        "trust_list_version": 1,
        "sources": {
            source_id(n): {
                "keys": [
                    {
                        "algorithm": "ed25519",
                        "keyid": f"key-{n}",
                        "public_key": public_key_b64(n),
                    }
                ]
            }
            for n in SOURCES
        },
    }


def signed_item(source: str, query: Any, value: Any, valid_from: str, valid_until: str,
                nonce: str) -> Dict[str, Any]:
    """One signed AER evidence envelope: the statement plus an Ed25519 signature over its
    DSSE pre-authentication encoding, exactly as `verify_deposit_evidence` expects."""
    statement = make_statement(source_id(source), query, value, valid_from, valid_until,
                               nonce)
    payload = evidence_payload_bytes(statement)
    if payload is None:
        raise ValueError("statement is not canonicalizable")
    signature = source_key(source).sign(pae(EVIDENCE_PAYLOAD_TYPE, payload))
    return make_envelope(
        statement,
        [{"keyid": f"key-{source}", "sig": base64.b64encode(signature).decode("ascii")}],
    )
