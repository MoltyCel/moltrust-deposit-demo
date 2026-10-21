# moltrust-deposit-demo

A reference savings agent that moves deposits between two **simulated** banks under an open
payment mandate, and a receipt per sweep that anyone can recompute offline.

> **Demo, not a financial product.** No investment advice, no banking service, no offer. The
> banks, their rates and their deposit guarantee schemes are made up test data. Nothing here
> touches a real account, initiates a payment or writes to a chain.

## What it shows

An agent that chases interest rates is a payment agent with a very specific licence: it may
move money, but only between banks that stay insured, only when the extra interest is worth
the move, only after the money has settled, and only so often. Those four sentences are the
mandate. The point of this demo is that they are **checked before the agent signs**, by code
anyone can run, against evidence anyone can re-verify — not reconstructed afterwards from a
bank statement.

The check is not written here. It is `check_transfer` from
[`moltrust_enforce.deposit`](https://github.com/MoltyCel/moltrust-api/tree/main/sdk/python),
the same kernel a production agent runs, with the five `ch.moltrust.deposit.*` constraint
types of Verifiable Intent:

| Constraint | In this mandate |
|---|---|
| `min_hold_days` | funds must sit 30 days before they may leave again |
| `sweep_threshold_bp` | the target must pay at least 150 bp more (APY) |
| `max_sweeps_per_period` | at most 2 sweeps per calendar month |
| `insured_counterparty_only` | target covered by `SIM-DGS-A`/`SIM-DGS-B`, balance ≤ 100,000.00 USD |
| `execution_window_hours` | execute at `trigger_time + hash-derived offset`, within 6 h |

## Run it

Python 3.10 or newer.

```bash
pip install -r requirements.txt
python -m demo.run_scenarios
```

Five sweeps are planned and decided, one receipt each into `receipts/`:

```
scenario                             disposition  reason
sweep-allowed                        allow        -
sweep-forbidden-holding-period       forbid       holding period not elapsed
sweep-forbidden-rate-spread          forbid       rate difference below threshold
sweep-forbidden-uninsured-target     forbid       target not covered
sweep-held-execution-window          hold         outside execution window
```

`hold` is not a refusal. The sweep is fine, it is simply not its minute yet: every agent
under the same mandate gets its own delay inside the six hours, derived from
`sha256(mandate_id|trigger_id)`, so a rate change does not put a thousand agents at the
counter in the same second.

## Check a receipt without trusting the run

```bash
python -m demo.verify_receipt receipts/sweep-allowed.json
```

The verifier recomputes, it does not read conclusions:

1. every rate and insurance observation in the bundle is signed by a source the **pinned**
   trust list names, and was valid at the trigger — `verify_deposit_evidence`, offline;
2. the execution offset, from the two identifiers alone;
3. the disposition and the reason, by running the kernel again on the receipt's own mandate,
   transfer, history and evidence.

It exits non-zero if any of the three disagrees with the receipt. Change a rate in a receipt
by one basis point and the evidence check rejects the bundle — the statement no longer
matches its signature, and the bundle commit no longer matches its items — while the kernel
independently turns the `allow` into a `forbid`. Change only the recorded decision and the
kernel contradicts it on its own.

## What is in a receipt

An AER evidence bundle (the signed observations, bound to this mandate and this transfer,
with `decision_timestamp` equal to the trigger time), the mandate, the planned transfer, the
transfer history the balances come from, and the decision with the constraint that made it.
The kernel reads no clock and opens no connection — rates and insurance status reach it only
through the bundle, which is why the same receipt yields the same decision on any machine.

## Layout

```
demo/sources.py        the two simulated banks, the two evidence sources, the trust list
demo/run_scenarios.py  the agent: plans five sweeps, decides each, writes the receipts
demo/verify_receipt.py the offline check
receipts/              written by the run, including trust-list.json
```

The demo signing keys are derived from a published string in `demo/sources.py`. They are
deterministic on purpose, so that a trust list can be rebuilt from the source file alone.
They are demo keys and secure nothing.

## Licence

Apache-2.0, see [LICENSE](LICENSE).
