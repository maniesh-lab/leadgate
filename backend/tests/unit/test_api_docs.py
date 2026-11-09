"""The OpenAPI docs: every endpoint is described and shows the real error shape."""

import json

from fastapi.testclient import TestClient


def test_openapi_reads_cleanly(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    operations = [
        (path, method, operation)
        for path, methods in spec["paths"].items()
        for method, operation in methods.items()
    ]
    for path, method, operation in operations:
        # FastAPI's default 422 body isn't what the API sends (see app.api.errors).
        assert "HTTPValidationError" not in json.dumps(operation), (method, path)
        if path in ("/api/health", "/api/ready"):
            continue
        assert operation.get("description"), (method, path)
        # The reset link is the credential there: no login, so no 401.
        if path != "/api/auth/reset-password":
            assert "401" in operation["responses"], (method, path)
    assert "HTTPValidationError" not in spec["components"]["schemas"]
