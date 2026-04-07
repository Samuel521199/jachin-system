# 项目进度每日快照

本目录由 **`mcp:atom_pmo_lark_doc`**（`operation=sync`）按 **拉取日期** 自动创建子文件夹 `YYYY-MM-DD/`，写入从飞书 Wiki 嵌入的 **K11 多维表** 导出的 Markdown（与 `sync_bi_project_context` 同源逻辑）。

默认包含三张表（可在 `atom_pmo_lark_doc` 配置中覆盖 `wiki_urls` 或关闭 `use_k11_default_tables`）：

1. **K11 需求池** — 需求描述、Sprint、优先级、责任人、需求/开发状态等  
2. **K11 项目进度** — 任务树、优先级、Sprint、执行人、起止日期、预计人天等  
3. **美术/设计任务** — 任务、Sprint、设计责任人、排期等  

每次同步会在当日目录内生成若干 `.md` 及 `00_SYNC_MANIFEST.json`，并额外生成 **`00_K11_TABLES_INDEX.md`** 说明本次种子链接与表含义。
