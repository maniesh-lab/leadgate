# leadgate frontend image: build the app with Node, then serve the static files with nginx
# (which also passes /api to the API; see nginx.conf). Only the nginx stage ships.
FROM node:26.12.1-alpine AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.30.6-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/security-headers.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
