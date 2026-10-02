# Security and credentials

Keep wallet private keys, API signer keys, recovery phrases and signed
authentication frames out of commits, issue reports, chat and logs.
`.env` and `.env.*` files are ignored except for the empty `.env.example`
template. The SDK does not load environment files automatically.

Ordinary trading and private streams require the account's public address and
an authorized delegated signer. They do not require the account wallet's private
key. Account-key access is needed only for explicit signer administration through
the corresponding SDK methods; authorization through the exchange UI avoids
configuring that key in the SDK.

An API signer can exercise its authorized trading permissions. Protect it even
when it cannot control every wallet operation. If it is exposed, revoke its
authorization through a trusted account-wallet flow and replace it. Removing a
key from the latest commit does not remove it from Git history.

Tests use explicitly identified deterministic public keys and synthetic
fixtures. Never fund those keys or reuse them for a live account.

For a potential vulnerability, use the repository's private security advisory
channel if its maintainers have enabled it. Otherwise request a private reporting
contact without publishing exploit details or credentials. Do not attach
environment files or raw signed requests to a public issue.

This is an alpha package. Consult the README and `docs/testnet-integration.md`
for tested behavior and remaining live integration checks.
