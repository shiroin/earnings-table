#!/usr/bin/env python3
"""
Webアプリと同じ見た目の決算比較表PNGを、コマンドラインで作る（毎朝の自動まとめ用）

  python3 render.py 決算.csv -o 決算.png --title "米国企業の決算（10月28日発表）" --period below

CSVの列（スプレッドシート「決算データ」と同じ名前）:
  企業, 期, 単位, 売上高, 売上高前年比, 営業利益, 営業利益前年比, 営業利益率(空欄=自動), 時価総額
"""
import argparse, csv, functools, http.server, threading
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
KEYS = ["企業", "期", "単位", "売上高", "売上高前年比", "営業利益", "営業利益前年比", "営業利益率", "時価総額"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--title", default="注目企業の決算")
    ap.add_argument("--period", choices=["corner", "below", "none"], default="corner", help="期の表示: corner=ロゴの上 / below=ロゴの下")
    ap.add_argument("--minus", default="△")
    ap.add_argument("--theme", default="night", help="night / slate / signal / forest / mincho / pop / blueprint / mono")
    ap.add_argument("--scale", type=float, default=2)
    a = ap.parse_args()
    with open(a.csv, encoding="utf-8-sig", newline="") as f:
        rows = [{k: (r.get(k) or "").strip() for k in KEYS} for r in csv.DictReader(f) if (r.get("企業") or "").strip()]

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
            page.evaluate("([r,o]) => window.__render(r,o)", [rows, {"title": a.title, "period": a.period, "minus": a.minus, "theme": a.theme}])
            page.evaluate("document.fonts.ready.then(()=>1)")
            page.wait_for_timeout(400)
            page.locator("#canvas").screenshot(path=a.out)
            b.close()
    finally:
        srv.shutdown()
    print("画像を作りました:", a.out)


if __name__ == "__main__":
    main()
