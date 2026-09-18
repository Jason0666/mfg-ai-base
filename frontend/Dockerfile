# 生产构建：vite build → nginx 静态托管
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --registry https://registry.npmmirror.com
COPY . .
# 构建期注入后端地址（§2.3）：留空=同源/反代；静态托管分离部署时
#   docker build --build-arg VITE_API_BASE=https://your-backend.example.com
ARG VITE_API_BASE=
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY ../deploy/nginx.conf /etc/nginx/nginx.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
