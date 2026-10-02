# Contributing to the SDK

Start with the [developer guide](docs/developer-guide.md) if you are using the
library. This page explains how to make and verify repository changes.

## Local setup and checks

Use Python 3.11+ and run from the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest
ruff check .
ruff format --check .
mypy
```

On Windows, activate with `.venv\Scripts\Activate.ps1`.
Use `ruff format .` when formatting changed Python files.
`mypy` uses the strict source-package configuration in `pyproject.toml`.
CI declares Python 3.11–3.13 checks. Local verification has used Python 3.12;
consult the remote CI results for the other runtimes.

The default tests use HTTP fixtures/local WebSocket servers. They require no
keys or funded accounts; network integration tests are skipped by default.
For a focused change, run the relevant file, for example:

```bash
python -m pytest tests/unit/test_websocket.py -q
```

## Repository map

| Location | Responsibility |
| --- | --- |
| `src/risex/__init__.py` | Supported public exports and package version |
| `src/risex/client.py` | User-facing asynchronous facade |
| `src/risex/config.py` | Explicit environment/settings validation |
| `src/risex/rest.py` | HTTP pacing, bounded reads and single-attempt mutations |
| `src/risex/websocket.py` | Subscription/auth lifecycle, parsing and reconnect |
| `src/risex/models.py` | Native request, response and event models |
| `src/risex/signing.py`, `auth.py`, `nonce.py` | EIP-712, wire signatures and local nonce coordination |
| `src/risex/units.py`, `orderbook.py` | Exact conversions and full-book CRC32 state |
| `src/risex/exceptions.py` | Errors applications can handle |
| `tests/unit/`, `tests/fixtures/` | Offline behavior checks and contract-shaped data |
| `tests/integration/` | Explicit public/wallet/funded test gates |
| `tests/consumer_smoke.py` | Built-wheel consumer verification outside the checkout |
| `examples/` | Standalone public API examples |
| `docs/` | Usage, reference, provider contracts and integration evidence |
| `.github/workflows/ci.yml` | Lint/types/tests/build and isolated install checks |

## Making a change

Confirm the native provider contract before changing encodings or semantics.
Record discrepancies and sources in [API contracts](docs/api-contracts.md).
Preserve exact Decimal/integer conversion, native statuses and identifiers.
Do not introduce automatic rounding, write retries, invented replay guarantees
or application strategy imports into the SDK.

Update public exports, usage/reference documentation and examples when their API
changes. Keep examples dependent only on installed public `risex` objects.
Add relevant offline tests for meaningful protocol/lifecycle changes.
Keep test private keys explicitly marked as public deterministic test vectors;
never use them for live funded tests.

Choose fixtures that exercise the behavior being changed: partial fills,
scope/precision checks, unusable acknowledgements, transmission uncertainty,
fresh private auth and stale state are particularly important.
Compare signatures/action hashes against independently constructed encodings;
a test that only reproduces the implementation is weak evidence.

## Building and checking the standalone boundary

```bash
python -m build
```

This creates wheel/sdist artifacts in `dist/`. Install the specific new wheel in
a separate virtual environment and execute from outside this checkout.
The CI workflow provides the exact consumer smoke setup: copy
`tests/consumer_smoke.py` and `tests/fixtures/` into
an unrelated directory, then run the copied script with that environment's
`python -I`. It checks the SDK comes from site-packages and exercises public APIs
with mocked HTTP/local streams while rejecting external socket connections.

The installable wheel must contain the `risex` package and typing marker, not
application adapters, strategy modules or fixtures. Keep the distribution independently
versioned; update `pyproject.toml`, `risex.__version__` and the HTTP user agent
together when changing the version.

## Integration checks

Public checks are read-only and explicitly enabled:

```bash
RISEX_RUN_INTEGRATION=1 \
python -m pytest tests/integration/test_public_testnet.py -v
```

That suite reads testnet markets/streams and checks signing metadata on both
networks. Wallet/private and funded order checks have separate explicit flags
documented in [testnet integration](docs/testnet-integration.md).
Do not infer live acceptance from an offline signature test.
Record validation environment/results and unresolved limits without keys or
signed authentication frames.

Select a license and publication destination before public distribution.
Local builds do not publish the package or execute funded integration gates.
