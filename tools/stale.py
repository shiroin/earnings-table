#!/usr/bin/env python3
"""
取りこぼし一覧: 株価データ上は新しい決算が出ているのに、企業マスターにも「決算データ」シートにもまだ数字がない会社。
  python3 tools/stale.py [--days 30]      # 直近30日以内に発表された分だけ（新しい順）
出力: 企業(日本名), ティッカー, マスターの最新期, 最新の発表日
"""
import argparse, csv, io, json, re, datetime
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def qmatch(ds, E, q):
    for d in ds:
        t = datetime.date.fromisoformat(d) - datetime.timedelta(days=15); mo = t.month
        for k in range(3):
            mm = ((mo - 1 - k) % 12) + 1
            if (mm - E) % 3 == 0: mo = mm; break
        if (((mo - E) % 12) // 3 or 4) == q: return d
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=30); a = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    m = json.loads((ROOT / "data/masters.json").read_text(encoding="utf-8"))["companies"]
    mc = json.loads((ROOT / "data/mcap.json").read_text(encoding="utf-8"))["companies"]
    url = f"https://docs.google.com/spreadsheets/d/{cfg['sheetId']}/gviz/tq?tqx=out:csv&sheet=%E6%B1%BA%E7%AE%97%E3%83%87%E3%83%BC%E3%82%BF"
    try: dg = list(csv.DictReader(io.StringIO(urllib.request.urlopen(url, timeout=30).read().decode("utf-8"))))
    except Exception: dg = []
    latest_dg = {}
    for d in dg:
        k = d.get("企業", ""); latest_dg[k] = max(latest_dg.get(k, ""), d.get("発表日", ""))
    since = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    out = []
    for c in m:
        ds = (mc.get(c.get("ticker")) or {}).get("earn_dates") or []
        if not c.get("periods") or not ds or ds[0] < since: continue
        p = c["periods"][-1]
        E = int(re.sub(r"\D", "", c.get("fy_end") or "") or 0)
        if not E and re.fullmatch(r"\s*(CY)?\s*\d{4}\s*[-_ ]?Q[1-4]\s*", p["period"], re.I): E = 12
        qm = re.search(r"Q([1-4])", p["label"] or "")
        if not E or not qm: continue
        md = qmatch(ds, E, int(qm.group(1)))
        if md and md >= ds[0]: continue                      # マスターが最新
        dgd = max(latest_dg.get(c["ja"], ""), latest_dg.get(c["name"], ""))
        if dgd and dgd >= ds[0]: continue                    # 決算データに最新がある
        out.append((ds[0], c["ja"], c["ticker"], c.get("country", ""), p["label"]))
    out.sort(reverse=True)
    print(f"取りこぼし {len(out)}社（{since} 以降の発表）")
    for d, ja, t, co, lab in out:
        print(f"{d}\t{ja}\t{t}\t{co}\tマスター最新:{lab}")


if __name__ == "__main__":
    main()
