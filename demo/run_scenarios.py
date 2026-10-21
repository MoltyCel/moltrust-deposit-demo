"""The reference savings agent: five sweeps between two simulated banks, one receipt each.

Run it with one command:

    python -m demo.run_scenarios

The agent decides nothing on its own. For every planned sweep it calls `check_transfer`
from `moltrust_enforce.deposit` — the same kernel a production agent runs — and writes the
decision together with the evidence it was based on into `receipts/`. Those receipts are
what a third party checks with `demo.verify_receipt`, offline.

No real bank, no real account, no payment initiation, no mainnet.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from moltrust_enforce.deposit import (
    check_transfer,
    insurance_query,
    rate_query,
    window_offset_seconds,
)
from moltrust_enforce.evidence import build_bundle, parse_timestamp

from .sources import BANKS, signed_item, trust_list

RECEIPT_VERSION = 1
MANDATE_ID = "mandate-sim-savings-0001"
CURRENCY = "USD"

TRIGGER_TIME = "2026-10-15T08:00:00Z"
EVIDENCE_FROM = "2026-10-15T06:00:00Z"
EVIDENCE_UNTIL = "2026-10-15T10:00:00Z"

WINDOW_HOURS = 6

# The open mandate the agent carries. All five deposit constraint types, in the order the
# kernel evaluates them.
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
            "type": "ch.moltrust.deposit.insured_counterparty_only",
            "schemes": ["SIM-DGS-A", "SIM-DGS-B"],
            "max_insured_balance": {"currency": CURRENCY, "amount": 10_000_000},
        },
        {"type": "ch.moltrust.deposit.execution_window_hours", "hours": WINDOW_HOURS},
    ],
}

AMOUNT = {"currency": CURRENCY, "amount": 1_000_000}          # 10,000.00 USD

# One arrival at Nord long before the mandate's holding period, so the default case is
# clean. Scenarios that need a fresh arrival add their own.
OLD_ARRIVAL = {
    "source": "sim-bank-extern",
    "target": "sim-bank-nord",
    "amount": {"currency": CURRENCY, "amount": 5_000_000},
    "exec_time": "2026-07-01T08:00:00Z",
}

OPENING = {
    "sim-bank-nord": {"currency": CURRENCY, "amount": 5_000_000},
    "sim-bank-sued": {"currency": CURRENCY, "amount": 0},
}


def _evidence(rates: Dict[str, int], insurance: Dict[str, str], nonce: str) -> List[dict]:
    """Signed rate and insurance observations for this trigger.

    `rates` and `insurance` are what the two sources published; the agent does not get to
    pick them, and the kernel never asks a source itself.
    """
    items = []
    for inst, bp in rates.items():
        items.append(
            signed_item("sim-rates", rate_query(inst, "APY"), bp,
                        EVIDENCE_FROM, EVIDENCE_UNTIL, f"{nonce}-rate-{inst}")
        )
    for inst, scheme in insurance.items():
        items.append(
            signed_item("sim-insurance", insurance_query(inst), scheme,
                        EVIDENCE_FROM, EVIDENCE_UNTIL, f"{nonce}-ins-{inst}")
        )
    return items


def _exec_time(trigger_id: str, on_time: bool) -> str:
    """Execution time of the sweep. On time means `trigger_time + offset`, derived from the
    two identifiers alone — anyone can recompute it."""
    if not on_time:
        return TRIGGER_TIME
    offset = window_offset_seconds(MANDATE_ID, trigger_id, WINDOW_HOURS)
    base = parse_timestamp(TRIGGER_TIME)
    return _format(base + offset)


def _format(epoch: int) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(
        epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def scenarios() -> List[Dict[str, Any]]:
    """The five sweeps. Each names the constraint it is meant to exercise."""
    nord, sued = BANKS["sim-bank-nord"]["apy_bp"], BANKS["sim-bank-sued"]["apy_bp"]
    out: List[Dict[str, Any]] = []

    # 1 — allowed: 245 bp of spread, funds settled since July, Sued is covered.
    out.append({
        "id": "sweep-allowed",
        "exercises": "all five pass",
        "trigger_id": "rate-change-0042",
        "source": "sim-bank-nord",
        "target": "sim-bank-sued",
        "on_time": True,
        "rates": {"sim-bank-nord": nord, "sim-bank-sued": sued},
        "insurance": {"sim-bank-sued": "SIM-DGS-B"},
        "history": [OLD_ARRIVAL],
    })

    # 2 — forbidden: the money arrived at Nord five days ago, the mandate says thirty.
    out.append({
        "id": "sweep-forbidden-holding-period",
        "exercises": "ch.moltrust.deposit.min_hold_days",
        "trigger_id": "rate-change-0043",
        "source": "sim-bank-nord",
        "target": "sim-bank-sued",
        "on_time": True,
        "rates": {"sim-bank-nord": nord, "sim-bank-sued": sued},
        "insurance": {"sim-bank-sued": "SIM-DGS-B"},
        "history": [OLD_ARRIVAL, {
            "source": "sim-bank-extern",
            "target": "sim-bank-nord",
            "amount": AMOUNT,
            "exec_time": "2026-10-10T08:00:00Z",
        }],
    })

    # 3 — forbidden: Sued cut its rate to 3.00 %, 120 bp of spread against a 150 bp floor.
    out.append({
        "id": "sweep-forbidden-rate-spread",
        "exercises": "ch.moltrust.deposit.sweep_threshold_bp",
        "trigger_id": "rate-change-0044",
        "source": "sim-bank-nord",
        "target": "sim-bank-sued",
        "on_time": True,
        "rates": {"sim-bank-nord": nord, "sim-bank-sued": 300},
        "insurance": {"sim-bank-sued": "SIM-DGS-B"},
        "history": [OLD_ARRIVAL],
    })

    # 4 — forbidden: Sued's scheme membership lapsed, the mandate only allows covered banks.
    out.append({
        "id": "sweep-forbidden-uninsured-target",
        "exercises": "ch.moltrust.deposit.insured_counterparty_only",
        "trigger_id": "rate-change-0045",
        "source": "sim-bank-nord",
        "target": "sim-bank-sued",
        "on_time": True,
        "rates": {"sim-bank-nord": nord, "sim-bank-sued": sued},
        "insurance": {"sim-bank-sued": "NONE"},
        "history": [OLD_ARRIVAL],
    })

    # 5 — held: nothing is wrong with the sweep, it is simply not its minute yet.
    out.append({
        "id": "sweep-held-execution-window",
        "exercises": "ch.moltrust.deposit.execution_window_hours",
        "trigger_id": "rate-change-0046",
        "source": "sim-bank-nord",
        "target": "sim-bank-sued",
        "on_time": False,
        "rates": {"sim-bank-nord": nord, "sim-bank-sued": sued},
        "insurance": {"sim-bank-sued": "SIM-DGS-B"},
        "history": [OLD_ARRIVAL],
    })
    return out


def receipt_for(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Decide one sweep and build its receipt."""
    transfer = {
        "mandate_id": MANDATE_ID,
        "source": spec["source"],
        "target": spec["target"],
        "amount": AMOUNT,
        "trigger_id": spec["trigger_id"],
        "trigger_time": TRIGGER_TIME,
        "exec_time": _exec_time(spec["trigger_id"], spec["on_time"]),
    }
    history = {"transfers": spec["history"], "opening_balances": OPENING}
    bundle = build_bundle(
        _evidence(spec["rates"], spec["insurance"], spec["id"]),
        MANDATE, transfer, TRIGGER_TIME,
    )
    decision = check_transfer(MANDATE, transfer, history, bundle)
    return {
        "receipt_version": RECEIPT_VERSION,
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
    out_dir = os.path.join(here, "receipts")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "trust-list.json"), "w") as fh:
        json.dump(trust_list(), fh, indent=2, sort_keys=True)
        fh.write("\n")

    print("Simulated banks")
    for inst, bank in BANKS.items():
        print(f"  {inst:16s} {bank['name']:24s} {bank['apy_bp'] / 100:.2f} % APY  "
              f"scheme {bank['insurance_scheme']}")
    print()
    print(f"{'scenario':36s} {'disposition':12s} reason")
    print("-" * 100)

    for spec in scenarios():
        receipt = receipt_for(spec)
        path = os.path.join(out_dir, f"{spec['id']}.json")
        with open(path, "w") as fh:
            json.dump(receipt, fh, indent=2, sort_keys=True)
            fh.write("\n")
        d = receipt["decision"]
        print(f"{spec['id']:36s} {d['disposition']:12s} {d['reason'] or '-'}")

    print()
    print(f"{len(scenarios())} receipts in receipts/. Check one without trusting this run:")
    print("  python -m demo.verify_receipt receipts/sweep-allowed.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
