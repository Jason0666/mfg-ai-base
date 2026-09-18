# 生产构建：vite build → nginx 静态托管
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --registry https://registry.npmmirror.com
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY ../deploy/nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
