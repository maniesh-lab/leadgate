"""Turns outside calls off on the public demo (`PUBLIC_DEMO=true`).

`app.worker.main.open_worker()` gives every HTTP client this transport on the public
demo, so a search, a website visit or a CRM write fails at once with a clear message
and never leaves the server. The demo's own leads don't notice: their findings are
already stored (`app.db.demo`).
"""

import httpx2


def refuse(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("outside calls are off on the public demo", request=request)


def blocked_transport() -> httpx2.MockTransport:
    return httpx2.MockTransport(refuse)
