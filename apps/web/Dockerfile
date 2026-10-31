FROM node:22-slim AS build
WORKDIR /repo
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile --filter @trip-genie/web...
COPY apps/web apps/web
RUN pnpm --filter @trip-genie/web build

FROM node:22-slim
WORKDIR /repo/apps/web
ENV NODE_ENV=production
COPY --from=build /repo /repo
EXPOSE 3000
CMD ["node_modules/.bin/next", "start", "-p", "3000"]
