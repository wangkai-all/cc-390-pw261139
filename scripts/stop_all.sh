#!/usr/bin/env bash
# 停止全部服务
set -euo pipefail
cd "$(dirname "$0")/.."
for mod in storage_svc legacy_svc transformer compat_svc web_svc; do
  if [ -f "logs/$mod.pid" ]; then
    pid=$(cat "logs/$mod.pid")
    kill "$pid" 2>/dev/null && echo "停止 $mod (pid $pid)" || echo "$mod 已不在运行"
    rm -f "logs/$mod.pid"
  fi
done
