"""Confirm each configured provider's model ID exists, with one minimal call.

Runs before any live eval run. A wrong model ID fails here instead of
mid-run, and no substitute model is ever used: a failure stops the run
and is reported. Providers without keys in the environment are reported
as blocked, not as failures.

Prints provider names, model IDs, and a status per provider. It never
prints keys or settings values.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# The SDK clients break on the sandbox proxy variables' bracketed IPv6
# entries; localhost must bypass the proxy.
os.environ["no_proxy"] = "localhost,127.0.0.1"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from google.genai import types as genai_types  # noqa: E402

from claims_copilot.config import Settings  # noqa: E402
from claims_copilot.providers import get_provider  # noqa: E402

PROVIDERS = ["azure_openai", "gemini", "anthropic", "openai"]
PROMPT = "Reply with OK."


async def _check(provider_name: str) -> tuple[str, str]:
    try:
        provider = get_provider(Settings(llm_provider=provider_name))  # type: ignore[arg-type]
    except ValueError as error:
        return provider_name, f"BLOCKED: {error}"
    # The base class does not declare the client; every real adapter
    # carries one, and this script only runs against real adapters.
    client = provider.client  # type: ignore[attr-defined]
    try:
        if provider_name in ("azure_openai", "openai"):
            await client.chat.completions.create(
                model=provider.model,
                messages=[{"role": "user", "content": PROMPT}],
                max_tokens=5,
            )
        elif provider_name == "gemini":
            await asyncio.to_thread(
                client.models.generate_content,
                model=provider.model,
                contents=PROMPT,
                config=genai_types.GenerateContentConfig(max_output_tokens=5),
            )
        elif provider_name == "anthropic":
            await client.messages.create(
                model=provider.model,
                max_tokens=5,
                messages=[{"role": "user", "content": PROMPT}],
            )
    except Exception as error:  # noqa: BLE001 - the point is the failure, any kind
        return provider_name, f"FAILED: {type(error).__name__}: {error}"
    return provider_name, f"OK (model {provider.model})"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--providers",
        default=",".join(PROVIDERS),
        help="comma-separated subset to check; unlisted providers are skipped, not failed",
    )
    args = parser.parse_args()
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    unknown = [name for name in names if name not in PROVIDERS]
    if unknown:
        print(f"unknown providers: {', '.join(unknown)}")
        return 2
    results = [await _check(name) for name in names]
    failed = False
    for name, status in results:
        print(f"{name}: {status}")
        if not status.startswith("OK"):
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
