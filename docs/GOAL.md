# Git Knowledge Bucket — Goal Document

設計の詳細は [DESIGN.md](DESIGN.md) を参照。
この文書は `goal_driven_parallel_dev` の最高優先度ゴール。

---

## 現在の状況

Phase 1〜7完了。445テスト合格、lint clean。
Phase 8は文書数30万件/repo 5GBまで着手しない。
次は Phase 9（実運用と統合品質）を進行中。

---

## Phase 6: 運用品質と堅牢性 ✅

### 6.1 エラーハンドリング強化 ✅

- [x] `parsers/paper.py`: arXiv API / CrossRef API のネットワークエラー時の graceful handling
- [x] `parsers/repo.py`: `gh api` 失敗時のエラーメッセージ改善
- [x] `parsers/pdf.py`: 破損PDFのハンドリング
- [x] `ingest.py`: 破損Markdownのスキップと警告出力
- [x] `index.py`: SQLiteロック競合時のリトライ

### 6.2 大量インポート最適化 ✅

- [x] `ingest.py` のバッチトランザクション最適化
- [x] `graph.py` のバッチグラフ構築
- [x] `index.py` のbulk insert

### 6.3 インデックスリカバリ ✅

- [x] `kb index --verify` コマンド
- [x] `kb index --repair` コマンド
- [x] stale HEADの自動検出と full rebuild フォールバック

---

## Phase 7: 機能拡張 ✅

### 7.1 動画入力対応 ✅

- [x] `parsers/video.py`: YouTube URLからのメタデータ取得
- [x] `kb add-video <url>` CLIコマンド
- [x] `source_type: video` の追加
- [x] `prompts/analyzer_video.md` の作成

### 7.2 Embedding-based vector search ✅

- [x] `src/kb/embeddings.py`: OpenAI / ローカルモデルのembedding生成
- [x] `kb vectorize --engine embedding` でembeddingベースのベクトルインデックス構築
- [x] `kb search --semantic` でembedding vectorを使った検索（TF-IDFフォールバック付き）
- [x] `.kb/embeddings.npz` 保存

### 7.3 S3 / R2 Rawデータ保存 ✅

- [x] `src/kb/storage.py`: S3互換ストレージへのアップロード
- [x] `kb add --save-raw` でS3/R2に保存、`raw_ref` 記録
- [x] `kb raw <doc_id>` でrawデータの取得・表示

---

## Phase 8: 大規模化（着手条件: 30万文書 / repo 5GB）

> 以下は文書数が30万件を超えるか、repo sizeが5GBを超えるまで着手しない。

- [ ] IDハッシュによる16 shard repo分割
- [ ] バッチ再分析パイプライン（既存文書の再分析を一括実行）
- [ ] 外部ツール連携（Obsidianプラグイン、モバイル入力UI）

---

## Phase 9: 実運用と統合品質

### 9.1 Web UIの実用性向上

- [ ] 検索結果のページネーション: 1ページ20件、`/recent?page=2` でページ遷移。200件登録時に全ページが正常表示されること
- [ ] 文書一覧のソート: `?sort=date|importance|type` パラメータで並び替え。各ソートのテスト追加
- [ ] 文書編集機能: `/doc/<id>/edit` ページでメモ・評価を追記しPOSTでfront matter更新。テストで編集→保存→再表示を確認
- [ ] ダークモード: `prefers-color-scheme: media` またはトグルボタンで切替。CSS変数で色管理

**完了基準**: `kb serve` 起動後、200件ダミー文書を登録して全ページ（検索、一覧、詳細、編集）が正常動作すること。各機能のテスト追加。

### 9.2 AI分析パイプライン統合

- [ ] `kb ingest --analyze` でLLM APIを呼び出し、分析結果（concepts, importance, summary）をfront matterに書き込む。`src/kb/analyzer.py` の `parse_analysis_response` を利用
- [ ] APIキーは環境変数 `KB_LLM_API_KEY` から読み込み。未設定時は警告してスキップ
- [ ] レート制限対応: API呼び出し間に0.5sスリープ、429エラー時にexponential backoff（最大3リトライ）
- [ ] 分析失敗時はプレーンなfront matterで保存。`kb analyze --retry-failed` で `analysis.confidence` がない文書だけ再分析

**完了基準**: `KB_LLM_API_KEY=xxx kb ingest --analyze` でAI分析済みレコードが生成され、front matterにconcepts/summary/importanceが記録されること。APIモックテストで検証。

### 9.3 設計書の整合性更新

- [ ] `docs/DESIGN.md` にvideo parser、embeddings、storage、index verify/repair、content_hashの記載があること
- [ ] `docs/DESIGN.md` のCLI一覧（section 15）に全コマンド（add-video, raw, index --verify/--repair含む）が列挙されていること
- [ ] README.mdのCLIコマンド一覧が現在の実装と一致していること

**完了基準**: `grep -c "kb add-video\|kb raw\|index --verify\|embeddings.py\|storage.py\|parsers/video.py" docs/DESIGN.md` で全項目がヒットすること。
