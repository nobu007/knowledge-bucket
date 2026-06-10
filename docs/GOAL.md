# Git Knowledge Bucket — Goal Document

設計の詳細は [DESIGN.md](DESIGN.md) を参照。
この文書は `goal_driven_parallel_dev` の最高優先度ゴール。

---

## 現在の状況

Phase 1〜5の基本実装は完了。348テスト合格、lint clean。
現在は Phase 6（運用品質向上）を進行中。

---

## Phase 6: 運用品質と堅牢性

### 6.1 エラーハンドリング強化

- [x] `parsers/paper.py`: arXiv API / CrossRef API のネットワークエラー時の graceful handling（タイムアウト、HTTPエラー、無効レスポンス）
- [x] `parsers/repo.py`: `gh api` 失敗時のエラーメッセージ改善（gh 未インストール、認証エラー、レート制限）
- [x] `parsers/pdf.py`: pypdf が例外を投げる破損PDFのハンドリング
- [x] `ingest.py`: inbox内の破損Markdown（不正YAML front matter）のスキップと警告出力
- [x] `index.py`: SQLiteロック競合時のリトライ（`sqlite3.OperationalError: database is locked`）

**完了基準**: 各モジュールのエラーパステストを追加。全テスト合格。

### 6.2 大量インポート最適化

- [ ] `ingest.py` のバッチモード: inbox内100件以上のファイルを一括処理時のSQLiteトランザクション最適化
- [ ] `graph.py` のバッチグラフ構築: 1000件以上の新規文書を一括でグラフ構築するバッチモード
- [ ] `index.py` のbulk insert: FTS5へのINSERTをトランザクション内でバッチ化

**完了基準**: 1000件のダミー文書でのベンチマーク。現状より2倍以上高速化。

### 6.3 インデックスリカバリ

- [ ] `kb index --verify` コマンド: FTSインデックスと実際のMarkdownファイルの整合性チェック
- [ ] `kb index --repair` コマンド: 欠損エントリの再構築、ゴミエントリの削除
- [ ] `kv_meta` の `last_indexed_commit` が存在しないコミットを指している場合の自動検出と full rebuild フォールバック

**完了基準**: 意図的にインデックスを破損させて `--verify` で検出、`--repair` で修復できること。

---

## Phase 7: 機能拡張

### 7.1 動画入力対応

- [ ] `parsers/video.py`: YouTube URLからメタデータ（タイトル、概要、チャンネル、長さ）を取得
- [ ] `kb add-video <url>` CLIコマンド
- [ ] 新しい `source_type: video` の追加
- [ ] `prompts/analyzer_video.md` の作成

**完了基準**: YouTube URLを渡してレコードが生成されること。テストはAPIをモック化。

### 7.2 Embedding-based vector search

- [ ] `src/kb/embeddings.py`: OpenAI / ローカルモデルのembedding生成インターフェース
- [ ] `kb vectorize --engine embedding` でembeddingベースのベクトルインデックス構築
- [ ] `kb search --semantic` でembedding vectorを使った検索（TF-IDFフォールバック付き）
- [ ] embeddingの保存先: `.kb/embeddings.npz`

**完了基準**: embeddingベースのセマンティック検索がTF-IDFより高い関連性を示すこと（手動確認）。

### 7.3 S3 / R2 Rawデータ保存

- [x] `src/kb/storage.py`: S3互換ストレージへのアップロードインターフェース
- [x] `kb add <url> --save-raw` でHTML/PDF本文をS3/R2に保存、front matterに `raw_ref` を記録
- [x] `kb raw <doc_id>` でrawデータの取得・表示

**完了基準**: `--save-raw` でファイルがS3/R2にアップロードされ、`raw_ref` がfront matterに記録されること。

---

## Phase 8: 大規模化（必要になったら）

> 以下は文書数が30万件を超えるか、repo sizeが5GBを超えるまで着手しない。

- [ ] IDハッシュによる16 shard repo分割
- [ ] バッチ再分析パイプライン（既存文書の再分析を一括実行）
- [ ] 外部ツール連携（Obsidianプラグイン、モバイル入力UI）
