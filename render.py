#!/usr/bin/env python3
"""
Webアプリと同じ見た目の決算比較表PNGを、コマンドラインで作る（毎朝の自動まとめ用）

  python3 render.py 決算.csv -o 決算.png --title "米国企業の決算（10月28日発表）" --period below
  python3 render.py --seg NVDA -o NVDA_seg.png [--seg-period FY2027Q2] [--seg-profit]   # セグメント表（企業マスターから）

CSVの列（スプレッドシート「決算データ」と同じ名前）:
  企業, 期, 単位, 売上高, 売上高前年比, 営業利益, 営業利益前年比, 営業利益率(空欄=自動), 時価総額
"""
import argparse, csv, re, functools, http.server, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
KEYS = ["企業", "社名表示", "期", "単位", "売上高", "売上高前年比", "営業利益", "営業利益前年比", "営業利益率", "時価総額"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="?")
    ap.add_argument("--seg", help="セグメント表を作る企業（ティッカー・英語名・日本名）")
    ap.add_argument("--seg-period", help="期（例 FY2027Q2）。省略で最新")
    ap.add_argument("--seg-profit", action="store_true", help="セグメント利益も入れる")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--period", choices=["corner", "below", "none"], default="corner", help="期の表示: corner=ロゴの右上 / below=ロゴの下")
    ap.add_argument("--minus", default="△")
    ap.add_argument("--yen", action="store_true", help="金額を日本円に換算（為替は config.json の fx、--fx で上書き）")
    ap.add_argument("--fx", help='為替の上書き（例: USD=157.9,KRW=0.108）')
    ap.add_argument("--align", choices=["auto", "left", "right"], default="auto", help="売上高・営業利益・時価総額の揃え（auto: 通貨がそろえば右、混在なら左）")
    ap.add_argument("--palette", default="teal", help="配色: teal / green / wine / charcoal / purple / ocean")
    ap.add_argument("--head", help="見出しの色（例 #1F6E75）。指定すると配色より優先")
    ap.add_argument("--accent", help="営業利益率の色（例 #EE7E1A）")
    ap.add_argument("--scale", type=float, default=2)
    a = ap.parse_args()
    rows = []
    if a.csv:
      with open(a.csv, encoding="utf-8-sig", newline="") as f:
        rows = [{k: (r.get(k) or "").strip() for k in KEYS} for r in csv.DictReader(f) if (r.get("企業") or "").strip()]
    elif not a.seg:
        ap.error("CSV か --seg を指定してください")
    for r in rows:  # 社名表示: ○ / 1 / TRUE などで表示
        r["社名表示"] = bool(re.match(r"^(○|〇|◯|1|true|yes|はい|y|on|表示)$", r["社名表示"], re.I))

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *x): pass
    handler = functools.partial(Quiet, directory=str(ROOT))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            page = b.new_page(viewport={"width": 4000, "height": 1200}, device_scale_factor=a.scale)
            page.goto(f"http://127.0.0.1:{srv.server_port}/index.html?headless=1")
            page.wait_for_function("window.__ready === true")
            if a.seg:
                page.evaluate("([k,o]) => window.__renderSeg(k,o)", [a.seg, {"period": a.seg_period, "profit": a.seg_profit, "palette": a.palette, "title": a.title}])
            else:
                page.evaluate("([r,o]) => window.__render(r,o)", [rows, {"title": a.title or "注目企業の決算", "period": a.period, "minus": a.minus, "align": a.align, "cur": "jpy" if a.yen else "local",
                    "fx": {k: float(v) for k, v in (x.split("=") for x in a.fx.split(","))} if a.fx else None, "palette": a.palette, "head": a.head, "accent": a.accent}])
            page.wait_for_timeout(400)
            page.locator("#canvas").screenshot(path=a.out)
            b.close()
    finally:
        srv.shutdown()
    print("画像を作りました:", a.out)


if __name__ == "__main__":
    main()
