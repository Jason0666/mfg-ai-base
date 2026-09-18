#!/usr/bin/env bash
# 一键构建生产镜像
set -e
cd "$(dirname "$0")/.."
echo ">>> build backend image"
docker build -t mfg-ai-base/backend:latest ./backend
echo ">>> build frontend image"
docker build -t mfg-ai-base/frontend:latest ./frontend
echo ">>> done"
