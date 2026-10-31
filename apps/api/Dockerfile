FROM node:22-slim AS build
WORKDIR /repo
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/api/package.json apps/api/
RUN pnpm install --frozen-lockfile --filter @trip-genie/api...
COPY apps/api apps/api
RUN pnpm --filter @trip-genie/api build

FROM node:22-slim
WORKDIR /repo/apps/api
ENV NODE_ENV=production
COPY --from=build /repo /repo
EXPOSE 8787
CMD ["node", "dist/main.js"]
