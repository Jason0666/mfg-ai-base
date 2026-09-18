#!/usr/bin/env bash
# 本地开发启动：后端 + 前端
set -e
cd "$(dirname "$0")/.."
echo ">>> start backend (uvicorn)"
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BE_PID=$!
echo ">>> start frontend (vite)"
cd ../frontend
npm install
npm run dev &
FE_PID=$!
trap "kill $BE_PID $FE_PID 2>/dev/null" EXIT
wait
