import azure.functions as func

from claims_copilot.db import init_db
from claims_copilot.main import create_app

# The Functions ASGI bridge forwards HTTP requests only; it never runs
# the app's lifespan, so tables are created explicitly here instead of
# on startup. The backend package must be installed in the function
# app environment (see README.md in this directory).
fastapi_app = create_app(init_db_on_startup=False)
init_db()
asgi = func.AsgiMiddleware(fastapi_app)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.route(route="{*route}", methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
async def http_handler(req: func.HttpRequest) -> func.HttpResponse:
    return await asgi.handle_async(req)
