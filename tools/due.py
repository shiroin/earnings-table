#!/usr/bin/env python3
"""決算発表予定の一覧: data/mcap.json の発表予定日（sched: 日本株は株探、その他はYahoo）と過去の発表日から、
指定した期間に発表する（した）ウォッチリスト企業を出す。
  python3 tools/due.py                 # 今日（日本時間）
  python3 tools/due.py --from 2026-11-01 --to 2026-11-02
出力: 日付, 企業(日本名), ティッカー, 本拠地, マスターの最新期
"""
import argparse, json, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def main():
    today = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=9)).date().isoformat()
    ap = argparse.ArgumentParser(); ap.add_argument("--from", dest="f", default=today); ap.add_argument("--to", dest="t", default=today); a = ap.parse_args()
    m = json.loads((ROOT / "data/masters.json").read_text(encoding="utf-8"))["companies"]
    mc = json.loads((ROOT / "data/mcap.json").read_text(encoding="utf-8"))["companies"]
    out = []
    for c in m:
        e = mc.get(c.get("ticker")) or mc.get(c.get("name")) or {}
        ds = set(e.get("sched") or []) | set(e.get("earn_dates") or [])
        for d in sorted(ds):
            if a.f <= d <= a.t:
                lab = (c.get("periods") or [{}])[-1].get("period", "")
                out.append((d, c.get("ja"), c.get("ticker"), c.get("country", ""), lab))
    out.sort()
    print(f"{a.f}〜{a.t} の発表予定・発表 {len(out)}社（予定日は目安。実際の発表は必ず確認）")
    for r in out: print("\t".join(map(str, r)))

if __name__ == "__main__":
    main()
