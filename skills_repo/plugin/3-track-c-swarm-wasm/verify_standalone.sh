#!/bin/sh
# 轨道 C 独立验证：无需 Jachin 主项目，验证 stdin/stdout JSON 协议
# 规范要求：echo '{"key":"val"}' | python src/main.py 输出正确 JSON
cd "$(dirname "$0")"
python3 verify_standalone.py
exit $?
