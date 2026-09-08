# Dev-mode only: runs the Vite dev server with hot reload against the bind-mounted
# source (see infra/docker/docker-compose.yml's frontend service). Not a production
# build - there's no separate production image/nginx stage in this repo yet.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]
