"""
マーケットダッシュボード — データ取得スクリプト
yfinance でデータを取得し、docs/data.json に保存する。
財務省の国債金利情報CSVで日本国債利回り（10Y / 30Y）を補完取得する。
GitHub Actions から毎日自動実行される。
"""

import json
import sys
import re
import os
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


def fetch_moving_averages() -> dict:
    """
    S&P500, NASDAQ, Dow Jones の50日/200日移動平均線データを取得する。
    yfinance で 1年分のヒストリーを取得し、移動平均を計算する。
    ミニチャート用に直近60日分の終値・50日線・200日線のデータも返す。
    """
    MA_TARGETS = [
        {"symbol": "^GSPC", "name": "S&P 500"},
        {"symbol": "^IXIC", "name": "NASDAQ"},
        {"symbol": "^DJI",  "name": "Dow Jones"},
    ]

    print("\n[moving_averages]")
    results = {}

    for target in MA_TARGETS:
        sym = target["symbol"]
        name = target["name"]
        print(f"  Fetching MA data: {name} ({sym}) ...", end=" ")
        try:
            ticker = yf.Ticker(sym)
            hist = ticker.history(period="1y", interval="1d", auto_adjust=True)

            if hist.empty or len(hist) < 50:
                print(f"✗ データ不足 ({len(hist)}行)")
                results[sym] = {"name": name, "ok": False}
                continue

            closes = hist["Close"].dropna()

            # 移動平均を計算
            ma50 = closes.rolling(window=50).mean()
            ma200 = closes.rolling(window=200).mean() if len(closes) >= 200 else None

            price = float(closes.iloc[-1])
            ma50_val = float(ma50.iloc[-1]) if ma50.iloc[-1] == ma50.iloc[-1] else None
            ma200_val = float(ma200.iloc[-1]) if (ma200 is not None and ma200.iloc[-1] == ma200.iloc[-1]) else None

            # 乖離率
            ma50_dev = round((price - ma50_val) / ma50_val * 100, 2) if ma50_val else None
            ma200_dev = round((price - ma200_val) / ma200_val * 100, 2) if ma200_val else None

            # ゴールデンクロス / デッドクロス判定
            cross = None
            if ma200 is not None and len(ma50) >= 2 and len(ma200) >= 2:
                prev_ma50 = float(ma50.iloc[-2]) if ma50.iloc[-2] == ma50.iloc[-2] else None
                prev_ma200 = float(ma200.iloc[-2]) if ma200.iloc[-2] == ma200.iloc[-2] else None
                if prev_ma50 and prev_ma200 and ma50_val and ma200_val:
                    if prev_ma50 <= prev_ma200 and ma50_val > ma200_val:
                        cross = "golden"
                    elif prev_ma50 >= prev_ma200 and ma50_val < ma200_val:
                        cross = "dead"

            # 52週高値からの下落率（直近1年の高値から）
            high_52w = float(closes.max())
            from_high = round((price - high_52w) / high_52w * 100, 2) if high_52w else None

            # ミニチャート用: 直近60営業日分のデータ
            chart_len = min(60, len(closes))
            chart_closes = [round(float(v), 2) for v in closes.iloc[-chart_len:]]
            chart_ma50 = []
            chart_ma200 = []
            chart_dates = [d.strftime("%m/%d") for d in closes.index[-chart_len:]]

            for i in range(len(closes) - chart_len, len(closes)):
                v50 = float(ma50.iloc[i]) if (i < len(ma50) and ma50.iloc[i] == ma50.iloc[i]) else None
                chart_ma50.append(round(v50, 2) if v50 else None)
                if ma200 is not None and i < len(ma200):
                    v200 = float(ma200.iloc[i]) if ma200.iloc[i] == ma200.iloc[i] else None
                    chart_ma200.append(round(v200, 2) if v200 else None)
                else:
                    chart_ma200.append(None)

            results[sym] = {
                "name": name,
                "ok": True,
                "price": round(price, 2),
                "ma50": round(ma50_val, 2) if ma50_val else None,
                "ma200": round(ma200_val, 2) if ma200_val else None,
                "ma50_dev": ma50_dev,
                "ma200_dev": ma200_dev,
                "cross": cross,
                "high_52w": round(high_52w, 2),
                "from_high": from_high,
                # ミニチャート用
                "chart_dates": chart_dates,
                "chart_closes": chart_closes,
                "chart_ma50": chart_ma50,
                "chart_ma200": chart_ma200,
            }
            above50 = "上回る" if (ma50_dev and ma50_dev > 0) else "下回る"
            above200 = "上回る" if (ma200_dev and ma200_dev > 0) else ("下回る" if ma200_dev else "N/A")
            print(f"✓ {price:.0f} (50日線{above50}, 200日線{above200})")

        except Exception as e:
            print(f"✗ エラー: {e}")
            results[sym] = {"name": name, "ok": False}

    return results


