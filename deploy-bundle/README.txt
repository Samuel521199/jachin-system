Jachin L1 + L2 部署包
====================

【目标机器使用】
1. 确保已安装 Docker Desktop
2. 将本文件夹完整拷贝到目标机器
3. 启动方式（任选其一）:
   - PowerShell: .\启动.ps1  （推荐）
   - CMD 命令提示符: 启动.bat
   - Linux/Mac: chmod +x 启动.sh && ./启动.sh

【访问地址】
L1 平台: http://localhost:3000
L2 API:  http://localhost:18888

【端口冲突】
若 3000 或 18888 被占用，可设置环境变量后启动:
  PowerShell: $env:L1_PORT=3001; $env:L2_PORT=18889; .\启动.ps1

【停止服务】
docker compose -f docker-compose.deploy-l1-l2-images.yml down

【说明】
- jachin-l1-l2-images.tar 需在有代码的机器上运行 deploy\生成部署包.bat 生成
- 若本文件夹缺少 .tar 文件，请从开发机重新生成部署包
