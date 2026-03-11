Jachin L1 独立部署包（平台 Nexus + PostgreSQL）
========================================

【使用】
1. 确保已安装 Docker Desktop
2. 将本文件夹拷贝到目标机器
3. 运行: .\启动.ps1 (Windows) 或 ./启动.sh (Linux)

【访问】
L1 平台: http://localhost:3000

【端口冲突】
$env:L1_PORT=3001; .\启动.ps1

【停止】
docker compose down