def fetch_shiller_cape() -> dict:
    """
    シラーPER（CAPE: Cyclically Adjusted PE Ratio）を取得する。
    バリュエーション判定で重視する指標。
    実績PERとは水準が大きく異なる（例: 実績PER 26倍 → シラーPER 37倍）。

    取得ソース（フォールバック付き）:
      1. multpl.com スクレイピング（最も高頻度に更新）
      2. posix4e Shiller API（JSON、週次更新）
      3. Yale大学公式Excelファイル（月次、最も権威あるソース）
    """
    print(f"  Fetching Shiller CAPE (P/E 10) ...")
    meta = {"symbol": "SHILLER_CAPE", "name": "シラーPER（CAPE）", "tag": "SHILLER_CAPE"}

    # --- 方法1: multpl.com ---
    try:
        url = "https://www.multpl.com/shiller-pe"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MarketDashboard/1.0)"}
        r = requests.get(url, timeout=15, headers=headers)
        r.raise_for_status()

        # "Current Shiller PE Ratio is XX.XX" パターンを探す
        match = re.search(r'Current\s+Shiller\s+PE\s+Ratio.*?<b[^>]*>([\d.]+)', r.text, re.IGNORECASE | re.DOTALL)
        if not match:
            # 別パターン: id="current" 内の数値
            match = re.search(r'id="current"[^>]*>[\s\S]*?([\d]+\.[\d]+)', r.text)
        if match:
            cape = round(float(match.group(1)), 2)
            print(f"    ✓ multpl.com: {cape}")
            return {**meta, "ok": True, "price": cape}
        else:
            print(f"    ⚠ multpl.com: パース失敗")
    except Exception as e:
        print(f"    ⚠ multpl.com: {e}")

    # --- 方法2: posix4e Shiller API ---
    try:
        url = "https://posix4e.github.io/shiller_wrapper_data/data/latest.json"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        cape = round(data["stock_market"]["cape"], 2)
        print(f"    ✓ Shiller API: {cape}")
        return {**meta, "ok": True, "price": cape}
    except Exception as e:
        print(f"    ⚠ Shiller API: {e}")

    # --- 方法3: multpl.com テーブルページ（月次データ） ---
    try:
        url = "https://www.multpl.com/shiller-pe/table/by-month"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MarketDashboard/1.0)"}
        r = requests.get(url, timeout=15, headers=headers)
        r.raise_for_status()
        rows = re.findall(
            r'<tr>\s*<td[^>]*>([\w ,]+\d{4})</td>\s*<td[^>]*>([\d.]+)</td>',
            r.text
        )
        if rows:
            cape = round(float(rows[0][1]), 2)
            print(f"    ✓ multpl.com table: {cape}")
            return {**meta, "ok": True, "price": cape}
    except Exception as e:
        print(f"    ⚠ multpl.com table: {e}")

    print(f"    ✗ 全ソース失敗")
    return {**meta, "ok": False, "price": None}


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


# ── FRED API によるマクロ経済指標の自動取得 ────────────────────
# APIキーが設定されていれば FRED API を使用、なければ CSV フォールバック
#
# 取得する指標:
#   CPI (前年同月比), コアCPI, PCEデフレーター, 失業率,
#   非農業部門雇用者数, 平均時給, GDP成長率, JOLTS求人件数,
#   ミシガン大学消費者信頼感指数
#
# ISM製造業/サービス業PMIはFREDに直接データがないため、
# ISM製造業のみ FRED シリーズ "NAPM" (旧名だが同データ) で取得を試みる。
# サービス業は FRED に該当シリーズがないため手動更新のまま残す。

FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"

