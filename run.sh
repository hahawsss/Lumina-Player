#!/usr/bin/env bash
# 开发启动：用项目自己的 uv 环境跑，参数透传（可接文件/目录）
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --quiet python -m lumina "$@"
