# Contributing

Thanks for your interest! This project is owner-controlled: you're welcome to
download and run it, but **changes need the owner's permission** (see
[LICENSE](LICENSE)).

## How to propose a change

1. **Ask first.** Open an issue describing what you'd like to change and wait
   for the owner to say yes.
2. Once you have the go-ahead, fork the repo (or, if you were added as a
   collaborator, create a branch) and make your change.
3. Open a pull request. Nothing reaches `main` without the owner's review.

## Development setup

```bash
git clone https://github.com/nicolaasvzz/Investment_Bot.git
cd Investment_Bot
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests -q
```

The C# bot needs the [.NET SDK](https://dotnet.microsoft.com/download) (10.0):

```bash
dotnet build TradeBot/TradeBot.csproj
```

## Ground rules

- **Never commit secrets.** API keys go in a local `.env` (Python) or
  `dotnet user-secrets` (C#) — both are git-ignored. If you ever push a key by
  accident, revoke it immediately; deleting the commit is not enough.
- **Paper trading only by default.** Don't add code paths that place real-money
  orders, or that make the live endpoint easier to reach by accident.
- **Keep behaviour identical unless the change is about behaviour.** The
  backtester and the live loop share the same strategy and risk code on
  purpose. Performance work should produce bit-identical backtests — compare
  the trade list and equity curve before and after.
- **Add a test** for any bug you fix or feature you add.

## Reporting a security problem

Please don't open a public issue for anything involving credentials or
accounts — contact the owner privately through GitHub instead.
