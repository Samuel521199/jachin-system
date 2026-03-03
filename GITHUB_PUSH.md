# 上传到 GitHub 指南

## 当前状态

- ✅ Git 仓库已初始化
- ✅ 首次提交已完成（commit message 为系统白皮书与说明）
- ✅ 标签已创建（见 CHANGELOG.md 当前版本）

## 推送到 GitHub

### 1. 在 GitHub 创建新仓库（若尚未创建）

1. 登录 https://github.com
2. 点击 **New repository**
3. 仓库名：`jachin-system`
4. 描述：`分布式智能体操作系统 (Distributed Agent OS)`
5. 选择 **Public** 或 **Private**
6. **不要**勾选 "Add a README"（项目已有）
7. 点击 **Create repository**

### 2. 添加远程并强制推送（以本地为准、清理远程无用文件）

在项目根目录执行（替换为你的用户名）：

```powershell
# 添加远程
git remote add origin https://github.com/你的用户名/jachin-system.git

# 强制推送 - 使远程与本地完全一致，清理 GitHub 上旧的无用文件
git push -u origin main --force

# 推送标签（替换为 CHANGELOG 中的当前版本）
git push origin v0.5.6 --force
```

### 3. 使用脚本（已配置默认 Samuel521199）

```powershell
.\scripts\push_to_github.ps1
# 或指定仓库：
.\scripts\push_to_github.ps1 -RepoUrl "https://github.com/你的用户名/jachin-system.git"
```

### 4. 若已有远程且需更新

```powershell
git remote set-url origin https://github.com/你的用户名/jachin-system.git
git push -u origin master --force
git push origin v0.2.0 --force
```

## 版本信息

- **分支**: main（若远程为 master 则需先迁移）
- **标签**: 见 CHANGELOG.md（当前 v0.5.x）
- **更新日志**: CHANGELOG.md
