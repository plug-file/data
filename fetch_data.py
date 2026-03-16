"""
バフェット太郎 マーケットダッシュボード — データ取得スクリプト
yfinance でデータを取得し、docs/data.json に保存する。
Twelvedata API で日本国債利回り（JP10Y / JP30Y）を補完取得する。
GitHub Actions から毎日自動実行される。
"""

import json
import os
import sys
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
        {"symbol": "^TNX", "name": "米10年債利回り", "tag": "US10Y"},
        {"symbol": "^TYX", "name": "米30年債利回り", "tag": "US30Y"},
        {"symbol": "JGBS=F", "name": "日本10年債先物",   "tag": "JP10Y"},
        {"symbol": "JGBL=F", "name": "日本30年債先物",   "tag": "JP30Y"},
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


def fetch_twelvedata_bond(symbol: str, name: str, tag: str) -> dict:
    """Twelvedata API で日本国債利回りを取得する"""
    api_key = os.environ.get("TWELVEDATA_API_KEY", "")
    meta = {"symbol": symbol, "name": name, "tag": tag}
    if not api_key:
        print(f"  ⚠ TWELVEDATA_API_KEY が未設定: {symbol}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}

    try:
        # 直近1ヶ月の日足データを取得
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": symbol,
            "interval": "1day",
            "outputsize": 30,
            "apikey": api_key,
        }
        print(f"  Fetching {symbol} from Twelvedata ...")
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        if data.get("status") == "error":
            print(f"  ⚠ Twelvedata error for {symbol}: {data.get('message','')}", file=sys.stderr)
            return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}

        values = data.get("values", [])
        if not values:
            return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}

        # values は新しい順 → 古い順に反転
        values = list(reversed(values))

        closes = []
        dates = []
        for v in values:
            c = float(v["close"])
            closes.append(round(c, 4))
            # "2026-03-14" → "03/14"
            dt = v["datetime"]
            dates.append(dt[5:7] + "/" + dt[8:10])

        price = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else None

        change_pct = None
        if price is not None and prev_close is not None and prev_close != 0:
            change_pct = round((price - prev_close) / abs(prev_close) * 100, 3)

        high_val = max(closes) if closes else None
        low_val = min(closes) if closes else None
        from_high = None
        if price and high_val and high_val != 0:
            from_high = round((price - high_val) / high_val * 100, 2)

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

    except Exception as e:
        print(f"  ⚠ Twelvedata {symbol}: {e}", file=sys.stderr)
        return {**meta, "ok": False, "price": None, "change_pct": None, "dates": [], "closes": []}


# Twelvedata で取得する日本国債利回り
TWELVEDATA_BONDS = [
    {"symbol": "JP10Y", "name": "日本10年債利回り", "tag": "JP10Y_YIELD"},
    {"symbol": "JP30Y", "name": "日本30年債利回り", "tag": "JP30Y_YIELD"},
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

    # Twelvedata: 日本国債利回り → rates に追加
    print("\n[twelvedata_bonds]")
    for bond in TWELVEDATA_BONDS:
        result = fetch_twelvedata_bond(bond["symbol"], bond["name"], bond["tag"])
        output["rates"].append(result)
        status = "✓" if result["ok"] else "✗"
        print(f"  {status} {bond['symbol']}: {result.get('price')}")

    # docs/data.json に保存
    out_path = "docs/data.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 保存完了: {out_path}")
    print(f"   更新時刻: {output['updated_jst']}")


if __name__ == "__main__":
    main()
