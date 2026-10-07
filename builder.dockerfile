# Alpine Build Stage
FROM python:3.14-alpine AS build-alpine

RUN pip install pex

COPY . .

RUN pex . -o /tmp/http-process-wrapper-musl -e app:serve --scie eager --scie-only --scie-platform musl-linux-x86_64

# Debian Slim Build Stage
FROM python:3.14-slim AS build-debian

RUN pip install pex

COPY . .

RUN pex . -o /tmp/http-process-wrapper-glibc -e app:serve --scie eager --scie-only --scie-platform linux-x86_64

# Artifact Exporter Stage
FROM scratch AS exporter

COPY --from=build-alpine /tmp/http-process-wrapper-musl /
COPY --from=build-debian /tmp/http-process-wrapper-glibc /
