"""Reference tool: web_fetch. CONTRACT v0.7 #16.

A real `web_fetch` implementation using only Python stdlib. No
third-party dependency. Marked `deterministic=False`.

In production, prefer this for simple cases; for anything serious
(rate limiting, retry policies, conditional GETs) write your own
@tool that wraps httpx or aiohttp.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from activegraph.tools.context import ToolContext
from activegraph.tools.decorators import tool
from activegraph.tools.errors import ToolError


class WebFetchInput(BaseModel):
    url: str = Field(description="The HTTP/HTTPS URL to fetch.")
    timeout_seconds: float = Field(default=10.0, gt=0)


class WebFetchOutput(BaseModel):
    text: str
    status: int
    final_url: str


@tool(
    name="web_fetch",
    description="Fetch the body text of a URL via HTTP GET. Follows redirects.",
    input_schema=WebFetchInput,
    output_schema=WebFetchOutput,
    cost_per_call=Decimal("0.001"),
    timeout_seconds=10.0,
    deterministic=False,
)
def web_fetch(args: WebFetchInput, ctx: ToolContext) -> WebFetchOutput:
    if ctx.external_io_mode not in {"runtime_recorded", "live_unrecorded"}:
        raise ToolError(
            "tool.unrecorded_external_io",
            "direct web_fetch is unrecorded; use runtime tool dispatch or "
            "set external_io_mode='live_unrecorded' explicitly",
            payload_extras={"url": args.url},
        )
    import urllib.error
    import urllib.request

    req = urllib.request.Request(args.url, headers={"User-Agent": "activegraph/0.7"})
    try:
        with urllib.request.urlopen(req, timeout=args.timeout_seconds) as resp:
            status = resp.getcode() or 0
            final_url = resp.geturl() or args.url
            body = resp.read()
    except urllib.error.HTTPError as e:
        body = e.read() if hasattr(e, "read") else b""
        return WebFetchOutput(
            text=_decode(body), status=e.code, final_url=args.url
        )
    except urllib.error.URLError as e:
        raise ToolError(
            "tool.network_error",
            f"network error fetching {args.url}: {e.reason}",
            payload_extras={"url": args.url},
        ) from e
    except TimeoutError as e:
        raise ToolError(
            "tool.timeout",
            f"timeout after {args.timeout_seconds}s fetching {args.url}",
            payload_extras={"url": args.url, "timeout_seconds": args.timeout_seconds},
        ) from e
    return WebFetchOutput(text=_decode(body), status=status, final_url=final_url)


def _decode(body: bytes) -> str:
    if not body:
        return ""
    for enc in ("utf-8", "latin-1"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")
