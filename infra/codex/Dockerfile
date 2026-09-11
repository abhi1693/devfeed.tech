# Build from the repository root; only the Node binary and Codex packages ship.
FROM node:24-alpine3.24@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS builder
ARG CODEX_VERSION=0.154.0
RUN npm install --global --no-audit --no-fund @openai/codex@${CODEX_VERSION}

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates libstdc++ bubblewrap \
    && addgroup -S -g 10001 codex \
    && adduser -S -D -H -u 10001 -G codex codex \
    && mkdir -p /var/lib/codex /run/codex /workspace /usr/local/bin \
    && chown -R codex:codex /var/lib/codex /run/codex /workspace \
    && chmod 700 /run/codex \
    && ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
ENV CODEX_HOME=/var/lib/codex
COPY infra/codex/config.toml infra/codex/entrypoint.sh infra/codex/server.cjs infra/codex/health.cjs infra/codex/transport.cjs /opt/devfeed/
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["sh", "/opt/devfeed/entrypoint.sh"]
CMD ["app-server", "--listen", "ws://127.0.0.1:4500"]
