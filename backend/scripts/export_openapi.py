"""Print the API's OpenAPI document (JSON) without starting the server or a database.

`npm run gen:api` turns it into `frontend/src/api/schema.d.ts`, and `npm run check:api`
(CI and the pre-push hook) fails when that file no longer matches the API. Run from
anywhere:

    python backend/scripts/export_openapi.py > openapi.json

No .env is read: the document doesn't depend on any setting, and the placeholder values
below only let the app start.
"""

import json
import sys

from app.config import Settings
from app.main import create_app


def main() -> int:
    settings = Settings(_env_file=None, database_url="sqlite://", jwt_secret="x" * 32)
    json.dump(create_app(settings).openapi(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
