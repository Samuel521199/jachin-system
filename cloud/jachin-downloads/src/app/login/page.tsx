import { LoginForm } from "./login-form";
import { isDownloadsDevLoginBypassEnabled } from "@/lib/dev-login-bypass";

/** 避免构建时写死是否展示 GitHub；以运行时 .env 为准 */
export const dynamic = "force-dynamic";

export default function LoginPage() {
  const showGithub = Boolean(
    process.env.AUTH_GITHUB_ID?.trim() && process.env.AUTH_GITHUB_SECRET?.trim()
  );
  const devBypass = isDownloadsDevLoginBypassEnabled();
  return <LoginForm showGithub={showGithub} devBypass={devBypass} />;
}
