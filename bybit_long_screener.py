import os
import time
import json
import urllib.request
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ====== 你的 Telegram 配置 ======
TELEGRAM_BOT_TOKEN = "8956084355:AAHfH9cx52SwwKTErjl3MdNGvywWTcMTB6o"
TELEGRAM_CHAT_ID = "762563666"

def send_telegram_message(text):
    """发送文本消息到 Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        urllib.request.urlopen(urllib.request.Request(
            url, 
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'}
        ))
    except Exception as e:
        print(f"发送 Telegram 消息失败: {e}")

# 假网页响应，用来骗过 Render 的端口检查
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bybit Screener Bot is running!")

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# ---- settings you can change ----
MIN_TURNOVER_USD = 1_000_000
MAX_PULLBACK = 0.5
PIVOT_LOOKBACK = 200
CANDLE_RUN = 3
PIVOT_STRENGTH = 3
MIN_PULLBACKS = 2
MAX_C = 12                # max pullbacks per BASE
TREND_BARS = 10           # GC / DC: bars both SMAs must keep rising / falling
REQUIRE_CLOSE_ABOVE_SMA50 = True
HISTORY_BARS = 2000       # bars downloaded per timeframe (finds the real golden cross)
NEW_COIN_BARS = 150       # fewer daily bars than this = no daily SMA150 yet -> skipped
# ---------------------------------

INPUT_FILE = "bybit_updated.txt"
OUTPUT_1H = "bybit base 1H.txt"
OUTPUT_4H = "bybit base 4H.txt"
KLINE = "https://api.bybit.com/v5/market/kline"
TICKERS = "https://api.bybit.com/v5/market/tickers?category=linear"
HERE = os.path.dirname(os.path.abspath(__file__))


def get_json(url):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                data = json.load(r)
            if data.get("retCode") == 0:
                return data["result"]
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def get_turnover():
    res = get_json(TICKERS)
    if not res:
        raise RuntimeError("Could not fetch tickers from Bybit")
    return {x["symbol"]: float(x["turnover24h"] or 0) for x in res["list"]}


def get_klines(symbol, interval, limit):
    keys = ("t", "o", "h", "l", "c", "v")
    rows, end = [], None
    while len(rows) < limit:
        want = min(1000, limit - len(rows))
        url = f"{KLINE}?category=linear&symbol={symbol}&interval={interval}&limit={want}"
        if end is not None:
            url += f"&end={end}"
        res = get_json(url)
        if not res or not res["list"]:
            break
        chunk = res["list"]
        rows += chunk
        end = int(chunk[-1][0]) - 1
        if len(chunk) < want:
            break
    rows.reverse()
    cols = {"t": 0, "o": 1, "h": 2, "l": 3, "c": 4, "v": 5}
    return {k: [float(x[cols[k]]) for x in rows] for k in keys}


def sma(values, n, offset=0):
    end = len(values) - offset
    if end < n:
        return None
    return sum(values[end - n:end]) / n


def rolling_sma(values, n):
    out, total = [None] * len(values), 0.0
    for i, x in enumerate(values):
        total += x
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


def daily_status(symbol):
    c = get_klines(symbol, "D", 200)["c"]
    if len(c) < NEW_COIN_BARS:
        return "new"
    now, before = sma(c, 150), sma(c, 150, offset=10)
    return "rising" if now is not None and before is not None and now > before else "not rising"


BASE_SCAN_BARS = 4500

def qualifies(o, h, l, c, i):
    body = abs(c[i] - o[i])
    return c[i] < o[i] or (h[i] - l[i]) - body > body


def has_run(o, h, l, c, a, b):
    run = 0
    for i in range(a, b + 1):
        run = run + 1 if qualifies(o, h, l, c, i) else 0
        if run >= CANDLE_RUN:
            return True
    return False


def compact_ok(o, h, l, c, hi, lo, end):
    if lo + 2 <= end and all(qualifies(o, h, l, c, j) for j in (lo, lo + 1, lo + 2)):
        if all(l[j] >= l[lo] and h[j] <= h[lo] for j in (lo + 1, lo + 2)):
            return True
    if lo - 1 >= hi and lo + 1 <= end and all(qualifies(o, h, l, c, j) for j in (lo - 1, lo, lo + 1)):
        if l[lo] <= l[lo - 1] and l[lo + 1] >= l[lo] and h[lo + 1] <= h[lo - 1]:
            return True
    return False


def candle_ok(o, h, l, c, hi, lo, end):
    if lo - hi >= 2 and has_run(o, h, l, c, hi, lo):
        return True
    return compact_ok(o, h, l, c, hi, lo, end)


def trend_ok(a, b, i, n, d):
    if i < n:
        return False
    for k in range(n):
        j = i - k
        x, y, px, py = a[j], b[j], a[j - 1], b[j - 1]
        if x is None or y is None or px is None or py is None:
            return False
        if (x <= y) if d > 0 else (x >= y):
            return False
        if d > 0 and not (x > px and y > py):
            return False
        if d < 0 and not (x < px and y < py):
            return False
    return True


def find_cycle_start(s150, s200):
    active = confirmed = False
    below = 0
    start = None
    for i in range(len(s150)):
        a, b = s150[i], s200[i]
        if a is None or b is None:
            continue
        up = a > b
        prev_up = i > 0 and s150[i - 1] is not None and s200[i - 1] is not None \
            and s150[i - 1] > s200[i - 1]
        if not active:
            if up and not prev_up:
                active, confirmed, below, start = True, False, 0, i
        elif not confirmed:
            if up:
                below = 0
                if trend_ok(s150, s200, i, TREND_BARS, 1):
                    confirmed = True
            else:
                below += 1
                if below >= TREND_BARS:
                    active = False
        elif trend_ok(s150, s200, i, TREND_BARS, -1):
            active = confirmed = False
    return start if active else None


def top_between(h, a, b, h1v):
    if b - a < 1:
        return None
    best = a + 1
    for q in range(a + 1, b + 1):
        if h[q] > h[best]:
            best = q
    return best if h[best] < h1v else None


def pb_pct(h, l, cv):
    return (h[cv[0]] - l[cv[1]]) / h[cv[0]]


def settle(o, h, l, c, pb, h1v, end_idx):
    while len(pb) >= 2:
        cur, prev = pb[-1], pb[-2]
        if pb_pct(h, l, cur) < pb_pct(h, l, prev):
            return
        if len(pb) == 2:
            pb.pop()
            return
        x_low = cur[1]
        pb.pop()
        pb.pop()
        new_hi = top_between(h, pb[-1][1], x_low, h1v)
        if new_hi is None or not candle_ok(o, h, l, c, new_hi, x_low, end_idx):
            return
        pb.append((new_hi, x_low))


def build_base(o, h, l, c, s50, p1, end_idx, cs):
    S = PIVOT_STRENGTH
    h1v = h[p1]
    lows = []
    last_type = 0
    cur_low = 0.0
    last_high_idx, last_high = -1, 0.0
    force_low_at = -1
    if end_idx - 2 >= p1:
        for i in range(p1, end_idx - 1):
            n_high = n_low = False
            if i <= end_idx - S:
                lo_i, hi_i = max(0, i - S), i + S
                n_high = h[i] >= max(h[lo_i:hi_i + 1])
                n_low = l[i] <= min(l[lo_i:hi_i + 1])
            c_high = c_low = False
            if i + 2 <= end_idx:
                left_high = all(h[q] <= h[i] for q in range(max(0, i - S), i))
                if all(qualifies(o, h, l, c, j) for j in (i, i + 1, i + 2)):
                    shape_a = all(l[j] >= l[i] and h[j] <= h[i] for j in (i + 1, i + 2))
                    shape_b = (h[i + 1] <= h[i] and l[i + 1] <= l[i]
                               and l[i + 2] >= l[i + 1] and h[i + 2] <= h[i])
                    if shape_a:
                        c_high, c_low = left_high, True
                    elif shape_b:
                        c_high = left_high
                        force_low_at = i + 1
            is_high = n_high or c_high
            is_low = n_low or c_low or i == force_low_at
            if is_high:
                if last_type == -1:
                    last_type, last_high_idx, last_high = 1, i, h[i]
                elif last_type == 1 and h[i] > last_high:
                    last_high_idx, last_high = i, h[i]
            if is_low:
                if last_type == -1:
                    if l[i] < cur_low:
                        lows[-1] = i
                        cur_low = l[i]
                else:
                    lows.append(i)
                    last_type, cur_low = -1, l[i]
    ok = len(lows) > 0
    if ok and last_type == 1:
        after = last_high_idx + 1
        if after > end_idx:
            ok = False
        else:
            mn_idx = after
            for q in range(after, end_idx + 1):
                if l[q] < l[mn_idx]:
                    mn_idx = q
            lows.append(mn_idx)

    pb = []
    valid = 0
    if ok:
        for x in lows:
            if not pb:
                if s50[x] is not None and l[x] <= s50[x] and x >= cs \
                        and candle_ok(o, h, l, c, p1, x, end_idx):
                    pb.append((p1, x))
                continue
            done = False
            for k in range(len(pb)):
                if l[x] <= l[pb[k][1]]:
                    new_hi = p1 if k == 0 else top_between(h, pb[k - 1][1], x, h1v)
                    del pb[k:]
                    if new_hi is not None and candle_ok(o, h, l, c, new_hi, x, end_idx):
                        pb.append((new_hi, x))
                        settle(o, h, l, c, pb, h1v, end_idx)
                    done = True
                    break
            if not done:
                new_hi = top_between(h, pb[-1][1], x, h1v)
                if new_hi is not None and candle_ok(o, h, l, c, new_hi, x, end_idx):
                    pb.append((new_hi, x))
                    settle(o, h, l, c, pb, h1v, end_idx)
        del pb[MAX_C:]

        prev_low = prev_pb = None
        for k, cv in enumerate(pb):
            high, low = h[cv[0]], l[cv[1]]
            pbv = (high - low) / high
            good = pbv <= MAX_PULLBACK if k == 0 else (high < h1v and low > prev_low and pbv < prev_pb)
            if good and candle_ok(o, h, l, c, cv[0], cv[1], end_idx):
                valid += 1
                prev_low, prev_pb = low, pbv
            else:
                break
    if valid > 0:
        lo1 = pb[0][1]
        if not (s50[lo1] is not None and l[lo1] <= s50[lo1] and lo1 >= cs):
            valid = 0
    return valid, pb[:valid]


def find_forming_base(k):
    c_all = k["c"]
    n_all = len(c_all)
    if n_all < 260:
        return None
    s150 = rolling_sma(c_all, 150)
    s200 = rolling_sma(c_all, 200)
    cs_all = find_cycle_start(s150, s200)
    if cs_all is None:
        return None

    n = min(n_all, BASE_SCAN_BARS)
    shift = n_all - n
    o, h, l, c = k["o"][shift:], k["h"][shift:], k["l"][shift:], k["c"][shift:]
    s50 = rolling_sma(c_all, 50)[shift:]
    cs = max(0, cs_all - shift)
    ce = n - 1
    S = PIVOT_STRENGTH

    floor_idx = -1
    base_no = 0
    last = None
    while True:
        p_p = -1
        from_i = floor_idx + 1 if floor_idx >= 0 else cs
        if ce - S >= from_i:
            for i in range(from_i, ce - S + 1):
                mn = min(l[max(0, i - S):i + S + 1])
                if l[i] <= mn and s50[i] is not None and l[i] <= s50[i]:
                    p_p = i
                    break
        if p_p < 0:
            break
        floor_h = floor_idx if floor_idx >= 0 else 0
        start_h = max(floor_h, p_p - PIVOT_LOOKBACK)
        if p_p - 1 < start_h:
            floor_idx = p_p
            continue
        hp = start_h
        for q in range(start_h, p_p + 1):
            if h[q] > h[hp]:
                hp = q
        brk = -1
        for q in range(p_p + 1, ce + 1):
            if h[q] > h[hp]:
                brk = q
                break
        end_idx = brk - 1 if brk >= 0 else ce
        valid, pb = build_base(o, h, l, c, s50, hp, end_idx, cs)
        if valid >= MIN_PULLBACKS:
            base_no += 1
            last = {"hp": hp, "brk": brk, "pullbacks": pb, "base_no": base_no}
        if brk < 0:
            break
        floor_idx = brk
    if last is None or last["brk"] >= 0:
        return None
    last["k"] = {"o": o, "h": h, "l": l, "c": c, "s50": s50}
    return last


def check_forming(k):
    c = k["c"]
    if REQUIRE_CLOSE_ABOVE_SMA50:
        s50 = sma(c, 50)
        if s50 is None or not c[-1] > s50:
            return None
    return find_forming_base(k)


def base_text(res):
    w = res["k"]
    top = w["h"][res["hp"]]
    cs = " | ".join(f"C{i + 1}: {(w['h'][hi] - w['l'][lo]) / w['h'][hi] * 100:.1f}%"
                    for i, (hi, lo) in enumerate(res["pullbacks"]))
    return f"BASE {res['base_no']} top {top:g}  {cs}"


def analyze_setup(symbol, interval):
    return check_forming(get_klines(symbol, interval, HISTORY_BARS))


def main():
    if not os.path.exists(os.path.join(HERE, INPUT_FILE)):
        print(f"警告: 未找到 {INPUT_FILE} 文件")
        return
        
    with open(os.path.join(HERE, INPUT_FILE), encoding="utf-8") as f:
        items = [x.strip() for x in f.read().split(",") if x.strip()]

    turnover = get_turnover()
    list_1h, list_4h = [], []
    for i, item in enumerate(items, 1):
        symbol = item.split(":")[1].removesuffix(".P")
        if turnover.get(symbol, 0) < MIN_TURNOVER_USD:
            continue
        status = daily_status(symbol)
        if status == "new":
            print(f"[{i}/{len(items)}] SKIP {symbol}  (no daily SMA150 yet)")
        elif status == "rising":
            r1h = analyze_setup(symbol, "60")
            r4h = analyze_setup(symbol, "240")
            if r1h:
                list_1h.append(item)
            if r4h:
                list_4h.append(item)
            if r1h or r4h:
                parts = []
                if r1h:
                    parts.append("1H " + base_text(r1h))
                if r4h:
                    parts.append("4H " + base_text(r4h))
                print(f"[{i}/{len(items)}] FORMING {symbol}   " + "   ||   ".join(parts))
        time.sleep(0.05)

    for name, result in ((OUTPUT_1H, list_1h), (OUTPUT_4H, list_4h)):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(",".join(result))
    print(f"\nDone: 1H {len(list_1h)} -> {OUTPUT_1H} | 4H {len(list_4h)} -> {OUTPUT_4H}")


if __name__ == "__main__":
    threading.Thread(target=run_health_check_server, daemon=True).start()
    
    print("Bybit 选股监控云端服务已启动...")
    while True:
        try:
            print("开始执行新一轮选股扫描...")
            main()
            
            content_1h = ""
            content_4h = ""
            if os.path.exists(os.path.join(HERE, OUTPUT_1H)):
                with open(os.path.join(HERE, OUTPUT_1H), encoding="utf-8") as f:
                    content_1h = f.read()
            if os.path.exists(os.path.join(HERE, OUTPUT_4H)):
                with open(os.path.join(HERE, OUTPUT_4H), encoding="utf-8") as f:
                    content_4h = f.read()
                
            msg = f"📊 *Bybit 选股扫描完成*\n\n📈 *1H 基础形态*:\n`{content_1h[:3000] if content_1h else '无'}`\n\n📈 *4H 基础形态*:\n`{content_4h[:3000] if content_4h else '无'}`"
            send_telegram_message(msg)
            
        except Exception as e:
            print(f"运行出错: {e}")
            send_telegram_message(f"⚠️ Bybit 选股脚本运行出错: {e}")
        
        print("扫描完成，等待下一次循环 (4小时)...")
        time.sleep(14400)
