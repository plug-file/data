# バフェット太郎 マーケットダッシュボード

yfinance で市場データを取得し、GitHub Pages で公開する週次チェックダッシュボードです。

---

## 🗂 ファイル構成

```
buffett-dashboard/
├── fetch_data.py               # データ取得スクリプト（yfinance）
├── requirements.txt            # Python依存ライブラリ
├── .github/
│   └── workflows/
│       └── update.yml          # GitHub Actions（自動実行）
└── docs/
    ├── index.html              # ダッシュボード本体
    └── data.json               # 取得データ（自動生成）
```

---

## 🚀 セットアップ手順

### 1. リポジトリを作成

GitHub で新しいリポジトリを作成し、このフォルダの中身をすべてプッシュします。

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git push -u origin main
```

### 2. GitHub Pages を有効化

1. リポジトリの **Settings → Pages** を開く
2. **Source** を `Deploy from a branch` に設定
3. **Branch** を `main`、フォルダを `/docs` に設定して **Save**

数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で公開されます。

### 3. GitHub Actions の確認

`.github/workflows/update.yml` が自動的に動きます。
手動で即時実行したい場合は **Actions → Update Market Data → Run workflow** をクリック。

---

## ⏰ 自動更新スケジュール

| タイミング | 時刻 | 用途 |
|---|---|---|
| 月〜金 | 22:00 UTC（翌朝7:00 JST） | 米国市場クローズ後のデータ取得 |
| 毎週土曜 | 01:00 UTC（10:00 JST） | 週次チェック用 |

---

## 🖥 ローカルで動かす場合

```bash
# 依存ライブラリのインストール
pip install -r requirements.txt

# データ取得
python fetch_data.py

# ローカルサーバーで確認（file:// では fetch が動かないため必須）
cd docs
python -m http.server 8000
# → http://localhost:8000 を開く
```

---

## 📊 取得データ一覧

| カテゴリ | 銘柄 |
|---|---|
| 株式インデックス | ^GSPC (S&P500), ^IXIC (NASDAQ), ^DJI (Dow), ^N225 (日経平均), NIY=F (日経先物) |
| ビッグテック | NVDA, MSFT, AAPL, META, AMZN, GOOGL, TSLA |
| 金利 | ^TNX (米10年), ^TYX (米30年), JPNB-F (日本10年) |
| コモディティ | GC=F (金), SI=F (銀), PL=F (プラチナ), CL=F (WTI) |
| 為替 | DX-Y.NYB (DXY), USDJPY=X, EURJPY=X, CHFJPY=X |
| 暗号資産 | BTC-USD, ETH-USD |

マクロ指標（CPI, PCE, NFP等）は月次発表後に `docs/index.html` 内の `MACRO_DATA` を手動更新してください。

---

## 🔧 カスタマイズ

- **銘柄の追加・変更**: `fetch_data.py` の `SYMBOLS` を編集
- **更新頻度の変更**: `.github/workflows/update.yml` の `cron` を編集
- **マクロ指標の更新**: `docs/index.html` の `MACRO_DATA` 配列を直接編集
