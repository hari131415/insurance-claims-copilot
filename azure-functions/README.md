# Azure Functions deployment (config only, deployment unverified)

`function_app.py` serves the FastAPI app through the Functions ASGI
bridge behind one catch-all HTTP route. It is config, not a tested
deployment: no Azure account or Functions host was available when
it was written, so nothing here has run against real Azure.

## Deploy

1. Build a wheel from `backend/` (`pip wheel .`) and copy it into
   this directory; uncomment its line in `requirements.txt`.
2. Copy `local.settings.json.example` to `local.settings.json`
   (gitignored) for local runs, or set the same values as Function
   App settings for a deployed app. Never commit real values.
3. `func azure functionapp publish <app-name>` from this directory.

## Settings that matter

- `STORAGE_BACKEND` must be `azure_blob` on Functions: there is no
  durable local disk. `AZURE_STORAGE_CONNECTION_STRING` points at
  the Blob account (a container named `claim-uploads` by default).
- `DATABASE_URL` must be Postgres on Functions; SQLite has no
  durable home there either.
- `LLM_PROVIDER` plus the provider's key and model, as app
  settings; prefer Key Vault references for the keys.
- CORS: the app adds its CORS middleware only when `ENVIRONMENT`
  is `development`, so a production Function App needs its CORS
  origins configured in the Function App's own CORS settings.
- Background extraction runs in-process in this app. A Functions
  Consumption plan can cut that off; a production deployment
  would move extraction to a queue or Durable Functions.
