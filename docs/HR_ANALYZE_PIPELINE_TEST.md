# HR 透析镜分析链路测试说明

**目的**：验证从 **`{职位}/pending`** 读简历、从 **`{职位}/jd.json`** 读 JD 并传入 `hr_analyze_resume` / Wasm 后，分析报告落在 **`{职位}/result/`**，且 **同一候选人重复分析会覆盖** 同名的 `*_analysis.md`（与 `persist_hr_analysis_batch_item` 的固定命名一致）。

**典型问题**：报告里出现与当前岗位无关的人设（例如误成「云边架构师」）时，优先排查：

1. **`jd.json` 里 `jd_full` 为空或过短** — Wasm 侧可能回退到内置/缓存 JD。  
2. **`target_role` 长期为默认 `backend_engineer`** — 与「Python 工程师」等岗位不一致时，可在 `jd.json` 增加 `analyzer_target_role`（见下）或通过测试脚本的启发规则传入。  
3. **分析的是缓存目录** — 确认 `pending`、`result` 与 `jd.json` 在同一职位根目录下。  
4. **`result/*_analysis.md` 内容长期不变、日志里 `ndjson_lines=0`** — 多半是 **`~/.jachin/l3_skill_cache/hr-analyzer4/main.wasm` 过旧**（体积明显小于仓库 `l3_node/skills/wasm_plugins/hr-analyzer4/main.wasm`）。旧版不按 NDJSON 批量落盘，新一次分析不会覆盖 `{stem}_analysis.md`。处理：删掉缓存目录里的 `hr-analyzer4` 后由 L2 重拉，或**用本仓库最新代码**（开发模式下已优先使用仓库内 Wasm）。  

---

## 目录布局（与线上一致）

```
~/.jachin/workspace/hr_recruitment/{职位文件夹}/
  jd.json          # 必须含 jd_full（或至少 job_title + 可拼出 JD 的字段）
  pending/         # 待分析简历（.pdf / .md / …）
  result/          # 输出 {简历主文件名}_analysis.md，重复运行覆盖同名文件
```

---

## 运行自动化测试

在项目根目录（本仓库）执行：

```powershell
# 使用默认：本机 workspace 下「Python 工程师」职位（可改参数）
python scripts/test_hr_analyze_jd_pipeline.py

# 显式指定职位根目录（与飞书/收网使用的目录一致）
python scripts/test_hr_analyze_jd_pipeline.py --job-root "C:\Users\Legion\.jachin\workspace\hr_recruitment\Python 工程师"

# 只校验路径与 JD 加载，不调用大模型
python scripts/test_hr_analyze_jd_pipeline.py --job-root "..." --dry-run

# 跑两轮分析，断言第二次覆盖 result 下同名 md（修改时间变化）
python scripts/test_hr_analyze_jd_pipeline.py --job-root "..." --runs 2

# 若曾误出「云边架构师」等无关岗位用语，可强制判失败
python scripts/test_hr_analyze_jd_pipeline.py --job-root "..." --forbid-substr "云边架构师"
```

**前置**：

- 已配置 `DASHSCOPE_API_KEY` 或 `OPENAI_API_KEY`（与现有 `test_analyze_pdf_leaderboard.py` 相同）。  
- 建议开发联调时：`JACHIN_DEV_HR_FIRST=1`，`JACHIN_APP_ROOT` 指向本仓库根，确保用仓库内最新 HR 插件与透析镜 Wasm。

---

## jd.json 建议字段

| 字段 | 说明 |
|------|------|
| `job_title` | 岗位名称，用于断言报告与岗位一致 |
| `jd_full` | **必填（推荐）**：完整 JD 正文传入 Wasm，避免误用默认模板 |
| `analyzer_target_role` | **可选**：传给透析镜的 `target_role`（如 `python_engineer`）；不写则脚本按标题启发 |

---

## 与调度器路径的关系

无人值守使用的 `jd_config_path` 若指向**另一份** `jd.json`，会出现「磁盘上是 Python 岗、分析却像别的岗」的错觉。本测试以 **职位根目录下的 `jd.json`** 为唯一 JD 源，与 `pending`/`result` 同级，便于对齐排查。

---

## 相关代码

- `skills_repo/plugin/com.jachin.hr.recruitment/tools/hr_analyze_resume.py` — MCP 入口，`jd_template` 必填。  
- `skills_repo/plugin/com.jachin.hr.recruitment/hr_analysis_persist.py` — `{stem}_analysis.md` 批量落盘（覆盖写）。  
- `l3_node/skills/loader.py` — 透析镜持久化前合并调用方 `output_dir`，保证写入 `{职位}/result/` 而非仅默认 `hr_analysis`。  
- `scripts/test_analyze_pdf_leaderboard.py` — 另一套基于 `plugin/data` 的 PDF + 排行榜测试。
