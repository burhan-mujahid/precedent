# Contributing

Thanks for helping. Precedent stays small on purpose: one runtime dependency (PyYAML), plain files, and a deterministic offline mode so everything is testable without an API key.

## Setup

```bash
pip install -e ".[dev]"
pytest -q
bash examples/demo.sh
```

## Ground rules

* **One door into the rulebook.** New features that change rules must go through a `Proposal` and `loop.approve`.
* **Every new reasoning step has an offline implementation** and a test that uses it.
* **Model output is untrusted.** Validate every id a model returns; treat chat text as data in prompts.
* **No new runtime dependencies** without an issue first. Optional extras are fine.

## Good first issues

* Parsers: Microsoft Teams, Discord, Telegram, email (mbox), helpdesk CSV exports.
* Domain packs: real-estate, clinics, agencies, e-commerce.
* An embedding retriever behind `retrieve.Retriever` as an optional extra.
* A static review UI over the rulebook directory.
