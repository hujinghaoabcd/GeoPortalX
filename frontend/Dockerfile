FROM node:22-alpine
WORKDIR /app
RUN corepack enable
COPY package.json /app/package.json
RUN pnpm install --no-frozen-lockfile
COPY . /app
EXPOSE 5173
CMD ["pnpm", "dev"]
