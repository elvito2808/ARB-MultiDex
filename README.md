# ARB-MultiDex

## Real-market DRY-RUN

From the repository root on Linux or macOS, use Python 3.10 or later:

```bash
PYTHONPATH=src python3 -B -m arb_multidex dry-run
```

This command evaluates one cycle using public Hedera Mainnet SaucerSwap V1
WHBAR/USDC reserves and KuCoin HBAR/USDT order-book data. It quotes BUY and
SELL at 250 HBAR per venue, then compares both directions against the inclusive
0.15 USDT threshold. The comparison uses the owner-approved WHBAR/HBAR and
USDC/USDT 1:1 assumptions. KuCoin pricing requires enough size at the best
level on the quoted side. Successful simulated execution settles the DEX leg
first and the KuCoin leg second. Exit status 0 includes a completed cycle with
`NO_OP`. Read `decisions` and `observations` for market rejection details.
Invalid configuration exits with status 2.

The default [configuration](config/dry-run.example.json) requires
`simulation_enabled: true` and `admission_policy: "ESTIMATED_COSTS_DRY_RUN"`.
Use `--config PATH` to select a copy. The `dry_run` object contains:

| Setting | Example | Meaning |
| --- | ---: | --- |
| `rpc_endpoints` | Hashio mainnet | Public Hedera JSON-RPC endpoints in failover order |
| `dex_max_age_ms` / `kucoin_max_age_ms` | 20000 / 5000 | Exclusive maximum observation ages |
| `max_skew_ms` | 15000 | Maximum timestamp difference between venues |
| `pool_fee_bps` | 30 | Estimated pool fee embedded in the constant-product quote |
| `kucoin_fee_bps` | 10 | Estimated separate fee in USDT |
| `buy_gas_tinybars` / `sell_gas_tinybars` | 10000000 / 10000000 | Estimated native HBAR gas by DEX side, each 0.1 HBAR |
| `gas_usdc_atoms_per_hbar` | 50000 | Estimated gas conversion, 0.05 USDC per HBAR |

Every estimate must be supplied explicitly. JSON output labels costs
`ESTIMATED` and includes each venue's observation time, liquidity, per-HBAR
prices, gross and net settlement, decision reasons, execution receipts, and
before/after balances. The command uses public reads and the simulation engine.
Setting `simulation_enabled` to `false` returns `LIVE_NOT_AUTHORIZED`.

The opening inventory is 1,000 WHBAR and 500 USDC on the DEX and 1,000 HBAR and
500 USDT on KuCoin. Set `initial_balances.dex.HBAR.units` (tinybars as a quoted
integer) before the first run to change the default 10 simulated HBAR for gas.
Use the same `--state-dir PATH` (default `.arb-multidex-dry-run`) on subsequent
runs to retain simulated balances and the execution journal. Keep
`dry-run-opportunities.sqlite3` and `dry-run-execution.sqlite3` together.
A fresh state directory starts a new simulation. If the optional
`websockets==16.0` dependency is installed, the provider tries KuCoin public
WebSocket depth first. It can also read public REST level 1.

### Strict fee-evidence follow-up

The imported strict market provider still requires independently verified venue
fee evidence. PR #19 tracks three timing items for future LIVE work. Recheck
expiry at batch return, enforce the lower validity bound at S2 evaluation, and
carry the earliest venue fee deadline into admitted leg and request expiry.
The estimated DRY-RUN uses static configured fees and a gas conversion valid
only for the DEX observation lifetime. [PR #19](https://github.com/elvito2808/ARB-MultiDex/pull/19)

## Offline fixtures

The offline CLI runs one arbitrage simulation cycle per selected worker.
Use Python 3.12 on Linux or macOS.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m arb_multidex --help
python -m arb_multidex simulate --config config/simulation.owner.example.json
```

The owner fixture uses Hedera Mainnet / SaucerSwap V1 WHBAR/USDC and KuCoin
HBAR-USDT market identifiers. Both sources are S1 mock feeds. The runtime
evaluates both directions at 250 HBAR-equivalent with the configured 1:1
simulation mappings and an inclusive minimum of 0.15 USDT. The mock pool
includes its embedded fee. The runtime adds a synthetic 10-basis-point
KuCoin fee and five tinybars of gas, converted using the S1 mock rate.
The 10 HBAR opening gas balance is separate from each execution's gas cost.

S2 checks the quotes and persists an admission before S3 can reserve
funds. S3 confirms the simulated DEX swap before any simulated KuCoin attempt.
The program needs no exchange account or credentials and makes zero real
orders or blockchain transactions.

## Scenarios and state

`--scenario` selects `profitable` (default), `no-profit`, `incomplete`,
`stale`, or `unavailable`. The incomplete fixture confirms the DEX leg and
exhausts three failed KuCoin attempts. The stale fixture advances the mock
clock to the quote expiry. The unavailable fixture retains S1's shallow KuCoin
market. Use a fresh state directory for independent fixture comparisons.

```bash
python -m arb_multidex simulate --config config/simulation.owner.example.json \
  --scenario incomplete --state-dir /tmp/arb-multidex-incomplete
```

Keep both SQLite files in the same state directory (default `.arb-multidex`).
Commands targeting that directory are serialized. Startup recovers outstanding
work before evaluating new opportunities. Balances, attempts, reservations,
and execution provenance survive process restarts. Repeating a completed
snapshot against its original journals returns a no-op. Keep unresolved
journals for reconciliation.

The CLI emits JSON results to stdout and structured JSON Lines events to stderr.
Quote sources, direction decisions, execution attempts and before/after
balances are in the results. Profit is the admitted estimate, including for an
incomplete execution. Numeric amounts in the balance and profit summaries
are exact decimal strings.

## Worker control

```bash
config=config/simulation.owner.example.json
worker=hedera-saucerswap-v1-whbar-usdc__kucoin-hbar-usdt
python -m arb_multidex start --config "$config"
python -m arb_multidex status --config "$config"
python -m arb_multidex stop --config "$config" --worker "$worker"
python -m arb_multidex restart --config "$config" --worker "$worker"
python -m arb_multidex stop --config "$config"
```

Repeat `--config` to configure up to three distinct relationship IDs. Commands
operate on all configured workers by default. Use `--worker` to select one,
and always specify a worker for `restart`. Workers share the mock KuCoin feed
and venue balances in a state directory. S2 and S3 fence overlapping work on the
same network. Lifecycle commands affect only the selected workers. `start`
checks health and enables a stopped worker. `simulate` runs one cycle for each
enabled worker. `stop` closes admission and drains accepted S3 work or leaves
it durably awaiting reconciliation. `restart` is the explicit action for
`ERROR` and `TRADING_PAUSED`, and respects unresolved executions.
Automatic restart is scoped to the affected worker, with up to three attempts
and increasing delays. A successful cycle resets the consecutive restart count.

## Verification

```bash
PYTHONPATH=src python -m pytest -q -p no:cacheprovider
python -m ruff check src tests --target-version py310 --line-length 100
git diff --check
```
