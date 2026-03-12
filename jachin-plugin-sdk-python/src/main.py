"""
Jachin Plugin SDK - 示例插件：加密货币价格查询

使用 @jachin_plugin 装饰器，5 分钟上架到 Jachin 商城赚分润。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 确保同目录 jachin_sdk 可导入（开发时 python src/main.py）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jachin_sdk import jachin_plugin


@jachin_plugin
def fetch_crypto_price(ticker: str) -> dict:
    """
    查询加密货币价格（模拟数据，演示用）。

    Args:
        ticker: 币种代码，如 BTC、ETH、SOL

    Returns:
        包含价格、涨跌幅、时间戳的字典
    """
    # 模拟价格数据（生产环境可接入 CoinGecko、Binance 等 API）
    prices = {
        "BTC": 97_500.0,
        "ETH": 3_420.0,
        "SOL": 218.0,
        "DOGE": 0.42,
        "XRP": 2.18,
    }
    change = {
        "BTC": 2.3,
        "ETH": -0.8,
        "SOL": 5.2,
        "DOGE": 12.1,
        "XRP": -1.2,
    }
    ticker_upper = (ticker or "BTC").strip().upper()
    price = prices.get(ticker_upper, 0.0)
    delta = change.get(ticker_upper, 0.0)

    return {
        "ticker": ticker_upper,
        "price_usd": price,
        "change_24h": delta,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


if __name__ == "__main__":
    from jachin_sdk import run

    run()
