# Jachin Nexus (Layer 1)

灵界枢纽：智慧分发、协议标准、神经元商城、舰队大盘、IM 网关。

## 核心 API

| 路径 | 说明 |
|------|------|
| `POST /api/v1/agents/heartbeat` | 边缘 Agent 心跳，返回 blueprint、task（IM 消息） |
| `POST /api/v1/agents/result` | Agent 执行结果回传，推回 TG/飞书 |
| `POST /api/v1/webhooks/telegram` | Telegram 机器人 Webhook |
| `POST /api/v1/agents/bind-im` | 绑定 Agent 与 Telegram chat_id |

详见 [docs/IM_GATEWAY_SPEC.md](../../docs/IM_GATEWAY_SPEC.md)、[docs/NEXUS_DAEMON.md](../../docs/NEXUS_DAEMON.md)。

## 环境变量

见 `.env.example`。IM 网关需 `TELEGRAM_BOT_TOKEN`（从 @BotFather 获取）。**战役 2 物理基建**（Ngrok、setWebhook、绑定 Chat ID）见 [docs/TELEGRAM_TUNNEL_SETUP.md](../docs/TELEGRAM_TUNNEL_SETUP.md)。

---

## Getting Started

First, run the development server:

```bash
npm run dev
# or
yarn dev
# or
pnpm dev
# or
bun dev
```

Open [http://localhost:3000](http://localhost:3000) with your browser to see the result.

You can start editing the page by modifying `app/page.tsx`. The page auto-updates as you edit the file.

This project uses [`next/font`](https://nextjs.org/docs/app/building-your-application/optimizing/fonts) to automatically optimize and load [Geist](https://vercel.com/font), a new font family for Vercel.

## Learn More

To learn more about Next.js, take a look at the following resources:

- [Next.js Documentation](https://nextjs.org/docs) - learn about Next.js features and API.
- [Learn Next.js](https://nextjs.org/learn) - an interactive Next.js tutorial.

You can check out [the Next.js GitHub repository](https://github.com/vercel/next.js) - your feedback and contributions are welcome!

## Deploy on Vercel

The easiest way to deploy your Next.js app is to use the [Vercel Platform](https://vercel.com/new?utm_medium=default-template&filter=next.js&utm_source=create-next-app&utm_campaign=create-next-app-readme) from the creators of Next.js.

Check out our [Next.js deployment documentation](https://nextjs.org/docs/app/building-your-application/deploying) for more details.
