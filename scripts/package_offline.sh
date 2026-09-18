#!/usr/bin/env bash
# 离线镜像打包（§12.3）
# 把 mfg-ai-base/backend:latest 与 mfg-ai-base/frontend:latest 与 pgvector/pgvector:pg16
# 保存为 tar，便于客户内网 docker load
set -e
cd "$(dirname "$0")/.."
mkdir -p deploy/offline
echo ">>> saving images to deploy/offline/"
docker save -o deploy/offline/mfg-backend.tar mfg-ai-base/backend:latest
docker save -o deploy/offline/mfg-frontend.tar mfg-ai-base/frontend:latest
docker pull pgvector/pgvector:pg16
docker save -o deploy/offline/pgvector.tar pgvector/pgvector:pg16
echo ">>> done. tar files in deploy/offline/"
