# ARB-MultiDex

This Python CLI runs one offline arbitrage simulation cycle per selected worker.
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
