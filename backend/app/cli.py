"""Command-line tools, run from backend/ as `python -m app.cli <command>`.

- `seed`: create the admin user (from ADMIN_EMAIL / ADMIN_PASSWORD) and the two rubrics

Each command opens its own DB connection from DATABASE_URL. The real work lives in
other modules (`app.db.seed`, ...); this file only parses arguments and prints results.
Later phases add `collect`, `ingest` and `enrich` here.
"""

import argparse
import sys

from app.config import Settings, get_settings
from app.db.seed import seed
from app.db.session import make_engine, make_session_factory


def cmd_seed(settings: Settings, _args: argparse.Namespace) -> int:
    if not settings.admin_email or not settings.admin_password:
        print("ADMIN_EMAIL and ADMIN_PASSWORD must be set (in .env or the environment).")
        return 1

    engine = make_engine(settings.database_url)
    with make_session_factory(engine)() as session:
        result = seed(session, settings.admin_email, settings.admin_password)
    engine.dispose()

    admin_note = "created" if result.admin_created else "already exists, left unchanged"
    print(f"admin {settings.admin_email}: {admin_note}")
    print(f"rubrics created: {', '.join(result.rubrics_created) or 'none (all exist)'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="leadgate tools")
    commands = parser.add_subparsers(dest="command", required=True)

    seed_parser = commands.add_parser("seed", help="create the admin user and the two rubrics")
    seed_parser.set_defaults(handler=cmd_seed)

    return parser


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(settings or get_settings(), args)


if __name__ == "__main__":
    sys.exit(main())