# FREDシリーズ定義: (シリーズID, 表示名, タグ, 単位の説明, 前年比計算するか)
FRED_MACRO_SERIES = [
    # --- 前年同月比を自前計算するもの（月次・水準値） ---
    {"series_id": "CPIAUCSL",   "name": "CPI（前年同月比）",       "tag": "CPI",       "yoy": True,  "note": ""},
    {"series_id": "CPILFESL",   "name": "コアCPI（前年同月比）",   "tag": "CORE_CPI",  "yoy": True,  "note": "食品・エネルギー除く"},
    {"series_id": "PCEPI",      "name": "PCEデフレーター（前年比）","tag": "PCE",       "yoy": True,  "note": "FRBが重視するインフレ指標"},
    # --- そのまま使うもの（%や水準値） ---
    {"series_id": "UNRATE",     "name": "失業率",                   "tag": "UNRATE",    "yoy": False, "note": ""},
    {"series_id": "PAYEMS",     "name": "非農業部門雇用者数",       "tag": "NFP",       "yoy": False, "note": "千人", "mom_diff": True},
    {"series_id": "CES0500000003","name":"平均時給（前年比）",      "tag": "WAGE",      "yoy": True,  "note": "ドル"},
    {"series_id": "UMCSENT",    "name": "ミシガン大消費者信頼感",   "tag": "UMCSENT",   "yoy": False, "note": ""},
    {"series_id": "JTSJOL",     "name": "JOLTS求人件数",            "tag": "JOLTS",     "yoy": False, "note": "千件"},
    # ISM PMIは2016年にFREDから削除済み → S&Pグローバル製造業/サービス業PMIで代替
    {"series_id": "MPMIUSMA",   "name": "製造業PMI（S&Pグローバル）","tag": "ISM_MFG",  "yoy": False, "note": "50が好不況の分かれ目"},
    {"series_id": "MPMIUSSA",   "name": "サービス業PMI（S&Pグローバル）","tag": "ISM_SVC","yoy": False,"note": "50が好不況の分かれ目"},
    # GDP成長率（実質GDP・年率）— 四半期データ
    {"series_id": "A191RL1Q225SBEA","name":"GDP成長率（実質・年率）","tag": "GDP",      "yoy": False, "note": "四半期"},
]


def fetch_fred_api(series_id: str, api_key: str, limit: int = 24) -> list:
    """
    FRED API から指定シリーズの直近 N 件の観測値を取得する。
    返り値: [{"date": "YYYY-MM-DD", "value": float}, ...]
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    try:
        r = requests.get(FRED_API_BASE, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        observations = data.get("observations", [])
        results = []
        for obs in reversed(observations):  # 古い順に並べ替え
            val_str = obs.get("value", ".")
            if val_str == ".":
                continue
            try:
                results.append({
                    "date": obs["date"],
                    "value": float(val_str),
                })
            except (ValueError, KeyError):
                continue
        return results
    except Exception as e:
        print(f"  ⚠ FRED API error ({series_id}): {e}", file=sys.stderr)
        return []


def fetch_fred_csv_fallback(series_id: str, limit: int = 24) -> list:
    """
    FRED API キーがない場合のフォールバック: CSV 直接ダウンロード。
    レート制限が緩いが、一部シリーズで使えない場合がある。
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        lines = r.text.strip().splitlines()
        results = []
        for line in lines[1:]:
            cols = line.split(",")
            if len(cols) < 2:
                continue
            val_str = cols[1].strip()
            if val_str == "." or not val_str:
                continue
            try:
                results.append({
                    "date": cols[0].strip(),
                    "value": float(val_str),
                })
            except ValueError:
                continue
        return results[-limit:]
    except Exception as e:
        print(f"  ⚠ FRED CSV fallback error ({series_id}): {e}", file=sys.stderr)
        return []


def compute_yoy(observations: list) -> tuple:
    """
    月次の水準データから前年同月比（%）を計算する。
    返り値: (最新の前年比%, 前回の前年比%, 最新の発表月 "YYYY-MM")
    """
    if len(observations) < 13:
        # 最低13カ月分ないと前年比が計算できない
        return None, None, None

    latest = observations[-1]
    prev = observations[-2] if len(observations) >= 2 else None

    # 12カ月前の値を探す
    latest_date = latest["date"]  # "YYYY-MM-DD"
    latest_ym = latest_date[:7]    # "YYYY-MM"

    # 12カ月前
    yr = int(latest_ym[:4])
    mo = int(latest_ym[5:7])
    yr_ago = yr - 1
    target_ym = f"{yr_ago}-{mo:02d}"

    val_12m_ago = None
    for obs in observations:
        if obs["date"][:7] == target_ym:
            val_12m_ago = obs["value"]
            break

    if val_12m_ago is None or val_12m_ago == 0:
        return None, None, latest_ym

    yoy = round((latest["value"] - val_12m_ago) / val_12m_ago * 100, 1)

    # 前回の前年比も計算（トレンド判断用）
    prev_yoy = None
    if prev:
        prev_ym = prev["date"][:7]
        p_yr = int(prev_ym[:4])
        p_mo = int(prev_ym[5:7])
        p_target = f"{p_yr - 1}-{p_mo:02d}"
        for obs in observations:
            if obs["date"][:7] == p_target:
                if obs["value"] != 0:
                    prev_yoy = round((prev["value"] - obs["value"]) / obs["value"] * 100, 1)
                break

    return yoy, prev_yoy, latest_ym


