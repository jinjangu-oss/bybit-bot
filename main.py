import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from pybit.unified_trading import HTTP

# ==================== 配置区 ====================
TELEGRAM_BOT_TOKEN = "8956084355:AAHfH9cx52SwwKTErjl3MdNGvywWTcMTB6o"
TELEGRAM_CHAT_ID = "762563666"

BYBIT_API_KEY = "你的模拟盘API_KEY"      # 填入你的 API Key
BYBIT_API_SECRET = "你的模拟盘API_SECRET"   # 填入你的 API Secret

# 我们想要监控的所有盈亏比档位
TARGET_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]
# ===============================================

# 记录哪些仓位在哪些档位已经提醒过，格式：{(symbol_side_entry): {1.0: True, 1.5: True, ...}}
notified_levels = {}

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bybit Bot is running!")

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

def send_telegram_message(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"发送 Telegram 失败: {e}")

def check_bybit_positions():
    try:
        session = HTTP(
            testnet=True,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        response = session.get_positions(category="linear", settleCoin="USDT")
        if response['retCode'] == 0:
            positions = response['result']['list']
            for pos in positions:
                size = float(pos.get('size', 0))
                if size > 0:
                    symbol = pos['symbol']
                    side = pos['side']
                    entry_price = float(pos['avgPrice'])
                    mark_price = float(pos['markPrice'])
                    stop_loss = float(pos.get('stopLoss', 0))
                    
                    if stop_loss > 0:
                        risk = abs(entry_price - stop_loss)
                        if risk > 0:
                            reward = mark_price - entry_price if side == "Buy" else entry_price - mark_price
                            current_rr = reward / risk
                            print(f"[{symbol}] 方向: {side} | 当前盈亏比: {current_rr:.2f}")
                            
                            pos_key = f"{symbol}_{side}_{entry_price}"
                            if pos_key not in notified_levels:
                                notified_levels[pos_key] = {}
                            
                            # 逐个检查各个档位是否达标
                            for level in TARGET_LEVELS:
                                if current_rr >= level and not notified_levels[pos_key].get(level, False):
                                    msg = (
                                        f"🎯 *Bybit 盈亏比达标提醒* 🎯\n\n"
                                        f"• **币种**: {symbol}\n"
                                        f"• **方向**: {side}\n"
                                        f"• **开仓价**: {entry_price}\n"
                                        f"• **当前价**: {mark_price}\n"
                                        f"• **已达成盈亏比**: `1:{level:.1f}` (当前实际: `{current_rr:.2f}:1`)"
                                    )
                                    send_telegram_message(msg)
                                    notified_levels[pos_key][level] = True  # 标记该档位已提醒
                    else:
                        print(f"[{symbol}] 未设置止损价，无法计算盈亏比。")
    except Exception as e:
        print(f"请求出错: {e}")

if __name__ == "__main__":
    threading.Thread(target=run_health_check_server, daemon=True).start()
    print("多档位盈亏比监控程序已启动...")
    while True:
        check_bybit_positions()
        time.sleep(30)
