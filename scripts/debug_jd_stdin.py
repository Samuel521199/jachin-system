#!/usr/bin/env python3
"""调试 JD 传入：验证 loader 构建的 stdin 中 jd_template 是否可被 Wasm 正确解析"""
import json
import sys

# 模拟 loader 构建的 stdin
stdin = {
    "capability": "execute",
    "target_dir": "data/hr_resumes",
    "target_role": "backend_engineer",
    "jd_template": "云边协同后端架构师：精通 Rust/Go，具备百万级设备接入、高可用分布式系统经验。熟悉 Kubernetes、消息队列、时序数据库。",
}
s = json.dumps(stdin, ensure_ascii=False, separators=(",", ":"))
print("JSON len:", len(s))
print("jd_template in JSON:", '"jd_template"' in s)

# 模拟 Wasm extract_json_str_unescaped 逻辑
def extract_jd(s: str) -> str:
    pat = '"jd_template":"'
    idx = s.find(pat)
    if idx < 0:
        return ""
    val_start = idx + len(pat)
    i = val_start
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            i += 2
            continue
        if s[i] == '"':
            return s[val_start:i]
        i += 1
    return ""

extracted = extract_jd(s)
print("Extracted jd:", repr(extracted[:80]) + ("..." if len(extracted) > 80 else ""))
print("OK" if extracted else "FAIL")
