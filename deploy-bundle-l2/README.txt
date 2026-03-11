Jachin L2 独立部署包（控制面 + Redis）
====================================

【新机器首次启动 / 覆盖旧部署】
1. 确保已安装 Docker Desktop
2. 将本文件夹拷贝到目标机器
3. 进入目录，清理旧容器和镜像（若有）：
   docker compose down
   docker rmi jachin/l2-control:patched -f 2>$null
   docker rmi jachin/l2-control:latest -f 2>$null
4. 运行: .\启动.ps1 (Windows) 或 ./启动.sh (Linux)

【已有部署，仅重启】
docker compose down
.\启动.ps1

【访问】
L2 API: http://localhost:18888

【与 L1 配对（L1 与 L2 分机部署时）】
1. 在有源码的机器上执行: .\scripts\run-pair.ps1 -BaseUrl http://L1机器IP:3000
2. 配对成功后，将 instance_id、access_token、l1_user_id 填入本目录 .env
3. 参考 .env.example，或启动前设置:
   $env:NEXUS_BASE_URL="http://L1机器IP:3000"; .\启动.ps1

详见 deploy/PAIRING_L1_L2.md

【端口冲突】
$env:L2_PORT=18889; .\启动.ps1

【停止】
docker compose down
