# Git Knowledge Bucket

Web記事、GitHubリポジトリ、論文、メモ、PDF、動画をGitに蓄積し、FTS5検索・概念グラフ・Embeddingベクトル検索で再利用するローカル知識管理システム。

## インストール

```bash
# PyPI (when published)
pip install kb-tools

# GitHub から直接
pip install git+https://github.com/nobu007/knowledge-bucket.git

# 開発モード（リポジトリをcloneしている場合）
pip install -e ".[dev]"
```

## クイックスタート

```bash
# データ用ディレクトリを初期化
kb init ~/my-knowledge

# 文書を追加
cd ~/my-knowledge
kb add --title "test" --content "hello"

# インデックス構築
kb index --sync

# 検索
kb search "hello"
```

## CLIコマンド

### 基本

| コマンド | 説明 |
|---|---|
| `kb init` | ディレクトリ構造と設定ファイルを初期化 |
| `kb add <url-or-text>` | ドキュメントを追加（`--title`, `--source`, `--content`, `--type`, `--concepts`, `--save-raw`） |
| `kb ingest` | inbox内の未処理アイテムを処理（`--analyze` でLLM分析も同時実行） |
| `kb search "<query>"` | FTS5検索（`--limit`, `--semantic` でベクトル検索） |
| `kb show <doc_id>` | 文書メタデータと本文表示（`--full`） |
| `kb related <doc_id>` | 関連文書表示（`--limit`） |
| `kb sync` | Git同期パイプライン（pull → ingest → index → commit → push） |

### インデックス

| コマンド | 説明 |
|---|---|
| `kb index --sync` | 差分インデックス更新 |
| `kb index --rebuild` | 全件インデックス再構築 |
| `kb index --verify` | FTSインデックス整合性チェック |
| `kb index --repair` | 欠損エントリ再構築・ゴミエントリ削除 |
| `kb vectorize` | TF-IDF / Embedding vector index構築（`--engine tfidf\|embedding`） |

### 専用入力

| コマンド | 説明 |
|---|---|
| `kb add-paper <arxiv-or-doi>` | 論文をarXiv URL/ID、DOI、またはタイトルで追加 |
| `kb add-pdf <file>` | PDFをテキスト抽出して追加 |
| `kb add-repo <github-url>` | GitHubリポジトリをメタデータ取得して追加 |
| `kb add-video <url>` | YouTube等の動画をメタデータ取得して追加 |

### 分析・グラフ

| コマンド | 説明 |
|---|---|
| `kb analyze` | 文書のLLM分析（`--retry-failed` で未分析文書を再分析） |
| `kb graph build` | 概念グラフを構築 |
| `kb concepts suggest` | concept noteの昇格候補を提案・生成 |
| `kb health` | グラフ品質メトリクス表示（`--json`） |
| `kb concept <concept_id>` | 概念メタデータ・関連文書・共起概念表示 |

### エクスポート・ストレージ

| コマンド | 説明 |
|---|---|
| `kb export parquet` | グラフデータをParquetでエクスポート |
| `kb raw <doc_id>` | rawデータの取得・表示（S3/R2/ローカル） |
| `kb collections` | 仮想コレクション一覧表示 |

### Web UI

| コマンド | 説明 |
|---|---|
| `kb serve` | ローカルWeb UI起動（`--host`, `--port`, `--debug`） |

## LLM分析パイプライン

環境変数 `KB_LLM_API_KEY` を設定すると、LLM APIで文書の自動分析が可能。

```bash
KB_LLM_API_KEY=xxx kb ingest --analyze    # インポート時に分析
KB_LLM_API_KEY=xxx kb analyze --retry-failed  # 未分析文書を再分析
```

追加設定: `KB_LLM_BASE_URL`（APIベースURL）、`KB_LLM_MODEL`（モデル名）。

## テスト

```bash
python -m pytest tests/ -v
```
