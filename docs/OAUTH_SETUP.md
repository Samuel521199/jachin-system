# GitHub / Google OAuth 登录配置指南

本文档说明如何在 Jachin Nexus Layer 1 中启用 GitHub 和 Google 一键登录。

> **说明**：Nexus 使用 Auth.js + Drizzle ORM。OAuth 配置请参考 [Auth.js 文档](https://authjs.dev/getting-started/installation) 与项目 `auth.config.ts`。回调地址为 `http://localhost:3000/auth/callback` 或 `https://你的域名/auth/callback`。

---

## 一、GitHub OAuth App 创建

1. 打开 [GitHub Developer Settings](https://github.com/settings/developers)
2. 点击 **OAuth Apps** → **New OAuth App**
3. 填写：
   - **Application name**：Jachin Nexus
   - **Homepage URL**：`http://localhost:3000` 或生产域名
   - **Authorization callback URL**：`http://localhost:3000/auth/callback`
4. 记录 **Client ID** 和 **Client Secret**，填入 `.env.local`：
   ```
   AUTH_GITHUB_ID=xxx
   AUTH_GITHUB_SECRET=xxx
   ```

---

## 二、Google OAuth 配置

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建 OAuth 2.0 凭据，授权重定向 URI：`http://localhost:3000/auth/callback`
3. 将 Client ID 和 Secret 填入 `.env.local`：
   ```
   AUTH_GOOGLE_ID=xxx
   AUTH_GOOGLE_SECRET=xxx
   ```
