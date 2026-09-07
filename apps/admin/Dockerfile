# Build from the repository root: docker build -f apps/admin/Dockerfile .
FROM node:26-alpine3.24@sha256:2d984a15c9b54fd0aeb608b8e0d0d83529eb34d2966db27a1fb4f1edc3d298a3 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY apps/admin/package.json apps/admin/package.json
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY apps/admin apps/admin
RUN npm run admin:build

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
# Copy only the runtime binary, so package managers and headers never enter a layer.
RUN apk add --no-cache ca-certificates libstdc++ \
    && addgroup -S -g 1000 node \
    && adduser -S -D -H -u 1000 -G node node
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder --chown=node:node /app/apps/admin/.next/standalone ./
COPY --from=builder --chown=node:node /app/apps/admin/.next/static ./apps/admin/.next/static
USER node
EXPOSE 3000
CMD ["node", "apps/admin/server.js"]
