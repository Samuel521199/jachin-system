# Jachi Test

全链路测试 Skill，基于 jachin-system-pilot。用于验证 L1→L2→L3 分发与 Wasm 沙箱执行。

## 打包并上传到 L1

### 前置条件

1. **安装 jachin-cli**
   ```powershell
   cd tools\jachin-cli
   pip install -e .
   ```

2. **配置 JACHIN_DEV_TOKEN**
   - 在 `cloud/nexus/.env.local` 中设置 `JACHIN_DEV_TOKEN` 和 `JACHIN_DEV_ID`（L1 校验用）
   - 发布时需在环境变量或 `~/.jachin-cli/config.json` 中配置相同 token：
     ```json
     { "token": "你的token", "nexus_url": "http://localhost:3000" }
     ```

3. **启动 L1 Nexus**
   ```powershell
   cd cloud\nexus
   npm run dev
   ```

### 步骤

```powershell
# 1. 进入技能目录
cd skills_repo\jachi-test

# 2. 打包（生成 dist/com.jachin.test_v1.0.0.zip）
jachin pack

# 3. 发布到 L1
jachin publish
```

发布时选择：
- **可见性**：PRIVATE（影子上传，仅元数据）或 PUBLIC（完整 zip 上传）
- **月付价格**：0（免费）

PRIVATE 时实体包需手动侧载到 L2 `~/.jachin/inventory/`；PUBLIC 时 zip 上传至 Nexus，审核通过后 L2 可同步拉取。
