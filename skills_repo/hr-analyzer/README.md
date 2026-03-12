# HR 简历透视镜

根据岗位要求对候选人简历进行严苛评估，输出 Markdown 报告（含综合评分、优劣势、录用建议）。

## 打包并发布到 L1

### 前置条件

1. **编译 main.wasm**
   ```powershell
   cargo build --target wasm32-unknown-unknown --release
   Copy-Item target\wasm32-unknown-unknown\release\hr_analyzer.wasm main.wasm
   ```

2. **配置 JACHIN_DEV_TOKEN**（与 cloud/nexus/.env.local 一致）
   ```powershell
   $env:JACHIN_DEV_TOKEN = "你的token"
   ```

3. **启动 L1 Nexus**
   ```powershell
   cd cloud\nexus
   npm run dev
   ```

### 发布步骤

```powershell
# 1. 打包
jachin pack

# 2. 发布到 L1（PUBLIC 完整上传）
jachin publish --visibility PUBLIC --price 0
```

### L2 使用

1. L2 配对 L1 后，在 Admin 点击 Sync 拉取技能
2. 确保 local-hr-fs MCP 已部署（`scripts/setup_local_hr_fs.ps1`）
3. L2 需有 `data/hr_resumes/`、`config/hr_jds/` 目录及对应文件
