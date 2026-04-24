ARG PYTHON_VERSION=3.13-alpine

FROM python:${PYTHON_VERSION} AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apk add --no-cache \
    build-base \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev

WORKDIR /app

COPY pyproject.toml uv.lock VERSION ./
COPY bridge ./bridge

RUN uv venv && \
    uv pip install -r <(uv export --no-dev --no-emit-project)

RUN uv build && \
    uv pip install dist/*.whl

FROM python:${PYTHON_VERSION}
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH"

ARG COMMIT_SHA=dev
ARG DEPLOY_TIME=unknown
ENV COMMIT_SHA=$COMMIT_SHA
ENV DEPLOY_TIME=$DEPLOY_TIME

EXPOSE 8080
ENV PORT=8080

CMD ["sh", "-c", "uvicorn bridge.app:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 120 --loop uvloop"]