def fetch_all_macro(api_key: str | None) -> dict:
    """
    全マクロ指標を取得して辞書で返す。
    api_key が None の場合は CSV フォールバックを使用。
    """
    print("\n[macro] FRED マクロ経済指標")
    if api_key:
        print(f"  FRED API キー: 設定済み（{api_key[:4]}...）")
    else:
        print("  FRED API キー: 未設定 → CSV フォールバック")

    macro = {}

    for series_def in FRED_MACRO_SERIES:
        sid = series_def["series_id"]
        tag = series_def["tag"]
        name = series_def["name"]
        is_yoy = series_def.get("yoy", False)
        is_mom_diff = series_def.get("mom_diff", False)
        note = series_def.get("note", "")

        print(f"  Fetching {sid} ({name}) ...", end=" ")

        # データ取得（APIキーがあればAPI、なければCSV）
        if api_key:
            obs = fetch_fred_api(sid, api_key, limit=24)
        else:
            obs = fetch_fred_csv_fallback(sid, limit=24)

        if not obs:
            print("✗ データなし")
            macro[tag] = {
                "tag": tag, "name": name, "ok": False,
                "value": None, "prev_value": None,
                "date": None, "note": note, "trend": "neu",
            }
            continue

        latest = obs[-1]
        prev = obs[-2] if len(obs) >= 2 else None

        if is_yoy:
            # 前年同月比を計算
            val, prev_val, date_str = compute_yoy(obs)
            if val is not None:
                trend = "up" if (prev_val is not None and val > prev_val) else \
                        "dn" if (prev_val is not None and val < prev_val) else "neu"
                macro[tag] = {
                    "tag": tag, "name": name, "ok": True,
                    "value": val, "prev_value": prev_val,
                    "date": date_str, "note": note, "trend": trend,
                    "display": f"+{val}%" if val >= 0 else f"{val}%",
                }
                print(f"✓ {val}%")
            else:
                macro[tag] = {
                    "tag": tag, "name": name, "ok": False,
                    "value": None, "prev_value": None,
                    "date": None, "note": note, "trend": "neu",
                }
                print("✗ 前年比計算不可")

        elif is_mom_diff:
            # 前月差分（雇用者数の変化）
            val = latest["value"]
            prev_val = prev["value"] if prev else None
            diff = None
            if prev_val is not None:
                diff = round(val - prev_val, 1)
            date_str = latest["date"][:7]

            trend = "up" if (diff is not None and diff > 0) else \
                    "dn" if (diff is not None and diff < 0) else "neu"
            macro[tag] = {
                "tag": tag, "name": name, "ok": True,
                "value": round(val, 1),
                "change": diff,
                "prev_value": round(prev_val, 1) if prev_val else None,
                "date": date_str, "note": note, "trend": trend,
                "display": f"+{diff:.0f}K" if diff and diff >= 0 else f"{diff:.0f}K" if diff else "—",
            }
            print(f"✓ {val:.0f} (変化: {diff})")

        else:
            # そのまま使う（失業率、PMI、消費者信頼感など）
            val = round(latest["value"], 1)
            prev_val = round(prev["value"], 1) if prev else None
            date_str = latest["date"][:7]

            trend = "up" if (prev_val is not None and val > prev_val) else \
                    "dn" if (prev_val is not None and val < prev_val) else "neu"
            macro[tag] = {
                "tag": tag, "name": name, "ok": True,
                "value": val, "prev_value": prev_val,
                "date": date_str, "note": note, "trend": trend,
                "display": f"{val}",
            }
            print(f"✓ {val}")

    return macro


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

    # シラーPER（CAPE）→ output["shiller_cape"] に追加
    print("\n[shiller_cape]")
    cape = fetch_shiller_cape()
    output["shiller_cape"] = cape
    status = "✓" if cape["ok"] else "✗"
    print(f"  {status} {cape['symbol']}: {cape.get('price')}")

    # 移動平均線データ → output["moving_averages"] に追加
    ma_data = fetch_moving_averages()
    output["moving_averages"] = ma_data

    # FRED API: マクロ経済指標 → output["macro"] に追加
    fred_api_key = os.environ.get("FRED_API_KEY")
    macro = fetch_all_macro(fred_api_key)
    output["macro"] = macro

    # docs/data.json に保存
    out_path = "docs/data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存完了: {out_path}")
    print(f"   更新時刻: {output['updated_jst']}")


if __name__ == "__main__":
    main()
