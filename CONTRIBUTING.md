# Contributing

## Local setup

Follow the root `README.md`. Keep personal credentials only in `.env`; use `.env.example` to document configuration names without values.

## Before opening a pull request

Run the offline checks:

```powershell
python -m pytest
cd frontend
npm run lint
npm run build
```

Do not commit `.env`, API keys, `test_results.json`, `live_evaluation_results.json`, browser runtime files, uploaded images, `node_modules`, or frontend build output.

Real Gonka evaluations are optional because they consume account quota. When reporting one, share only the redacted summary and never paste request headers or credentials into an issue or pull request.
