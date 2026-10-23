# Build from the repository root: docker build -f apps/web/Dockerfile .
ARG DOCKERHUB_PREFIX=
FROM ${DOCKERHUB_PREFIX}node:26.12.1-alpine3.24@sha256:40cd011b55415bc894e45b02fdd5a5dfde056921b7d23a64c74f80497af48797 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY apps/admin/package.json apps/admin/package.json
COPY apps/partner/package.json apps/partner/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/http/package.json packages/http/package.json
COPY packages/theme/package.json packages/theme/package.json
COPY packages/ui/package.json packages/ui/package.json
COPY packages/telemetry/package.json packages/telemetry/package.json
COPY packages/braces packages/braces
RUN --mount=type=cache,target=/root/.npm npm ci --ignore-scripts --no-audit --no-fund
COPY packages/http packages/http
COPY packages/theme packages/theme
COPY packages/ui packages/ui
COPY packages/telemetry packages/telemetry
COPY apps/web apps/web
# Reader type checks include the shared extension analytics regression test.
COPY apps/extensions apps/extensions
COPY scripts/assets scripts/assets
COPY scripts/ci scripts/ci
RUN --mount=type=cache,target=/app/apps/web/.next/cache,sharing=locked \
    mkdir -p apps/web/public && npm run web:build && node packages/telemetry/scripts/private-sourcemaps.mjs apps/web/.next

FROM ${DOCKERHUB_PREFIX}alpine:3.24@sha256:71e9b7bae160ba167fa17414401b94cbcdaf3c32feeab9298bd794a2a0614c35 AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
# Copy only the runtime binary, so package managers and headers never enter a layer.
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates libatomic libstdc++ \
    && addgroup -S -g 1000 node \
    && adduser -S -D -H -u 1000 -G node node
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
RUN node --version && node -e 'require("node:crypto"); require("node:tls")'
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=node:node /app/apps/web/public ./apps/web/public
COPY --from=builder --chown=node:node /app/apps/web/.next/faro-sourcemaps ./apps/web/.next/faro-sourcemaps
USER node
EXPOSE 3000 9100
CMD ["node", "apps/web/server.js"]
