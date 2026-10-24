# moltrust-deposit-demo

A reference savings agent that moves deposits between two **simulated** banks under an open
payment mandate, and a receipt per sweep that anyone can recompute offline. A second profile
does the same for a yield agent moving stablecoins between **simulated** vaults on Base
Sepolia.

> **Demo, not a financial product.** No investment advice, no banking service, no offer. The
> banks, the vaults, their rates, their deposit guarantee schemes and their audits are made up
> test data. Nothing here touches a real account, initiates a payment or writes to a chain,
> testnet included.

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

## The vault profile

```bash
python -m demo.run_vault
python -m demo.verify_receipt receipts/vault/*.json
```

A yield agent that moves stablecoins between vaults is the savings agent with one rule
swapped. There is no deposit guarantee for a vault, so "only insured banks" becomes "only
the vaults on this list, and only while the audit they were approved on still covers them".
Holding period, rate spread, sweep limit and execution window are the same four constraints
with the same values, decided by the same kernel.

| Constraint | In this mandate |
|---|---|
| `min_hold_days` | 30 days in a vault before the money may leave again |
| `sweep_threshold_bp` | the target vault must pay at least 150 bp more (APY) |
| `max_sweeps_per_period` | at most 2 moves per calendar month |
| `ch.moltrust.vault.audited_vault_only` | target on the list, its listed audit report still current, position ≤ 100,000.00 USD |
| `execution_window_hours` | execute at `trigger_time + hash-derived offset`, within 6 h |

The new constraint lives in `demo/vaults.py`. It names each vault by its CAIP-10 account id
and pins the audit report the vault was approved on. A signed observation from a simulated
audit source says which report covers the code deployed at the vault at the trigger; if it
names a different one, or none, the move is refused. It is outside the kernel's namespace,
so the kernel leaves it alone, and the evidence bundle still binds it because the bundle
commits to the whole mandate.

The three vaults are simulated. Their addresses are derived from a published string,
`sha256("moltrust:deposit-demo-vault:" + name)`, last 20 bytes. On 24.10.2026 all three had
no code and no transactions on Base Sepolia (chain id 84532). No transaction is sent, the
demo reads no chain.

Every constraint is run twice, once on the allowed side of its boundary and once just past
it. Twelve moves, one receipt each into `receipts/vault/`:

```
scenario                               disposition  reason
vault-allowed-hold-boundary            allow        -
vault-forbidden-holding-period         forbid       holding period not elapsed
vault-allowed-rate-boundary            allow        -
vault-forbidden-rate-spread            forbid       rate difference below threshold
vault-allowed-sweep-limit-boundary     allow        -
vault-forbidden-sweep-limit            forbid       sweep limit reached
vault-allowed-position-boundary        allow        -
vault-forbidden-unlisted-vault         forbid       target vault not on the list
vault-forbidden-audit-lapsed           forbid       audit no longer covers the vault
vault-forbidden-above-position-limit   forbid       above position limit
vault-allowed-window-boundary          allow        -
vault-held-execution-window            hold         outside execution window
```

The boundaries: 30 days held against 29 days and 23 h 59 min 59 s, a spread of 150 bp against
149, the second move of the month against the third, a position that ends exactly at the
limit against one cent over, 300 s after the computed time against the trigger itself.
Gamma, the vault that is not listed, pays the highest rate of the three; the agent still
may not move there.

### A second chain: Solana devnet (documented, not built)

The mandate format does not name a chain. The chain is part of each vault's id, so a vault
on Solana devnet is listed as
`solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1:<base58 address>` (CAIP-2 reference per
[the Solana namespace](https://namespaces.chainagnostic.org/solana/caip2)). The constraint
check already keeps such ids exactly as written; only `eip155` addresses are fixed to lower
case, because only those are case-insensitive.

What would change for Solana:

- **Vault ids.** The listed account would be the vault's state account, not a contract
  address.
- **The audit observation.** On Solana the audited code is a program, and an upgradeable
  program can change under the same address. The audit source would name the program id
  and the hash of the deployed program data, and "audit lapsed" would mean that hash no
  longer matches the audited build.
- **The rate source.** It would read the vault's state accounts instead of a contract's
  view functions. The observation it signs keeps the same shape.

What stays the same: the mandate, the four kernel constraints, amounts in USD minor units,
the evidence bundle, the receipts and the verifier. Building this waits until there is a
reason to.

## Layout

```
demo/sources.py        the two simulated banks, the three evidence sources, the trust list
demo/run_scenarios.py  the savings agent: plans five sweeps, decides each, writes the receipts
demo/vaults.py         the simulated vaults and the audited_vault_only check
demo/run_vault.py      the yield agent: plans twelve moves, decides each, writes the receipts
demo/verify_receipt.py the offline check, for both profiles
receipts/              written by the runs, including trust-list.json
receipts/vault/        the receipts of the vault profile
```

The demo signing keys are derived from a published string in `demo/sources.py`. They are
deterministic on purpose, so that a trust list can be rebuilt from the source file alone.
They are demo keys and secure nothing.

## Licence

Apache-2.0, see [LICENSE](LICENSE).
