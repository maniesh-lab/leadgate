# leadgate backend image: the api, the worker (`python -m app.worker`) and migrate use it.
FROM python:3.14.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
RUN pip install .
# Migrations: the compose `migrate` service runs `alembic upgrade head` with this image.
COPY alembic.ini ./
COPY alembic ./alembic
# The public demo's leads (`python -m app.cli demo`; the worker reads the CSV from here).
COPY demo ./demo

RUN useradd --create-home --uid 1000 leadgate
USER leadgate

EXPOSE 8000

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
