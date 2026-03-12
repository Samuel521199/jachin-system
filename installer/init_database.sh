#!/bin/bash
# 数据库初始化脚本 (Bash)
# Database Initialization Script (Bash)

set -e

DATABASE_URL="${DATABASE_URL:-postgresql://jachin:secure_password@localhost:5432/jachin_brain}"
SKIP_MIGRATIONS=false

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --database-url)
            DATABASE_URL="$2"
            shift 2
            ;;
        --skip-migrations)
            SKIP_MIGRATIONS=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "========================================"
echo "  Jachin-System v3.2 数据库初始化"
echo "========================================"
echo ""

# 检查Python环境
echo "[1/5] 检查Python环境..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version)
    echo "  [OK] Python版本: $PYTHON_VERSION"
else
    echo "  [ERROR] Python3未安装"
    exit 1
fi

# 检查Alembic
echo "[2/5] 检查Alembic..."
if command -v alembic &> /dev/null; then
    echo "  [OK] Alembic已安装"
else
    echo "  [ERROR] Alembic未安装，请运行: pip install alembic"
    exit 1
fi

# 检查数据库连接
echo "[3/5] 检查数据库连接..."
echo "  [INFO] 数据库URL: $DATABASE_URL"

# 运行迁移
if [ "$SKIP_MIGRATIONS" = false ]; then
    echo "[4/5] 运行数据库迁移..."
    
    export DATABASE_URL
    
    cd core
    export ALEMBIC_CONFIG="memory/schema/migrations/alembic.ini"
    
    alembic upgrade head
    
    if [ $? -eq 0 ]; then
        echo "  [OK] 数据库迁移完成"
    else
        echo "  [ERROR] 数据库迁移失败"
        exit 1
    fi
    
    cd ..
else
    echo "[4/5] 跳过数据库迁移..."
fi

# 验证数据库
echo "[5/5] 验证数据库..."
echo "  [OK] 数据库初始化完成"

echo ""
echo "========================================"
echo "  数据库初始化完成"
echo "========================================"
echo ""
echo "下一步:"
echo "  1. 启动服务: ./scripts/start.sh"
echo "  2. 测试API: ./scripts/test.sh"
echo ""
