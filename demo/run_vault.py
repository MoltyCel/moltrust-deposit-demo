"""The yield agent: twelve moves between simulated vaults on Base Sepolia, one receipt each.

Run it with one command:

    python -m demo.run_vault

Same mandate shape as the savings agent, with the insured-bank rule replaced by a list of
approved, audited vaults (`demo/vaults.py`). Every constraint gets two cases: one allowed
on its boundary, one refused just past it. The execution window refuses with `hold`, not
`forbid`; the vault list refuses three ways (not listed, audit lapsed, position too large).

No real vault, no real protocol, no mainnet, no transaction on any chain.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any, Dict, List

from moltrust_enforce.deposit import TOLERANCE_SECONDS, rate_query, window_offset_seconds
from moltrust_enforce.evidence import build_bundle, parse_timestamp

from .sources import signed_item, trust_list
from .vaults import (
    AUDITED_VAULT_ONLY,
    CHAIN,
    VAULTS,
    audit_item,
    check_vault_transfer,
    vault_id,
)

RECEIPT_VERSION = 1
PROFILE = "vault"
MANDATE_ID = "mandate-sim-vault-0001"
CURRENCY = "USD"

TRIGGER_TIME = "2026-10-15T08:00:00Z"
EVIDENCE_FROM = "2026-10-15T06:00:00Z"
EVIDENCE_UNTIL = "2026-10-15T10:00:00Z"

WINDOW_HOURS = 6
DAY = 86400

ALPHA, BETA, GAMMA = (vault_id(n) for n in ("sim-vault-alpha", "sim-vault-beta",
                                            "sim-vault-gamma"))

# The mandate. Four constraint types as in the banking profile, unchanged; the vault list
# stands where insured_counterparty_only stands there. Gamma is deliberately not listed.
MANDATE: Dict[str, Any] = {
    "mandate_id": MANDATE_ID,
    "constraints": [
        {"type": "ch.moltrust.deposit.min_hold_days", "days": 30},
        {
            "type": "ch.moltrust.deposit.sweep_threshold_bp",
            "min_rate_diff_bp": 150,
            "rate_basis": "APY",
        },
        {"type": "ch.moltrust.deposit.max_sweeps_per_period", "max": 2, "period": "MNTH"},
        {
            "type": AUDITED_VAULT_ONLY,
            "vaults": [
                {"vault": ALPHA, "audit": VAULTS["sim-vault-alpha"]["audit"]},
                {"vault": BETA, "audit": VAULTS["sim-vault-beta"]["audit"]},
            ],
            "max_position": {"currency": CURRENCY, "amount": 10_000_000},
        },
        {"type": "ch.moltrust.deposit.execution_window_hours", "hours": WINDOW_HOURS},
    ],
}

AMOUNT = {"currency": CURRENCY, "amount": 1_000_000}          # 10,000.00 USD

OLD_ARRIVAL = {
    "source": "sim-onramp",
    "target": ALPHA,
    "amount": {"currency": CURRENCY, "amount": 5_000_000},
    "exec_time": "2026-07-01T08:00:00Z",
}

OPENING = {ALPHA: {"currency": CURRENCY, "amount": 0},
           BETA: {"currency": CURRENCY, "amount": 0}}

RATES = {ALPHA: VAULTS["sim-vault-alpha"]["apy_bp"],
         BETA: VAULTS["sim-vault-beta"]["apy_bp"],
         GAMMA: VAULTS["sim-vault-gamma"]["apy_bp"]}
AUDITS = {ALPHA: VAULTS["sim-vault-alpha"]["audit"],
          BETA: VAULTS["sim-vault-beta"]["audit"],
          GAMMA: VAULTS["sim-vault-gamma"]["audit"]}


def _format(epoch: int) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _on_time(trigger_id: str) -> int:
    """`trigger_time + offset`, recomputable from the two identifiers alone."""
    return parse_timestamp(TRIGGER_TIME) + window_offset_seconds(
        MANDATE_ID, trigger_id, WINDOW_HOURS)


def _sweep(when: str) -> dict:
    """An earlier move out of Alpha in the same month; it counts toward the sweep limit."""
    return {"source": ALPHA, "target": BETA, "amount": AMOUNT, "exec_time": when}


def scenarios() -> List[Dict[str, Any]]:
    """Twelve moves. Each names the constraint it exercises and on which side of it it lies."""
    out: List[Dict[str, Any]] = []

    def add(sid, exercises, trigger, **kw):
        spec = {
            "id": sid,
            "exercises": exercises,
            "trigger_id": trigger,
            "source": ALPHA,
            "target": BETA,
            "exec_epoch": _on_time(trigger),
            "rates": dict(RATES),
            "audits": dict(AUDITS),
            "history": [OLD_ARRIVAL],
            "opening": dict(OPENING),
        }
        spec.update(kw)
        out.append(spec)

    # Holding period: the money reached Alpha exactly 30 days before the move, or one
    # second later, which is 29 full days.
    t = "vault-rate-0101"
    add("vault-allowed-hold-boundary", "min_hold_days: exactly 30 days held", t,
        history=[{**OLD_ARRIVAL, "exec_time": _format(_on_time(t) - 30 * DAY)}])
    t = "vault-rate-0102"
    add("vault-forbidden-holding-period", "min_hold_days: 29 days held", t,
        history=[{**OLD_ARRIVAL, "exec_time": _format(_on_time(t) - 30 * DAY + 1)}])

    # Rate spread: Beta pays exactly 150 bp more than Alpha, or 149.
    add("vault-allowed-rate-boundary", "sweep_threshold_bp: spread exactly 150 bp",
        "vault-rate-0103", rates={**RATES, BETA: RATES[ALPHA] + 150})
    add("vault-forbidden-rate-spread", "sweep_threshold_bp: spread 149 bp",
        "vault-rate-0104", rates={**RATES, BETA: RATES[ALPHA] + 149})

    # Sweep limit: two per calendar month. One earlier move this month, or two.
    add("vault-allowed-sweep-limit-boundary", "max_sweeps_per_period: second of two",
        "vault-rate-0105", history=[OLD_ARRIVAL, _sweep("2026-10-02T09:00:00Z")])
    add("vault-forbidden-sweep-limit", "max_sweeps_per_period: third of two",
        "vault-rate-0106",
        history=[OLD_ARRIVAL, _sweep("2026-10-02T09:00:00Z"), _sweep("2026-10-05T09:00:00Z")])

    # Vault list: listed and audited, with the position ending exactly at the limit; then
    # three refusals — not listed, audit no longer current, position one cent over.
    add("vault-allowed-position-boundary",
        "audited_vault_only: listed, audit current, position exactly at the limit",
        "vault-rate-0107", opening={**OPENING, BETA: {"currency": CURRENCY,
                                                      "amount": 9_000_000}})
    add("vault-forbidden-unlisted-vault", "audited_vault_only: target not on the list",
        "vault-rate-0108", target=GAMMA)
    add("vault-forbidden-audit-lapsed",
        "audited_vault_only: code changed since the listed audit",
        "vault-rate-0109", audits={**AUDITS, BETA: "NONE"})
    add("vault-forbidden-above-position-limit",
        "audited_vault_only: position one cent over the limit",
        "vault-rate-0110", opening={**OPENING, BETA: {"currency": CURRENCY,
                                                      "amount": 9_000_001}})

    # Execution window: at the edge of the tolerance, or at the trigger itself.
    t = "vault-rate-0111"
    add("vault-allowed-window-boundary",
        f"execution_window_hours: {TOLERANCE_SECONDS} s after the computed time", t,
        exec_epoch=_on_time(t) + TOLERANCE_SECONDS)
    t = "vault-rate-0112"
    add("vault-held-execution-window", "execution_window_hours: at the trigger time", t,
        exec_epoch=parse_timestamp(TRIGGER_TIME))
    return out


def _evidence(spec: Dict[str, Any]) -> List[dict]:
    """Signed rate observations for both vaults of the move, and the audit observation for
    the target. The agent does not pick these values; the sources publish them."""
    items = []
    for vault in (spec["source"], spec["target"]):
        items.append(signed_item("sim-rates", rate_query(vault, "APY"), spec["rates"][vault],
                                 EVIDENCE_FROM, EVIDENCE_UNTIL,
                                 f"{spec['id']}-rate-{vault}"))
    items.append(audit_item(spec["target"], spec["audits"][spec["target"]],
                            EVIDENCE_FROM, EVIDENCE_UNTIL, f"{spec['id']}-audit"))
    return items


def receipt_for(spec: Dict[str, Any]) -> Dict[str, Any]:
    transfer = {
        "mandate_id": MANDATE_ID,
        "source": spec["source"],
        "target": spec["target"],
        "amount": AMOUNT,
        "trigger_id": spec["trigger_id"],
        "trigger_time": TRIGGER_TIME,
        "exec_time": _format(spec["exec_epoch"]),
    }
    history = {"transfers": spec["history"], "opening_balances": spec["opening"]}
    bundle = build_bundle(_evidence(spec), MANDATE, transfer, TRIGGER_TIME)
    decision = check_vault_transfer(MANDATE, transfer, history, bundle)
    return {
        "receipt_version": RECEIPT_VERSION,
        "profile": PROFILE,
        "chain": CHAIN,
        "scenario": spec["id"],
        "exercises": spec["exercises"],
        "mandate": MANDATE,
        "transfer": transfer,
        "history": history,
        "bundle": bundle,
        "decision": {
            "disposition": decision.disposition,
            "reason": decision.reason,
            "constraint_type": decision.constraint_type,
            "constraint_index": decision.constraint_index,
            "detail": decision.detail,
        },
        "window_offset_seconds": window_offset_seconds(
            MANDATE_ID, spec["trigger_id"], WINDOW_HOURS),
        "trust_list_version": trust_list()["trust_list_version"],
    }


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(here, "receipts", "vault")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(here, "receipts", "trust-list.json"), "w") as fh:
        json.dump(trust_list(), fh, indent=2, sort_keys=True)
        fh.write("\n")

    print(f"Simulated vaults on {CHAIN} (Base Sepolia), no code at these addresses")
    listed = {e["vault"] for c in MANDATE["constraints"] if c["type"] == AUDITED_VAULT_ONLY
              for e in c["vaults"]}
    for name, v in VAULTS.items():
        vid = vault_id(name)
        print(f"  {name:16s} {vid.split(':')[-1]}  {v['apy_bp'] / 100:.2f} % APY  "
              f"{'listed' if vid in listed else 'not listed'}")
    print()
    print(f"{'scenario':38s} {'disposition':12s} reason")
    print("-" * 100)

    specs = scenarios()
    for spec in specs:
        receipt = receipt_for(spec)
        with open(os.path.join(out_dir, f"{spec['id']}.json"), "w") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True)
            fh.write("\n")
        d = receipt["decision"]
        print(f"{spec['id']:38s} {d['disposition']:12s} {d['reason'] or '-'}")

    print()
    print(f"{len(specs)} receipts in receipts/vault/. Check them without trusting this run:")
    print("  python -m demo.verify_receipt receipts/vault/*.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
