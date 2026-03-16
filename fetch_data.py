"""
バフェット太郎 マーケットダッシュボード — データ取得スクリプト
yfinance でデータを取得し、docs/data.json に保存する。
財務省の国債金利情報CSVで日本国債利回り（10Y / 30Y）を補完取得する。
GitHub Actions から毎日自動実行される。
"""

import json
import sys
import re
from datetime import datetime, timezone
import yfinance as yf
import requests

# ── 取得対象シンボル ────────────────────────────────────────────
SYMBOLS = {
    "indices": [
        {"symbol": "^GSPC",    "name": "S&P 500"},
        {"symbol": "^IXIC",    "name": "NASDAQ"},
        {"symbol": "^DJI",     "name": "Dow Jones"},
        {"symbol": "^N225",    "name": "日経平均"},
        {"symbol": "NIY=F",    "name": "日経平均先物"},
    ],
    "bigtech": [
        {"symbol": "NVDA",  "name": "NVIDIA"},
        {"symbol": "MSFT",  "name": "Microsoft"},
        {"symbol": "AAPL",  "name": "Apple"},
        {"symbol": "META",  "name": "Meta"},
        {"symbol": "AMZN",  "name": "Amazon"},
        {"symbol": "GOOGL", "name": "Alphabet"},
        {"symbol": "TSLA",  "name": "Tesla"},
    ],
    "rates": [
        # 注: 米2年債（^IRX=3ヶ月, yfinanceに2年債シンボルなし → fetch_fred_us2y()で取得）
        {"symbol": "^TNX", "name": "米10年債利回り", "tag": "US10Y"},
        {"symbol": "^TYX", "name": "米30年債利回り", "tag": "US30Y"},
    ],
    "commodities": [
        {"symbol": "GC=F", "name": "金スポット",     "icon": "🥇"},
        {"symbol": "SI=F", "name": "銀スポット",     "icon": "🥈"},
        {"symbol": "PL=F", "name": "プラチナ",       "icon": "💎"},
        {"symbol": "CL=F", "name": "WTI原油",        "icon": "🛢"},
    ],
    "fx": [
        {"symbol": "DX=F",     "name": "ドル指数（DXY）", "icon": "$"},
        {"symbol": "USDJPY=X", "name": "ドル円",           "icon": "¥"},
        {"symbol": "EURJPY=X", "name": "ユーロ円",         "icon": "€"},
        {"symbol": "CHFJPY=X", "name": "スイスフラン円",   "icon": "₣"},
    ],
    "crypto": [
        {"symbol": "BTC-USD", "name": "Bitcoin",  "icon": "₿"},
        {"symbol": "ETH-USD", "name": "Ethereum", "icon": "Ξ"},
    ],
    "vix": [
        {"symbol": "^VIX", "name": "VIX 恐怖指数"},
    ],
}

HISTORY_PERIOD = "1mo"
HISTORY_INTERVAL = "1d"


def fetch_quote(ticker_obj, meta: dict) -> dict:
    """1銘柄分のデータを取得してdictで返す"""
    try:
        info = ticker_obj.fast_info
        hist = ticker_obj.history(period=HISTORY_PERIOD, interval=HISTORY_INTERVAL, auto_adjust=True)

        price      = float(info.last_price)        if info.last_price      else None
        prev_close = float(info.previous_close)    if info.previous_close  else None
        high_52w   = float(info.year_high)         if info.year_high       else None
        low_52w    = float(info.year_low)          if info.year_low        else None

        # fast_info が None を返す銘柄（先物・一部指数など）はヒストリーから補完
        if price is None and not hist.empty:
            closes_valid = hist["Close"].dropna()
            if not closes_valid.empty:
                price = float(closes_valid.iloc[-1])
        if prev_close is None and not hist.empty:
            closes_valid = hist["Close"].dropna()
            if len(closes_valid) >= 2:
                prev_close = float(closes_valid.iloc[-2])

        # 騰落率
        change_pct = None
        if price and prev_close and prev_close != 0:
            change_pct = round((price - prev_close) / abs(prev_close) * 100, 3)

        # 高値比
        from_high = None
        if price and high_52w and high_52w != 0:
            from_high = round((price - high_52w) / high_52w * 100, 2)

        # 履歴データ（グラフ用）
        dates  = [d.strftime("%m/%d") for d in hist.index]
        closes = [round(float(v), 4) if v == v else None for v in hist["Close"]]

        return {
            **meta,
            "price":      round(price, 4) if price else None,
            "prev_close": round(prev_close, 4) if prev_close else None,
            "high_52w":   round(high_52w, 4) if high_52w else None,
            "low_52w":    round(low_52w, 4) if low_52w else None,
            "change_pct": change_pct,
            "from_high":  from_high,
            "dates":      dates,
            "closes":     closes,
            "ok":         True,
        }

    except Exception as e:
        print(f"  ⚠ {meta['symbol']}: {e}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None,
                "dates": [], "closes": []}


