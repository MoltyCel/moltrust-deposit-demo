"""Check a sweep receipt offline, without trusting the agent that wrote it.

    python -m demo.verify_receipt receipts/sweep-allowed.json
    python -m demo.verify_receipt receipts/vault/*.json

Four things are checked, in this order:

1. Every evidence item in the bundle is signed by a source the pinned trust list names and
   is valid at the bundle's decision timestamp (`verify_deposit_evidence`).
2. The recorded execution offset is the hash of `mandate_id|trigger_id` — recomputed here,
   not read from the receipt.
3. The kernel, run again on the receipt's own mandate, transfer, history and bundle,
   reaches the same disposition and the same reason. A receipt of the vault profile
   (`"profile": "vault"`) is run through `check_vault_transfer`, which calls the same
   kernel and adds the vault list.
4. The receipt states a disposition the kernel knows.

Exit code 0 only if all four hold, for every receipt named. The trust list is rebuilt from `demo.sources`, so this
command needs nothing from the network and nothing from the machine that ran the agent.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict

from moltrust_enforce.deposit import (
    ALLOW,
    FORBID,
    HOLD,
    check_transfer,
    verify_deposit_evidence,
    window_offset_seconds,
)

from .sources import trust_list
from .vaults import check_vault_transfer

WINDOW_HOURS = 6


def verify(receipt: Dict[str, Any]) -> list:
    """List of problems. Empty means the receipt holds."""
    problems = []

    problem = verify_deposit_evidence(receipt["bundle"], trust_list())
    if problem is not None:
        problems.append(f"evidence: {problem}")

    recorded = receipt["decision"]
    if recorded.get("disposition") not in (ALLOW, HOLD, FORBID):
        problems.append(f"decision: unknown disposition {recorded.get('disposition')!r}")

    offset = window_offset_seconds(
        receipt["mandate"]["mandate_id"], receipt["transfer"]["trigger_id"], WINDOW_HOURS)
    if offset != receipt.get("window_offset_seconds"):
        problems.append(f"offset: receipt says {receipt.get('window_offset_seconds')}, "
                        f"recomputed {offset}")

    check = check_vault_transfer if receipt.get("profile") == "vault" else check_transfer
    again = check(receipt["mandate"], receipt["transfer"], receipt["history"],
                  receipt["bundle"])
    if again.disposition != recorded.get("disposition"):
        problems.append(f"kernel: receipt says {recorded.get('disposition')}, "
                        f"kernel says {again.disposition}")
    elif again.reason != recorded.get("reason"):
        problems.append(f"kernel: reason differs — receipt {recorded.get('reason')!r}, "
                        f"kernel {again.reason!r}")

    return problems


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: python -m demo.verify_receipt <receipt.json> [<receipt.json> ...]")
        return 2

    failed = 0
    for i, path in enumerate(argv[1:]):
        if i:
            print()
        failed += check_one(path)
    if len(argv) > 2:
        print()
        print(f"{len(argv) - 1 - failed} of {len(argv) - 1} receipts hold")
    return 1 if failed else 0


def check_one(path: str) -> int:
    with open(path) as fh:
        receipt = json.load(fh)

    problems = verify(receipt)
    decision = receipt["decision"]
    print(f"receipt   {path}")
    print(f"scenario  {receipt['scenario']} ({receipt['exercises']})")
    print(f"decision  {decision['disposition']}  {decision['reason'] or '-'}")
    print(f"offset    {receipt['window_offset_seconds']} s after the trigger, recomputed")

    if problems:
        print("FAIL")
        for p in problems:
            print(f"  {p}")
        return 1
    print("OK — signatures, offset and disposition all recomputed here")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
