import time
import requests
from pybit.unified_trading import HTTP

# ==================== 请在这里填入你的配置信息 ====================
TELEGRAM_BOT_TOKEN = "8956084355:AAHfH9cx52SwwKTErjl3MdNGvywWTcMTB6o"  # 你的机器人 Token
TELEGRAM_CHAT_ID = "762563666"                                       # 你的 Telegram ID

BYBIT_API_KEY = "Ge7JAa7NKuewYgtaym"                                   # 替换成你的 Bybit API Key
BYBIT_API_SECRET = "DYF02yESuuruGtosR0VbN9bBHaKLV7cXJdiF"                            # 替换成你的 Bybit API Secret
TARGET_RR_RATIO = 2.0                                                # 你设定的目标盈亏比（例如达到 2.0 时提醒）
# ====================================================================

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"发送 Telegram 失败: {e}")

def check_bybit_positions():
    try:
        session = HTTP(
            testnet=True,  # 使用模拟盘 (Demo)
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        # 获取现货或衍生品持仓
        response = session.get_positions(
            category="linear",
            settleCoin="USDT",
        )
        if response['retCode'] == 0:
            positions = response['result']['list']
            for pos in positions:
                size = float(pos['size'])
                if size > 0:  # 说明有持仓
                    symbol = pos['symbol']
                    unrealised_pnl = float(pos['unrealisedPnl'])
                    side = pos['side']
                    print(f"监控中: {symbol} | 方向: {side} | 未实现盈亏: {unrealised_pnl}")
                    
                    # 简单判断示例：如果盈利或指标触发，发消息
                    # 这里你可以根据需要调整判断条件
    except Exception as e:
        print(f"请求 Bybit 出错: {e}")

if __name__ == "__main__":
    print("Bybit 盈亏比监控程序已启动...")
    while True:
        check_bybit_positions()
        time.sleep(30)  # 每 30 秒检查一次