def fetch_group(group_key: str) -> list:
    items = SYMBOLS[group_key]
    syms  = [m["symbol"] for m in items]
    print(f"  Fetching {group_key}: {syms}")

    # まとめてダウンロード（高速）
    tickers = yf.Tickers(" ".join(syms))

    results = []
    for meta in items:
        t = tickers.tickers[meta["symbol"]]
        results.append(fetch_quote(t, meta))
    return results


def fetch_fred_us2y() -> dict:
    """
    FRED API（APIキー不要）から米2年債利回り（DGS2）を取得する。
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2
    日次データ（営業日のみ）。
    """
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2"
    print(f"  Fetching US 2Y yield from FRED ...")
    meta = {"symbol": "US2Y", "name": "米2年債利回り", "tag": "US2Y"}
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        # ヘッダー行をスキップ。欠損値は "." で表現されるため除外
        data_rows = []
        for l in lines[1:]:
            cols = l.split(",")
            if len(cols) < 2:
                continue
            if cols[1].strip() == ".":  # 欠損
                continue
            data_rows.append(l)

        recent = data_rows[-30:]
        dates, closes = [], []
        for row in recent:
            cols = row.split(",")
            date_raw = cols[0].strip()   # YYYY-MM-DD
            val_str  = cols[1].strip()
            try:
                val = round(float(val_str), 4)
                mm_dd = date_raw[5:7] + "/" + date_raw[8:10]
                closes.append(val)
                dates.append(mm_dd)
            except ValueError:
                pass

        return _build_bond_result(
            symbol="US2Y", name="米2年債利回り", tag="US2Y",
            dates=dates, closes=closes
        )
    except Exception as e:
        print(f"  ⚠ US2Y fetch error: {e}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}


def fetch_ff_rate() -> dict:
    """
    FRED API（APIキー不要モード）からFFレートの実効値を取得する。
    https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF
    """
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    print(f"  Fetching FF rate from FRED ...")
    meta = {"symbol": "FEDFUNDS", "name": "FFレート（実効値）", "tag": "FEDFUNDS"}
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        data_rows = [l for l in lines[1:] if l.strip() and "." in l]
        recent = data_rows[-30:]

        dates, closes = [], []
        for row in recent:
            cols = row.split(",")
            if len(cols) < 2:
                continue
            date_raw = cols[0].strip()  # YYYY-MM-DD
            val_str  = cols[1].strip()
            try:
                val = round(float(val_str), 4)
                mm_dd = date_raw[5:7] + "/" + date_raw[8:10]
                closes.append(val)
                dates.append(mm_dd)
            except ValueError:
                pass

        return _build_bond_result(
            symbol="FEDFUNDS", name="FFレート（実効値）", tag="FEDFUNDS",
            dates=dates, closes=closes
        )
    except Exception as e:
        print(f"  ⚠ FF rate fetch error: {e}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}


def fetch_sp500_per() -> dict:
    """
    multpl.com から S&P500 の実績PER（Trailing P/E）を取得する。
    https://www.multpl.com/s-p-500-pe-ratio/table/by-month
    """
    url = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
    print(f"  Fetching S&P500 PER from multpl.com ...")
    meta = {"symbol": "SP500PE", "name": "S&P500 PER（実績）", "tag": "SP500_PE"}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MarketDashboard/1.0)"}
        r = requests.get(url, timeout=15, headers=headers)
        r.raise_for_status()

        # テーブルから月次PERを抽出
        # 行フォーマット例: <tr><td>Mar 1, 2026</td><td>26.15</td></tr>
        rows = re.findall(
            r'<tr>\s*<td[^>]*>([\w ,]+\d{4})</td>\s*<td[^>]*>([\d.]+)</td>',
            r.text
        )

        closes, dates = [], []
        for date_str, val_str in rows[:30]:
            try:
                val = round(float(val_str), 2)
                # "Mar 1, 2026" → "03/01" のような短縮形に変換
                from datetime import datetime as _dt
                d = _dt.strptime(date_str.strip(), "%b %d, %Y")
                closes.append(val)
                dates.append(d.strftime("%m/%d"))
            except (ValueError, TypeError):
                pass

        # 最新が先頭なので逆順に
        closes = closes[::-1]
        dates  = dates[::-1]

        return _build_bond_result(
            symbol="SP500PE", name="S&P500 PER（実績）", tag="SP500_PE",
            dates=dates, closes=closes
        )
    except Exception as e:
        print(f"  ⚠ S&P500 PER fetch error: {e}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}


def fetch_mof_jgb_yields() -> list:
    """
    財務省の国債金利情報CSV（日次更新）から日本10年債・30年債の利回りを取得する。
    https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv
    CSV列: 日付,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y
    """
    url = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
    print(f"  Fetching JGB yields from MOF CSV ...")

    results = []
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()

        # CSV は Shift_JIS エンコーディング
        text = r.content.decode("shift_jis", errors="replace")
        lines = text.strip().splitlines()

        # ヘッダー行・コメント行をスキップし、データ行を抽出
        data_rows = []
        for line in lines:
            # データ行は和暦日付で始まる: R7.3.14 や H30.1.5 など
            if re.match(r'^[SHMRT]\d+\.', line):
                data_rows.append(line)

        if not data_rows:
            print("  ⚠ MOF CSV: データ行が見つかりません", file=sys.stderr)
            return _empty_jgb_results()

        # 直近30日分（末尾から最大30行）
        recent = data_rows[-30:]

        dates_2y,  closes_2y  = [], []
        dates_10y, closes_10y = [], []
        dates_30y, closes_30y = [], []

        for row_str in recent:
            cols = row_str.split(",")
            if len(cols) < 15:
                continue

            # 日付を和暦→MM/DD に変換
            raw_date = cols[0].strip()
            m = re.match(r'[SHMRT](\d+)\.(\d+)\.(\d+)', raw_date)
            if not m:
                continue
            mm = m.group(2).zfill(2)
            dd = m.group(3).zfill(2)
            date_str = f"{mm}/{dd}"

            # 2Y = cols[2], 10Y = cols[10], 30Y = cols[14]
            val_2y  = cols[2].strip()  if len(cols) > 2  else "-"
            val_10y = cols[10].strip() if len(cols) > 10 else "-"
            val_30y = cols[14].strip() if len(cols) > 14 else "-"

            if val_2y and val_2y != "-":
                try:
                    closes_2y.append(round(float(val_2y), 4))
                    dates_2y.append(date_str)
                except ValueError:
                    pass

            if val_10y and val_10y != "-":
                try:
                    closes_10y.append(round(float(val_10y), 4))
                    dates_10y.append(date_str)
                except ValueError:
                    pass

            if val_30y and val_30y != "-":
                try:
                    closes_30y.append(round(float(val_30y), 4))
                    dates_30y.append(date_str)
                except ValueError:
                    pass

        # 2年債
        results.append(_build_bond_result(
            symbol="JP2Y", name="日本2年債利回り", tag="JP2Y_YIELD",
            dates=dates_2y, closes=closes_2y
        ))

        # 10年債
        results.append(_build_bond_result(
            symbol="JP10Y", name="日本10年債利回り", tag="JP10Y_YIELD",
            dates=dates_10y, closes=closes_10y
        ))

        # 30年債
        results.append(_build_bond_result(
            symbol="JP30Y", name="日本30年債利回り", tag="JP30Y_YIELD",
            dates=dates_30y, closes=closes_30y
        ))

        return results

    except Exception as e:
        print(f"  ⚠ MOF CSV fetch error: {e}", file=sys.stderr)
        return _empty_jgb_results()


def _build_bond_result(symbol, name, tag, dates, closes):
    meta = {"symbol": symbol, "name": name, "tag": tag}
    if not closes or len(closes) < 1:
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}

    price = closes[-1]
    prev_close = closes[-2] if len(closes) >= 2 else None

    change_pct = None
    if price is not None and prev_close is not None and prev_close != 0:
        change_pct = round((price - prev_close) / abs(prev_close) * 100, 3)

    high_val = max(closes)
    low_val = min(closes)
    from_high = round((price - high_val) / high_val * 100, 2) if high_val != 0 else None

    return {
        **meta,
        "price": round(price, 4),
        "prev_close": round(prev_close, 4) if prev_close else None,
        "high_52w": high_val,
        "low_52w": low_val,
        "change_pct": change_pct,
        "from_high": from_high,
        "dates": dates,
        "closes": closes,
        "ok": True,
    }


def _empty_jgb_results():
    return [
        {"symbol": "JP2Y",  "name": "日本2年債利回り",  "tag": "JP2Y_YIELD",
         "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []},
        {"symbol": "JP10Y", "name": "日本10年債利回り", "tag": "JP10Y_YIELD",
         "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []},
        {"symbol": "JP30Y", "name": "日本30年債利回り", "tag": "JP30Y_YIELD",
         "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []},
    ]


def main():
    print("=== データ取得開始 ===")
    output = {
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "updated_jst": datetime.now(timezone.utc).astimezone(
            __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
        ).strftime("%Y年%m月%d日 %H:%M JST"),
    }

    for group_key in SYMBOLS:
        print(f"\n[{group_key}]")
        results = fetch_group(group_key)
        # vix は単体なのでリストの最初の要素を直接入れる
        output[group_key] = results[0] if group_key == "vix" else results

    # 財務省CSV: 日本国債利回り → rates に追加（2Y / 10Y / 30Y）
    print("\n[mof_jgb_yields]")
    jgb_results = fetch_mof_jgb_yields()
    for result in jgb_results:
        output["rates"].append(result)
        status = "✓" if result["ok"] else "✗"
        print(f"  {status} {result['symbol']}: {result.get('price')}")

    # FRED: 米2年債利回り → rates に追加
    print("\n[fred_us2y]")
    us2y = fetch_fred_us2y()
    output["rates"].append(us2y)
    status = "✓" if us2y["ok"] else "✗"
    print(f"  {status} {us2y['symbol']}: {us2y.get('price')}")

    # FRED: FFレート → rates に追加
    print("\n[ff_rate]")
    ff = fetch_ff_rate()
    output["rates"].append(ff)
    status = "✓" if ff["ok"] else "✗"
    print(f"  {status} {ff['symbol']}: {ff.get('price')}")

    # multpl.com: S&P500 PER → output["sp500_pe"] に追加
    print("\n[sp500_per]")
    pe = fetch_sp500_per()
    output["sp500_pe"] = pe
    status = "✓" if pe["ok"] else "✗"
    print(f"  {status} {pe['symbol']}: {pe.get('price')}")

    # docs/data.json に保存
    out_path = "docs/data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存完了: {out_path}")
    print(f"   更新時刻: {output['updated_jst']}")


if __name__ == "__main__":
    main()
