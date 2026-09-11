# Build from the repository root: docker build -f apps/web/Dockerfile .
FROM node:24-alpine3.24@sha256:50c8e8ca1d27439048670df5883f32d57cf81cff6233222c893fd0d9884cbd81 AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
COPY apps/admin/package.json apps/admin/package.json
COPY apps/web/package.json apps/web/package.json
COPY packages/theme/package.json packages/theme/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY packages/theme packages/theme
COPY packages/ui packages/ui
COPY apps/web apps/web
RUN npm run web:build

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS runtime
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
# Copy only the runtime binary, so package managers and headers never enter a layer.
RUN apk upgrade --no-cache \
    && apk add --no-cache ca-certificates libstdc++ \
    && addgroup -S -g 1000 node \
    && adduser -S -D -H -u 1000 -G node node
COPY --from=builder /usr/local/bin/node /usr/local/bin/node
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./
COPY --from=builder --chown=node:node /app/apps/web/.next/static ./apps/web/.next/static
COPY --from=builder --chown=node:node /app/apps/web/public ./apps/web/public
USER node
EXPOSE 3000
CMD ["node", "apps/web/server.js"]
