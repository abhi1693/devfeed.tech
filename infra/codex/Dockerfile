FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5
ARG CODEX_VERSION=0.153.4
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates bubblewrap \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global --no-audit --no-fund @openai/codex@${CODEX_VERSION} \
    && npm cache clean --force \
    && groupadd --gid 10001 codex \
    && useradd --uid 10001 --gid 10001 --no-create-home codex \
    && mkdir -p /var/lib/codex /run/codex /workspace \
    && chown -R codex:codex /var/lib/codex /run/codex /workspace \
    && chmod 700 /run/codex
ENV CODEX_HOME=/var/lib/codex
COPY config.toml entrypoint.sh server.cjs health.cjs /opt/devfeed/
WORKDIR /workspace
USER 10001:10001
ENTRYPOINT ["sh", "/opt/devfeed/entrypoint.sh"]
CMD ["app-server", "--listen", "ws://127.0.0.1:4500"]
