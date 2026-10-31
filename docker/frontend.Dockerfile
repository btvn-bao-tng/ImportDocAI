# syntax=docker/dockerfile:1.7
#
# ImportDoc AI — React frontend built with Vite and served by nginx,
# which also reverse-proxies /api to the backend service.
# Build context is the repository root:
#   docker build -f docker/frontend.Dockerfile -t importdoc-ai-frontend .

# Vite 8 requires node ^20.19 || >=22.12.
FROM node:22-alpine AS build

WORKDIR /app

COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN npm ci

COPY src/frontend/ ./
# E2E specs and their Playwright config are dev-only; stale build output must never be copied.
RUN rm -rf e2e playwright.config.ts playwright-report test-results dist \
 && npm run build


FROM nginx:1.27-alpine AS runtime

RUN apk add --no-cache curl \
 && rm -f /etc/nginx/conf.d/default.conf

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
