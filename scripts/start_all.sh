#!/usr/bin/env bash
# 启动全部五个服务（数据目录 ./data，日志 ./logs）
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data logs

for mod in storage_svc legacy_svc transformer compat_svc web_svc; do
  if [ -f "logs/$mod.pid" ] && kill -0 "$(cat logs/$mod.pid)" 2>/dev/null; then
    echo "$mod 已在运行 (pid $(cat logs/$mod.pid))"
    continue
  fi
  nohup python3 -m "app.$mod" >> "logs/$mod.log" 2>&1 &
  echo $! > "logs/$mod.pid"
  echo "启动 $mod (pid $!)"
done

sleep 1
echo "--- 健康检查 ---"
for port in 8002 8004 8001 8003 8000; do
  curl -sf "http://127.0.0.1:$port/health" >/dev/null 2>&1 \
    && echo "  :$port 正常" || echo "  :$port 未就绪（见 logs/）"
done
echo "页面: http://127.0.0.1:8000"
