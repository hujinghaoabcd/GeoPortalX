FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gdal-bin libgdal-dev libgeos-dev libproj-dev build-essential curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
RUN uv sync --extra dev --no-install-project
COPY . /app

EXPOSE 8000
CMD ["uvicorn", "geoportalx.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
