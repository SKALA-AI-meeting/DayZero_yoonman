FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1
WORKDIR /app/services/dayzero
COPY services/dayzero/pyproject.toml services/dayzero/uv.lock ./
RUN pip install --no-cache-dir uv==0.13.3 && uv sync --frozen --no-dev --extra rag --no-install-project
COPY services/dayzero ./
COPY scenarios /app/scenarios
RUN uv sync --frozen --no-dev --extra rag
ENV PATH="/app/services/dayzero/.venv/bin:$PATH" SCENARIO_ROOT=/app/scenarios
# API runtime by default; CLI is installed and authenticated separately and remains
# unavailable until native-tool and filesystem isolation has been verified.
CMD ["uvicorn", "dayzero.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
