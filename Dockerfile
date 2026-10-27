FROM python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build
WORKDIR /app
RUN pip install --no-cache-dir uv==0.13.1
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never TIKTOKEN_CACHE_DIR=/app/tokenizer-cache
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
RUN .venv/bin/python -c 'import tiktoken; tiktoken.get_encoding("o200k_base")'

FROM python:3.14.8-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
RUN rm -rf /usr/local/lib/python3.14/ensurepip \
    /usr/local/lib/python3.14/site-packages/pip \
    /usr/local/lib/python3.14/site-packages/pip-*.dist-info /usr/local/bin/pip*
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3=3.0.22-1~deb12u2 openssl=3.0.22-1~deb12u2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app/tokenizer-cache /app/tokenizer-cache
ENV TIKTOKEN_CACHE_DIR=/app/tokenizer-cache
COPY migrations ./migrations
COPY alembic.ini ./
USER 10001:10001
ENTRYPOINT ["atp"]
CMD ["api"]

FROM build AS test
RUN uv sync --frozen --no-editable
COPY . .
ENV PATH="/app/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT []
CMD ["pytest", "-p", "no:cacheprovider"]
