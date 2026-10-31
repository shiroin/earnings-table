#!/usr/bin/env python3
"""
時価総額の更新: data/masters.json の各社ティッカーから、直近の時価総額を取って data/mcap.json に書く。
（Yahoo Finance の公開データを yfinance で取得。GitHub Actions で1日2回実行）
"""
import json, re, time, datetime
from pathlib import Path

import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (earnings-table)"}

SUFFIX = {"HKG": ".HK", "SHA": ".SS", "SHE": ".SZ", "KRX": ".KS", "KOSDAQ": ".KQ", "TPE": ".TW", "TPEX": ".TWO",
          "EPA": ".PA", "LON": ".L", "AMS": ".AS", "STO": ".ST", "CPH": ".CO", "ASX": ".AX", "TSE": ".TO",
          "TSX": ".TO", "BIT": ".MI", "SGX": ".SI", "ETR": ".DE", "FRA": ".F", "SWX": ".SW", "TYO": ".T"}
CUR = {"JPY": "円", "USD": "ドル", "KRW": "ウォン", "TWD": "台湾ドル", "HKD": "香港ドル", "CNY": "元", "EUR": "ユーロ",
       "GBP": "ポンド", "SEK": "スウェーデンクローナ", "DKK": "デンマーククローネ", "CHF": "スイスフラン", "CAD": "カナダドル",
       "AUD": "豪ドル", "SGD": "シンガポールドル", "ILS": "シェケル", "BRL": "レアル", "INR": "ルピー"}
MINOR = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ILA": ("ILS", 100), "ZAc": ("ZAR", 100)}


def yahoo_symbol(ticker, country):
    t = (ticker or "").strip().upper()
    if not t:
        return None
    m = re.match(r"^([A-Z]+):(.+)$", t)
    if m:
        ex, code = m.groups()
        if ex == "HKG":
            code = code.lstrip("0").zfill(4)
        return code + SUFFIX.get(ex, "")
    if re.fullmatch(r"\d{4}|\d{3}[A-Z]", t) and country in ("日本", ""):
        return t + ".T"
    return t


def yahoo_search(name):
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search", params={"q": name, "quotesCount": 5, "newsCount": 0},
                         headers=UA, timeout=20)
        for q in r.json().get("quotes", []):
            if q.get("quoteType") == "EQUITY":
                return q["symbol"]
    except Exception:
        pass
    return None


def fmt(value, cur):
    """時価総額 → 「3.94兆ドル」「7,656億円」"""
    name = CUR.get(cur, cur)
    if value >= 1e12:
        t = value / 1e12
        return (f"{t:,.0f}" if t >= 100 else f"{t:.1f}" if t >= 10 else f"{t:.2f}") + "兆" + name
    return f"{value / 1e8:,.0f}億{name}"


def get_cap(sym):
    fi = yf.Ticker(sym).fast_info
    cap, cur = fi.get("marketCap") or fi.get("market_cap"), fi.get("currency")
    if not cap or cap <= 0:
        raise ValueError("時価総額なし")
    if cur in MINOR:
        cur, d = MINOR[cur]
        cap = cap / d
    return float(cap), cur


def main():
    data = json.loads((ROOT / "data" / "masters.json").read_text(encoding="utf-8"))
    outp = ROOT / "data" / "mcap.json"
    old = {}
    if outp.exists():
        try: old = json.loads(outp.read_text(encoding="utf-8")).get("companies", {})
        except Exception: pass
    out, errors = {}, []
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
    for c in data["companies"]:
        key = c.get("ticker") or c.get("name")
        if not c.get("ticker"):
            continue  # 非上場
        sym = yahoo_symbol(c["ticker"], c.get("country", ""))
        tried = []
        for s in [sym, None]:
            if s is None:
                s = yahoo_search(c.get("name") or c.get("ja"))
                if not s or s in tried:
                    break
            tried.append(s)
            try:
                cap, cur = get_cap(s)
                out[key] = {"symbol": s, "value": cap, "currency": cur, "text": fmt(cap, cur), "date": today}
                break
            except Exception as e:
                last = f"{c.get('ja')}（{s}）: {e}"
            time.sleep(0.2)
        else:
            pass
        if key not in out:
            errors.append(last if tried else f"{c.get('ja')}: 銘柄が見つからない")
            if key in old:
                out[key] = old[key]  # 取れなかったら前回の値を残す
    outp.write_text(json.dumps({"updated": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
                                "companies": out, "errors": errors}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"時価総額: {len(out)}社 / エラー {len(errors)}件")
    for e in errors[:40]:
        print("  ", e)


if __name__ == "__main__":
    main()
