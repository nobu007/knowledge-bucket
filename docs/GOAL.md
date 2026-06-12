# Git Knowledge Bucket — Goal Document

設計の詳細は [DESIGN.md](DESIGN.md) を参照。
この文書は `goal_driven_parallel_dev` の最高優先度ゴール。

---

## 現在の状況

Phase 1〜11完了。482テスト合格、lint clean。
Phase 8は文書数30万件/repo 5GBまで着手しない。
11.3（push）のみ残り。

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

## Phase 10: ツールとデータの分離 ✅

現在の `knowledge-bucket/` はツール（`src/kb/`）とデータ（`records/`、`config/`）が同一リポジトリにある。これを分離し、ツールをpip install可能なパッケージにする。

### 10.1 プロンプトのパッケージ内包 ✅

- [x] `prompts/` を `src/kb/prompts/` に移動し、パッケージデータとして配布可能にする
- [x] `pyproject.toml` に `[tool.setuptools.package-data]` で `prompts/*.md` を含める
- [x] `analyzer.py` の `_PROMPTS_DIR` を `importlib.resources` ベースに変更（pip install先でも動くように）
- [x] テスト: `pip install .` → 別ディレクトリで `kb init /tmp/test-kb && kb analyze` がプロンプトを読み込めること

**完了基準**: `pip install .` 後に `src/` リポジトリ配下でなく任意のディレクトリからプロンプトがロードできること。

### 10.2 リポジトリの分離 ✅

**ツールリポジトリ** `knowledge-bucket/`（本リポジトリ）:

```text
knowledge-bucket/           # pip install可能なCLIパッケージ
  pyproject.toml
  README.md
  src/kb/
    __init__.py
    cli.py
    core.py
    index.py
    ingest.py
    graph.py
    related.py
    dedup.py
    health.py
    analyzer.py
    vectors.py
    embeddings.py
    storage.py
    export.py
    concepts.py
    sync.py
    web.py
    prompts/                # パッケージにバンドル
      analyzer_base.md
      analyzer_web.md
      analyzer_paper.md
      analyzer_repo.md
      analyzer_pdf.md
      analyzer_memo.md
      analyzer_video.md
    parsers/
      paper.py
      pdf.py
      repo.py
      video.py
  tests/
  docs/
    DESIGN.md
    GOAL.md
```

**データリポジトリ**（ユーザーが任意の場所に `kb init` で作成）:

```text
my-knowledge/               # ユーザーのナレッジデータ（Git管理）
  config/
    kb.yml
    aliases.yml
    stop_concepts.yml
    taxonomy.yml
  records/
    doc/
      ab/cd/01K2Z9....md
    concept/
      retrieval-augmented-generation.md
  inbox/
  .kb/                      # Git管理外
    index.db
    vectors.npz
    embeddings.npz
    exports/
    raw/
```

- [x] 現在の `knowledge-bucket/` から `records/`、`config/`、`inbox/`、`.kb/` を削除（`.gitignore` は残す）
- [x] `knowledge-bucket/` の `kb init .` はツール開発用テストデータとしてのみ使用
- [x] ユーザーは別ディレクトリで `kb init ~/my-knowledge` → そこで日常運用
- [x] `.gitignore` に `records/`、`config/`、`inbox/`、`.kb/` を追加（ツールリポジトリ側）

**完了基準**: `pip install -e .` 後、別ディレクトリで `kb init` → `kb add` → `kb search` が完動すること。ツールリポジトリにユーザーデータが混入しないこと。

### 10.3 pyproject.toml の配布設定 ✅

- [x] `name = "kb-tools"` にリネーム（`knowledge-bucket` はリポジトリ名として残す）
- [x] `version` を適切に管理（初期リリース `0.1.0`）
- [x] `[tool.setuptools.package-data]` で `kb/prompts/*.md` を含める
- [x] README.md にインストール方法と使い方を追記（`pip install` → `kb init` → `kb add` の流れ）

