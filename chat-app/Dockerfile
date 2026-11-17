# syntax=docker/dockerfile:1.7
# Node 22 LTS (Node 20 is end-of-life). Pin by digest with scripts/security/pin_images.sh.
ARG NODE_IMAGE=node:22-alpine

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1 NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN apk add --no-cache tini \
    && addgroup -g 10001 -S nodejs && adduser -S nextjs -u 10001 -G nodejs

COPY --from=builder /app/public ./public
# Requires `output: "standalone"` in next.config.ts (next.config.ts enables it when NEXT_OUTPUT=standalone, set above in the builder stage). When it is
# absent .next/standalone does not exist and this stage falls back to `next start`.
COPY --from=builder --chown=10001:10001 /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json

USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD wget -qO- http://127.0.0.1:3000/api/rag/health > /dev/null 2>&1 || exit 1

ENTRYPOINT ["/sbin/tini", "--"]
# Standalone server if present, otherwise the regular production server.
CMD ["sh", "-c", "if [ -f .next/standalone/server.js ]; then exec node .next/standalone/server.js; else exec node_modules/.bin/next start -H 0.0.0.0 -p 3000; fi"]
