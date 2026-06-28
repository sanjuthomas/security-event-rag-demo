FROM python:3.12-slim

WORKDIR /app

COPY shared/cypher_gen/pyproject.toml shared/cypher_gen/pyproject.toml
COPY shared/cypher_gen/src shared/cypher_gen/src
RUN pip install --no-cache-dir ./shared/cypher_gen

COPY shared/platform_auth/pyproject.toml shared/platform_auth/pyproject.toml
COPY shared/platform_auth/src shared/platform_auth/src
RUN pip install --no-cache-dir ./shared/platform_auth

COPY shared/telemetry/pyproject.toml shared/telemetry/pyproject.toml
COPY shared/telemetry/src shared/telemetry/src
RUN pip install --no-cache-dir ./shared/telemetry

COPY ssi-indexer/pyproject.toml .
COPY ssi-indexer/src ./src
COPY ssi-indexer/search-profiles ./search-profiles

RUN pip install --no-cache-dir .

EXPOSE 8090

CMD ["ssi-indexer"]