**完了基準**: 新しい環境で `pip install git+https://github.com/user/knowledge-bucket.git` → `kb init ~/kb` → `kb add --title "test" --content "hello"` が動くこと。

### 10.4 テストの分離対応 ✅

- [x] テストの `kb_root()` フィクスチャが一時ディレクトリを使うことを確認（既存テストがツールリポジトリ内のデータに依存しないこと）
- [x] `prompts/` の移動に伴うテストパス修正
- [x] `pip install .` 後のスモークテスト追加（`tests/test_install.py`）

**完了基準**: `pip install .` → 別ディレクトリでテストスイートが全件合格すること。

---

## Phase 9: 実運用と統合品質 ✅

### 9.1 Web UIの実用性向上 ✅

- [x] 検索結果のページネーション: 1ページ20件、`/recent?page=2` でページ遷移。200件登録時に全ページが正常表示されること
- [x] 文書一覧のソート: `?sort=date|importance|type` パラメータで並び替え。各ソートのテスト追加
- [x] 文書編集機能: `/doc/<id>/edit` ページでメモ・評価を追記しPOSTでfront matter更新。テストで編集→保存→再表示を確認
- [x] ダークモード: `prefers-color-scheme: media` またはトグルボタンで切替。CSS変数で色管理

**完了基準**: `kb serve` 起動後、200件ダミー文書を登録して全ページ（検索、一覧、詳細、編集）が正常動作すること。各機能のテスト追加。

### 9.2 AI分析パイプライン統合 ✅

- [x] `kb ingest --analyze` でLLM APIを呼び出し、分析結果（concepts, importance, summary）をfront matterに書き込む。`src/kb/analyzer.py` の `parse_analysis_response` を利用
- [x] APIキーは環境変数 `KB_LLM_API_KEY` から読み込み。未設定時は警告してスキップ
- [x] レート制限対応: API呼び出し間に0.5sスリープ、429エラー時にexponential backoff（最大3リトライ）
- [x] 分析失敗時はプレーンなfront matterで保存。`kb analyze --retry-failed` で `analysis.confidence` がない文書だけ再分析

**完了基準**: `KB_LLM_API_KEY=xxx kb ingest --analyze` でAI分析済みレコードが生成され、front matterにconcepts/summary/importanceが記録されること。APIモックテストで検証。

### 9.3 設計書の整合性更新 ✅

- [x] `docs/DESIGN.md` にvideo parser、embeddings、storage、index verify/repair、content_hashの記載があること
- [x] `docs/DESIGN.md` のCLI一覧（section 15）に全コマンド（add-video, raw, index --verify/--repair含む）が列挙されていること
- [x] README.mdのCLIコマンド一覧が現在の実装と一致していること

**完了基準**: `grep -c "kb add-video\|kb raw\|index --verify\|embeddings.py\|storage.py\|parsers/video.py" docs/DESIGN.md` で全項目がヒットすること。

---

## Phase 11: 公開・配布準備 ✅

### 11.1 CI/CD ✅

- [x] `.github/workflows/tests.yml`: push/PR時に `pytest` と `ruff check` を実行。Python 3.11, 3.12, 3.13でmatrix

**完了基準**: PR作成時にGitHub Actionsが自動実行され、テスト・lintが通ること。

### 11.2 プロジェクトメタデータ ✅

- [x] `LICENSE` ファイル追加（MIT）
- [x] `pyproject.toml` に `license`, `authors`, `classifiers`, `urls` 追記
- [x] README.md のGitHub URLを `nobu007/knowledge-bucket` に修正
- [x] `kb --version` コマンド追加（`click.version_option`）

**完了基準**: `pyproject.toml` にlicense・authorが設定され、`kb --version` がバージョン番号を返すこと。

### 11.3 未プッシュコミットの解消

- [ ] 現在のmainブランチのコミットを `git push` でoriginに反映

**完了基準**: `git status` が `Your branch is up to date with 'origin/main'` であること。
