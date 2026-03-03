# GitHub / Google OAuth 登录配置指南

本文档说明如何在 Jachin Nexus Layer 1 中启用 GitHub 和 Google 一键登录。

---

## 一、Supabase 回调地址

在配置 OAuth 前，先确认 Supabase 项目的回调地址：

```
https://<你的项目ID>.supabase.co/auth/v1/callback
```

例如：`https://abcdefgh.supabase.co/auth/v1/callback`

在 Supabase Dashboard → Authentication → URL Configuration 中，添加 **Redirect URLs**：

- `http://localhost:3000/auth/callback`
- `https://你的域名.com/auth/callback`

---

## 二、GitHub OAuth 配置

### 1. 创建 GitHub OAuth App

1. 打开 [GitHub Developer Settings](https://github.com/settings/developers)
2. 点击 **OAuth Apps** → **New OAuth App**
3. 填写：

| 字段 | 值 |
|------|-----|
| **Application name** | Jachin Nexus（或任意名称） |
| **Homepage URL** | `http://localhost:3000`（本地）或 `https://你的域名.com` |
| **Authorization callback URL** | `https://<项目ID>.supabase.co/auth/v1/callback` |

4. 点击 **Register application**
5. 在应用页面生成 **Client Secret**，记录 **Client ID** 和 **Client Secret**

### 2. 在 Supabase 中启用 GitHub

1. 打开 Supabase Dashboard → **Authentication** → **Providers**
2. 找到 **GitHub**，点击启用
3. 填入：
   - **Client ID**：从 GitHub OAuth App 复制
   - **Client Secret**：从 GitHub OAuth App 复制
4. 保存

---

## 三、Google OAuth 配置

### 1. 创建 Google OAuth 2.0 凭据

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 选择或创建项目
3. 进入 **APIs & Services** → **Credentials**
4. 点击 **Create Credentials** → **OAuth client ID**
5. 若未配置 OAuth 同意屏幕，先完成：
   - **User Type**：External（外部用户）
   - **App name**：Jachin Nexus
   - **User support email**：你的邮箱
   - **Developer contact**：你的邮箱
   - **Scopes**：添加 `email`、`profile`、`openid`
6. 创建 OAuth 客户端：
   - **Application type**：Web application
   - **Name**：Jachin Nexus Web
   - **Authorized JavaScript origins**：
     - `http://localhost:3000`
     - `https://你的域名.com`
   - **Authorized redirect URIs**：
     - `https://<项目ID>.supabase.co/auth/v1/callback`
7. 创建后记录 **Client ID** 和 **Client Secret**

### 2. 在 Supabase 中启用 Google

1. 打开 Supabase Dashboard → **Authentication** → **Providers**
2. 找到 **Google**，点击启用
3. 填入：
   - **Client ID**：从 Google Console 复制
   - **Client Secret**：从 Google Console 复制
4. 保存

---

## 四、验证流程

1. 启动应用：`npm run dev` 或 `start.bat cloud`
2. 访问 `http://localhost:3000/login`
3. 点击 **GitHub** 或 **Google** 按钮
4. 完成授权后应跳转回 `/console`

---

## 五、常见问题

| 问题 | 处理 |
|------|------|
| `redirect_uri_mismatch` | 检查 GitHub/Google 中的回调 URL 与 Supabase 完全一致 |
| `invalid_client` | 检查 Client ID 和 Client Secret 是否正确复制 |
| 登录后跳转到空白页 | 检查 Supabase Redirect URLs 是否包含 `/auth/callback` |
| 本地开发无法登录 | 确保 Authorized origins 包含 `http://localhost:3000` |

---

## 六、代码说明

登录页 `src/app/login/page.tsx` 中已实现：

```ts
supabase.auth.signInWithOAuth({
  provider: "github" | "google",
  options: {
    redirectTo: `${origin}/auth/callback?next=${redirectTo}`,
  },
});
```

回调页 `src/app/auth/callback/route.ts` 会处理 OAuth 返回的 `code`，交换 Session 后重定向到控制台。
