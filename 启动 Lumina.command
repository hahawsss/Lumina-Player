#!/usr/bin/env bash
# 双击这个文件就能启动 Lumina —— 等价于在终端里跑 ./start.sh。
# 标记 LUMINA_ONECLICK 让 start.sh 在启动成功后把这个终端窗口收掉；
# 失败时它会留着窗口，错误信息就在里面。
cd "$(dirname "$0")"
LUMINA_ONECLICK=1 exec ./start.sh "$@"
