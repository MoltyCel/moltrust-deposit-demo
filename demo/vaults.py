"""The vault profile: the same mandate, with the insured-bank rule swapped for a vault allowlist.

A yield agent that moves stablecoins between on-chain vaults is the savings agent of the
banking profile with one change. There is no deposit guarantee scheme for a vault, so
"only insured banks" becomes "only the vaults on this list, and only while the audit the
list was approved on still covers them". Holding period, rate spread, sweep limit and
execution window stay exactly as they are and are decided by the same kernel.

The three vaults below are simulated. Their addresses are derived from a published string
on Base Sepolia (chain id 84532), hold no code there, and nothing in this demo sends a
transaction to any chain.

The constraint this module adds:

- `ch.moltrust.vault.audited_vault_only` — `{"vaults": [{"vault", "audit"}, ...],
  "max_position": {"currency", "amount"}}`. The target must be a listed vault, named by its
  CAIP-10 account id (`<namespace>:<chain reference>:<address>`), a signed audit observation valid at the trigger must name the same audit
  report the mandate lists for it, and the position in the target after the transfer stays
  at or below `max_position`.

It sits where `insured_counterparty_only` sits in the kernel's order: after holding period,
rate spread and sweep limit, before the execution window. It lives outside the
`ch.moltrust.deposit.` namespace, so the kernel leaves it untouched and still binds it,
because the evidence bundle commits to the whole mandate.

The format is chain-neutral on purpose: the chain is part of the vault's id, not of the
mandate. The demo runs on Base Sepolia only; what a second chain would change is written
down in the README.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from moltrust_enforce.deposit import (
    FORBID,
    TransferDecision,
    check_transfer,
)
from moltrust_enforce.evidence import evidence_values, parse_timestamp, query_key

from .sources import signed_item

AUDITED_VAULT_ONLY = "ch.moltrust.vault.audited_vault_only"
AUDIT_QUERY_KIND = "ch.moltrust.vault.audit"

#: Base Sepolia, as a CAIP-2 chain id. Testnet only; there is no mainnet in this demo.
CHAIN = "eip155:84532"

MAX_VAULTS = 64

# CAIP-10: namespace (3-8), chain reference (1-32), account address (1-128).
_CAIP10 = re.compile(r"^[-a-z0-9]{3,8}:[-_a-zA-Z0-9]{1,32}:[-.%a-zA-Z0-9]{1,128}$")


def vault_address(name: str) -> str:
    """Deterministic, code-free address for a simulated vault. Public derivation."""
    digest = hashlib.sha256(b"moltrust:deposit-demo-vault:" + name.encode("utf-8")).digest()
    return "0x" + digest[-20:].hex()


def vault_id(name: str) -> str:
    """CAIP-10 account id of a simulated vault on Base Sepolia."""
    return f"{CHAIN}:{vault_address(name)}"


# The three simulated vaults. Gamma pays the most and is not on the mandate's list.
VAULTS = {
    "sim-vault-alpha": {"name": "Simulated Vault Alpha", "apy_bp": 390,
                        "audit": "SIM-AUDIT-2026-A"},
    "sim-vault-beta": {"name": "Simulated Vault Beta", "apy_bp": 610,
                       "audit": "SIM-AUDIT-2026-B"},
    "sim-vault-gamma": {"name": "Simulated Vault Gamma", "apy_bp": 720,
                        "audit": "SIM-AUDIT-2026-G"},
}


def audit_query(vault: str) -> Dict[str, str]:
    """The AER query an audit observation answers. Value: the identifier of the audit
    report that covers the code deployed at `vault` at the time of the observation, or any
    other string for none."""
    return {"kind": AUDIT_QUERY_KIND, "vault": vault}


def audit_item(vault: str, report: str, valid_from: str, valid_until: str,
               nonce: str) -> Dict[str, Any]:
    """A signed audit observation from the simulated audit source."""
    return signed_item("sim-audit", audit_query(vault), report, valid_from, valid_until,
                       nonce)


# ------------------------------------------------------------- constraint parsing

def _money_ok(m: Any) -> bool:
    return (isinstance(m, dict) and isinstance(m.get("currency"), str)
            and len(m["currency"]) == 3 and m["currency"].isascii()
            and m["currency"].isalpha() and m["currency"].isupper()
            and isinstance(m.get("amount"), int) and not isinstance(m["amount"], bool)
            and m["amount"] >= 0)


def vault_constraint_problem(c: Any) -> Optional[str]:
    """None if `c` is a well-formed `audited_vault_only` constraint, otherwise the reason."""
    if not isinstance(c, dict) or c.get("type") != AUDITED_VAULT_ONLY:
        return "not an audited_vault_only constraint"
    vaults = c.get("vaults")
    if not isinstance(vaults, list) or len(vaults) > MAX_VAULTS:
        return f"vaults is not an array of at most {MAX_VAULTS} entries"
    seen = set()
    for entry in vaults:
        if not isinstance(entry, dict):
            return "vaults contains an entry that is not an object"
        v, a = entry.get("vault"), entry.get("audit")
        if not isinstance(v, str) or not _CAIP10.match(v):
            return f"vault {v!r} is not a CAIP-10 account id"
        # EVM addresses are case-insensitive, so one spelling is fixed: lower case. Other
        # namespaces (Solana's base58, for one) are case-sensitive and stay as written.
        if v.startswith("eip155:") and v != v.lower():
            return f"vault {v} is not in lower case"
        if not isinstance(a, str) or not a:
            return f"vault {v} names no audit report"
        if v in seen:
            return f"vault {v} is listed twice"
        seen.add(v)
    if not _money_ok(c.get("max_position")):
        return "max_position is not an amount in ISO 4217 minor units"
    return None


# ------------------------------------------------------------------ the check

def position_at(vault: str, currency: str, history: dict) -> Tuple[int, Optional[str]]:
    """Position in `vault` from the opening balance and the history. `(amount, problem)`."""
    opening = history.get("opening_balances", {}).get(vault)
    amount = 0
    if opening is not None:
        if opening["currency"] != currency:
            return 0, "opening position currency differs from max_position currency"
        amount = opening["amount"]
    for h in history["transfers"]:
        if vault not in (h["source"], h["target"]):
            continue
        if h["amount"]["currency"] != currency:
            return 0, "history currency at the target differs from max_position currency"
        if h["target"] == vault:
            amount += h["amount"]["amount"]
        if h["source"] == vault:
            amount -= h["amount"]["amount"]
    if amount < 0:
        return 0, "history leaves a negative position at the target"
    return amount, None


def check_vault_transfer(mandate: Any, transfer: Any, history: Any,
                         bundle: Any = None) -> TransferDecision:
    """Check one planned vault transfer.

    The kernel decides holding period, rate spread, sweep limit and execution window, and
    validates mandate, transfer, history and the bundle's binding. A `forbid` from it stands.
    Otherwise every `audited_vault_only` constraint is checked, and a failure there is a
    `forbid` that comes before the kernel's `hold`, as coverage comes before the window in
    the kernel itself. If all pass, the kernel's decision stands.
    """
    decision = check_transfer(mandate, transfer, history, bundle)
    if decision.disposition == FORBID:
        return decision

    def forbid(reason, index=None, detail=None):
        return TransferDecision(FORBID, reason, AUDITED_VAULT_ONLY, index, detail)

    picked: List[Tuple[int, dict]] = [
        (i, c) for i, c in enumerate(mandate["constraints"])
        if isinstance(c, dict) and c.get("type") == AUDITED_VAULT_ONLY
    ]
    if not picked:
        return decision
    if bundle is None:
        return forbid("no audit evidence in the bundle")
    values = evidence_values(bundle)
    trigger = parse_timestamp(transfer["trigger_time"])
    target, amount = transfer["target"], transfer["amount"]

    for i, c in picked:
        problem = vault_constraint_problem(c)
        if problem is not None:
            return forbid(f"constraints[{i}]: {problem}", i)
        listed = {e["vault"]: e["audit"] for e in c["vaults"]}
        if target not in listed:
            return forbid("target vault not on the list", i,
                          {"target": target, "vaults": sorted(listed)})
        statement = values.get(query_key(audit_query(target)) or "")
        if statement is None:
            return forbid("no audit evidence in the bundle", i)
        start = parse_timestamp(statement["valid_from"])
        end = parse_timestamp(statement["valid_until"])
        if start is None or end is None or not start <= trigger <= end:
            return forbid("audit evidence window does not cover the trigger time", i)
        if statement["value"] != listed[target]:
            return forbid("audit no longer covers the vault", i,
                          {"listed_audit": listed[target], "observed": statement["value"]})
        limit = c["max_position"]
        if amount["currency"] != limit["currency"]:
            return forbid("transfer currency differs from max_position currency", i)
        position, problem = position_at(target, limit["currency"], history)
        if problem is not None:
            return forbid(problem, i)
        if position + amount["amount"] > limit["amount"]:
            return forbid("above position limit", i,
                          {"position": position, "amount": amount["amount"],
                           "max_position": limit["amount"], "currency": limit["currency"]})
    return decision
