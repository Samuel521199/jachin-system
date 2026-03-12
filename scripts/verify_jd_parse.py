#!/usr/bin/env python3
"""验证 loader 构建的 stdin 中 jd_template 的解析逻辑（模拟 Rust extract_json_str_unescaped）"""
import json

paths = "D:/Projects/jachi/jachin-system-main/data/hr_resumes/f1|||D:/Projects/jachi/jachin-system-main/data/hr_resumes/f2|||D:/Projects/jachi/jachin-system-main/data/hr_resumes/f3"
stdin_json = {
    "capability": "execute",
    "target_dir": "data/hr_resumes",
    "target_role": "backend_engineer",
    "jd_template": "云边协同后端架构师：精通 Rust/Go，具备百万级设备接入、高可用分布式系统经验。熟悉 Kubernetes、消息队列、时序数据库。",
}
line2 = json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
stdin_str = paths + "\n" + line2
print("len(char):", len(stdin_str))
print("len(bytes):", len(stdin_str.encode("utf-8")))

# 模拟 Rust 解析
nl = stdin_str.find("\n")
first = stdin_str[:nl].strip()
rest = stdin_str[nl + 1 :].strip()
print("first line len:", len(first))
print("rest (JSON) len:", len(rest))

# 模拟 extract_json_str_unescaped
pat = '"jd_template":"'
idx = rest.find(pat)
print("pattern", repr(pat), "pos:", idx)
if idx >= 0:
    val_start = idx + len(pat)
    print("val_start:", val_start, "char at val_start:", repr(rest[val_start : val_start + 5]))
    # 找结束引号（考虑转义）
    i = val_start
    while i < len(rest):
        if rest[i] == "\\" and i + 1 < len(rest):
            i += 2
            continue
        if rest[i] == '"':
            value = rest[val_start:i]
            print("extracted value len:", len(value))
            print("extracted value preview:", repr(value[:60]))
            break
        i += 1
else:
    print("pattern NOT FOUND in rest!")
    print("rest preview:", repr(rest[:200]))
