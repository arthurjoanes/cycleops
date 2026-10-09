# Caddy with the web build (base /). Paths in the node stage mirror the repository, because
# vite.config.ts reaches packages/contracts through ../../packages/contracts.
FROM node:26.10.0-trixie-slim@sha256:930557a230abacbc3f4fd9b8648abf8f4bee1e17cb72195dcdfb2f709bc85b33 AS web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
WORKDIR /src/apps/web
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN corepack pnpm install --frozen-lockfile
COPY packages/contracts/ /src/packages/contracts/
COPY apps/web/ ./
RUN corepack pnpm build

FROM caddy:2.11.7-alpine@sha256:d8542f48d34a9cf4e4c11a478865229840e87e4c96ea3f439101f31a5d35f75f
# Caddy keeps its state in /data and /config (XDG dirs of the base image).
RUN addgroup -S -g 10001 app \
    && adduser -S -D -H -u 10001 -G app app \
    && chown -R app:app /data /config
COPY infra/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=web /src/apps/web/dist /srv
USER app
EXPOSE 8080
