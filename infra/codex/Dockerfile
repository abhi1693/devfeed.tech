# Build from the repository root; only the Node binary and Codex packages ship.
ARG DOCKERHUB_PREFIX=
FROM ${DOCKERHUB_PREFIX}node:26.12.1-alpine3.24@sha256:40cd011b55415bc894e45b02fdd5a5dfde056921b7d23a64c74f80497af48797 AS builder
WORKDIR /opt/codex
COPY infra/codex/package.json infra/codex/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts --no-audit --no-fund

FROM ${DOCKERHUB_PREFIX}alpine:3.24@sha256:71e9b7bae160ba167fa17414401b94cbcdaf3c32feeab9298bd794a2a0614c35
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates libatomic libstdc++ bubblewrap \
    && addgroup -S -g 10001 codex \
    && adduser -S -D -H -u 10001 -G codex codex \
    && mkdir -p /var/lib/codex /run/codex /workspace /usr/local/bin \
    && chown -R codex:codex /var/lib/codex /run/codex /workspace \
    && chmod 700 /run/codex \
    && ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
RUN node --version && node -e 'require("node:crypto"); require("node:tls")'
COPY --from=builder /opt/codex/node_modules/@openai /usr/local/lib/node_modules/@openai
ENV CODEX_HOME=/var/lib/codex
COPY infra/codex/config.toml infra/codex/entrypoint.sh infra/codex/server.cjs infra/codex/health.cjs infra/codex/health-server.cjs infra/codex/transport.cjs /opt/devfeed/
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["sh", "/opt/devfeed/entrypoint.sh"]
CMD ["app-server", "--listen", "ws://127.0.0.1:4500"]
