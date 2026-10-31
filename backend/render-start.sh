#!/bin/sh
# The live demo's start command on Render (render.yaml): bring the database schema up to
# date, then start the API. Safe there because the demo runs one instance. Compose uses the
# image's own CMD instead (its `migrate` service runs the migrations).
set -e
alembic upgrade head
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
