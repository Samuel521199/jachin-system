-- 创建 jmp-packages Storage Bucket（云端弹药库）
-- 用于存放 Forge 发布的 .jmp 武器包，Public 读（下载链接带 Hash 不可猜测）
-- 仅服务端 Service Role Key 可写
INSERT INTO storage.buckets (id, name, public)
SELECT gen_random_uuid(), 'jmp-packages', true
WHERE NOT EXISTS (SELECT 1 FROM storage.buckets WHERE name = 'jmp-packages');
