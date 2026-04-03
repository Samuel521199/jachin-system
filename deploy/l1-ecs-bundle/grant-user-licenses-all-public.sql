-- =============================================================================
-- 补 user_licenses（全在 ECS 上操作；库账号与 l1.env 一致）
--
-- 【步骤 1】把本文件放到服务器目录（二选一）
--
--   方式 A：你本机仓库里已有本文件时，用 scp 上传到 ECS：
--     scp deploy/l1-ecs-bundle/grant-user-licenses-all-public.sql root@47.86.39.173:/opt/jachin-l1/
--
--   方式 B：已 SSH 登录 ECS 后，在服务器上创建文件：
--     mkdir -p /opt/jachin-l1
--     vi /opt/jachin-l1/grant-user-licenses-all-public.sql
--     （把本文件从仓库里整段复制粘贴进去，保存退出）
--
-- 【步骤 2】SSH 登录 ECS 后执行（下面密码为 postgres，与 DATABASE_URL 一致，无需再改）：
--
--     cd /opt/jachin-l1
--     PGPASSWORD=postgres psql -h 127.0.0.1 -U jachin -d jachin_nexus -f grant-user-licenses-all-public.sql
--
-- 【步骤 3】若 L2 的 tenant_id 不是默认用户，只改下面 INSERT 里第一处字符串，
--     与 docker exec ... cat /root/.jachin/nexus_config.json 里的 tenant_id 一致。
-- =============================================================================

INSERT INTO user_licenses (tenant_id, item_id, status)
SELECT '00000000-0000-0000-0000-000000000001', pr.id, 'ACTIVE'
FROM plugins_registry pr
WHERE pr.visibility = 'PUBLIC'
  AND pr.status = 'approved'
ON CONFLICT (tenant_id, item_id) DO NOTHING;

SELECT tenant_id, COUNT(*) AS cnt FROM user_licenses GROUP BY tenant_id ORDER BY tenant_id;
