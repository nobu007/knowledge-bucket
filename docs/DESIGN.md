# Git Knowledge Bucket 設計書

## 1. 基本方針

このシステムは、Web記事、GitHubリポジトリ、論文、メモ、PDFなどを、あとからAIと人間が再利用できるようにGitへ記録する。

ただし、Gitを人間向けフォルダ管理には使わない。

- 各情報は **1つのMarkdownドキュメント** として保存する
- 各ドキュメントには **不変ID（ULID）** を付ける
- ファイルパスは意味ではなく、IDから生成したハッシュで分散する
- タグ・カテゴリ・関連リンクはMarkdownに大量に直書きしない
- AIは「候補概念」を少数だけ出す
- 実際のグラフ構造はプログラムがSQLiteで生成する
- Gitに保存するものは「正本」、検索インデックスやグラフDBは「再生成可能なキャッシュ」

---

## 2. 重要な設計判断

### 採用するもの

|項目|方針|
|---|---|
|永続ID|ULID（Crockford Base32、26文字）|
|物理配置|`sha256(id)` によるシャーディング|
|正本|Markdown + YAML Front Matter|
|検索|ローカルSQLite FTS5 / TF-IDF vector index|
|グラフ|プログラムで生成（SQLiteに格納）|
|カテゴリ|物理フォルダではなく仮想ビュー（taxonomy.yml）|
|Git運用|バッチコミット|
|Rawデータ|原則Git外。必要ならS3 / R2 / Git LFS|
|実装言語|Python 3.11+、Click CLI|
|Web UI|Flask + D3.js|

### 採用しないもの

|NG設計|理由|
|---|---|
|人間向けフォルダ分類|100万件で破綻する|
|本文ハッシュをファイル名にする|更新時にパスが変わる|
|Markdownに大量の相互リンクを書く|グラフ更新のたびに大量ファイルが書き換わる|
|`index.parquet` を毎回Gitにコミット|バイナリ差分でGitが肥大化する|
|AIにタグを100個出させる|グラフがノイズ化する|
|1ページごとにgit commit|履歴が無駄に肥大化する|

---

## 3. S3から借りるべき思想

|S3の考え方|このシステムでの対応|
|---|---|
|オブジェクトキーは不変|MarkdownのIDとパスは不変|
|フォルダは実体ではない|カテゴリは仮想ビュー|
|オブジェクト一覧で検索しない|検索はインデックスDBで行う|
|メタデータは別管理|Front Matter + SQLite index|
|イベント駆動処理|inbox → ingest → analyze → index|
|ライフサイクル管理|古いRawデータはS3等へ逃がす|

---

## 4. リポジトリ構造

```text
knowledge-bucket/
  README.md
  pyproject.toml

  config/
    kb.yml              # メイン設定（records_dir、user_interests等）
    taxonomy.yml         # 仮想コレクション定義
    aliases.yml          # 概念の表記揺れ正規化
    stop_concepts.yml    # グラフリンク除外概念

  prompts/
    analyzer_base.md     # 基本分析プロンプト
    analyzer_web.md      # Web記事用
    analyzer_paper.md    # 論文用
    analyzer_repo.md     # Gitリポジトリ用
    analyzer_pdf.md      # PDF用
    analyzer_memo.md     # メモ用

  records/
    doc/
      ab/
        cd/
          01K2Z9P7Y8QWERTY1234567890.md
    concept/
      retrieval-augmented-generation.md
      graph-rag.md

  src/
    kb/
      __init__.py
      cli.py             # Click CLIエントリポイント
      core.py            # ULID生成、シャーディング、設定管理
      index.py           # SQLite FTS5インデックス構築・差分同期
      ingest.py          # inbox → records パイプライン
      graph.py           # 概念グラフ構築・スコアリング
      related.py         # 関連文書検索
      dedup.py           # 重複判定・source_key管理
      health.py          # グラフ品質メトリクス
      analyzer.py        # LLM分析プロンプト生成・レスポンス解析
      vectors.py         # TF-IDF vector index
      export.py          # Parquetエクスポート
      concepts.py        # concept note生成・提案
      sync.py            # Git syncパイプライン
      web.py             # Flask Web UI
      parsers/
        paper.py         # arXiv / DOI メタデータ取得
        pdf.py           # PDFテキスト抽出（pypdf）
        repo.py          # GitHub API メタデータ取得

  tests/
    test_cli.py
    test_core.py
    test_index.py
    test_ingest.py
    ...（18ファイル）

  docs/
    DESIGN.md
    GOAL.md

  .kb/                   # Git管理外
    index.db             # SQLiteインデックス
    vectors.npz          # TF-IDF vector index
    inbox/               # 処理待ちファイル
    exports/             # Parquetエクスポート出力先
```

`.kb/` はGit管理しない。

`.gitignore` で以下を除外：

```gitignore
.kb/
*.sqlite
*.duckdb
*.db
*.log
raw/
cache/
tmp/
.coverage
```

---

## 5. ファイルIDと物理パス

各ドキュメントにはULID（Crockford Base32、26文字）を付ける。

```text
01K2Z9P7Y8QWERTY1234567890
  ^^^^^^^^^^ ^^^^^^^^^^^^^^^
  タイムスタンプ  ランダム部
```

**ULIDの先頭をフォルダに使ってはいけない。** 先頭は時刻なので大量インポート時に同じフォルダへ偏る。

物理パスはSHA256ハッシュで決める：

```text
id = ULID
shard = sha256(id)[0:4]

path = records/doc/{shard[0:2]}/{shard[2:4]}/{id}.md
```

例：`records/doc/ab/cd/01K2Z9P7Y8QWERTY1234567890.md`

このパスは本文が変わっても変えない。シャード深度は `kb.yml` の `shard_depth` で設定可能（デフォルト2）。

---

## 6. Markdownスキーマ

各MarkdownはYAML Front Matter + 本文の形式。

### 基本Front Matter

```yaml
---
id: "01K2Z9P7Y8QWERTY1234567890"
title: "記事または論文またはリポジトリのタイトル"
source_type: web          # web | paper | git_repo | memo | pdf
source_key: "url:https://example.com/article"
content_hash: "sha256:abc123..."
created: "2026-06-07T12:00:00+09:00"
updated: "2026-06-07T12:00:00+09:00"
source: "https://example.com/article"  # 任意
concepts:                               # 任意
  - concept-a
  - concept-b
---
```

### 入力タイプ固有フィールド

論文の場合：

```yaml
paper_authors: "Author1, Author2"
arxiv_id: "2401.12345"
doi: "10.1234/..."
paper_published: "2024-01-15"
```

PDFの場合：

```yaml
pdf_pages: 42
pdf_author: "Author Name"
```

Gitリポジトリの場合：

```yaml
repo_language: "Python"
repo_stars: 1234
repo_topics: "rag, knowledge-graph, sqlite"
```

### 本文セクション（AI生成）

```markdown
# 概要

AIが生成した短い概要。

# 重要ポイント

- 重要ポイント1
- 重要ポイント2
- 重要ポイント3
```

Markdownに保存するのは **候補概念** まで。実際にどの概念をグラフの起点にするかは、インデックス側が決める。

---

## 7. 入力タイプごとの差分

Web記事、論文、Gitリポジトリ、メモ、PDFは最終的に同じMarkdownになる。違うのは最初の抽出処理だけ。

|入力|抽出するもの|パーサー|
|---|---|---|
|Web記事|タイトル、本文、著者、公開日、URL| ingest内で処理 |
|論文|タイトル、著者、Abstract、DOI、arXiv ID| `parsers/paper.py`|
|Gitリポジトリ|README、description、topics、language、stars| `parsers/repo.py`|
|PDF|テキスト、ページ数、著者| `parsers/pdf.py`（pypdf）|
|メモ|本文、作成日時| ingest内で処理|

Gitリポジトリを保存するときは全コードをコピーしない。README、概要、用途、主要構造、参照URLを保存する。

---

## 8. AI分析ルール

AIには自由に大量タグを作らせない。制約付きJSONを返させる。

### AIに出させるもの

- タイトル
- 200〜400字程度の要約
- なぜ重要か
- 重要ポイント 最大5個
- primary concepts 最大3個
- candidate concepts 最大5個
- display tags 最大8個
- entities 最大10個
- confidence
- importance

### AIに出させないもの

- 100個の関連キーワード
- 一般語だけのタグ
- 「AI」「開発」「論文」だけのような粗すぎる概念
- 本文全体の冗長な再生成

### Analyzerプロンプト構成

入力タイプごとに専用プロンプトを使う：

```text
prompts/
  analyzer_base.md     # 共通指示
  analyzer_web.md      # Web記事向け追加指示
  analyzer_paper.md    # 論文向け追加指示
  analyzer_repo.md     # リポジトリ向け追加指示
  analyzer_pdf.md      # PDF向け追加指示
  analyzer_memo.md     # メモ向け追加指示
```

共通制約：

- primary_concepts は最大3個
- candidate_concepts は最大5個
- display_tags は最大8個
- genericすぎる語を避ける
- 複合語・固有名詞・技術名を優先
- 「AI」「Web」「開発」など単独では広すぎる語をprimaryにしない
- 出力はJSONのみ

---

## 9. タグ・概念・グラフ生成ルール

### 用語を3種類に分ける

|種類|役割|上限|
|---|---|---|
|`tags_display`|人間向け表示ラベル|最大8|
|`primary concepts`|その文書の中心概念|最大3|
|`active graph terms`|実際にグラフ接続に使う概念|最大5|

AIが出した候補をそのままリンクに使わない。必ずプログラム側でフィルタする。

---

## 10. 概念フィルタリング

インデックス側で各概念の出現文書数 `df` を持つ。

```text
N = 全ドキュメント数
df = その概念を持つドキュメント数
```

### Hub判定

```text
hub_threshold = min(5000, max(50, floor(0.002 * N)))
```

|N|hub_threshold|
|---|---|
|1,000|50|
|10,000|50|
|100,000|200|
|1,000,000|2,000|

`df > hub_threshold` の概念は文書間リンクに使わない。表示タグや大分類としては使える。

---

## 11. active graph terms の選び方

各候補概念にスコアを付ける：

```text
score =
  0.40 * AI重要度（weight）
+ 0.25 * IDFスコア（正規化済み）
+ 0.15 * 固有名詞/技術名ブースト（ハイフン含む、大文字含む）
+ 0.10 * 複合語ブースト（複数単語）
+ 0.10 * ユーザー関心との一致（user_interests設定）
- generic penalty
- hub penalty（二乗ペナルティ）
```

選択ルール：

- 最大5個
- Hub概念は除外
- あまりに一般的な語は除外
- `df = 1` の完全新規概念は保持するが、文書間リンクにはまだ使わない
- `df >= 2` になってからリンク候補に使う

---

## 12. グラフ構造

グラフはMarkdown内に書かず、SQLiteに生成する。

### ノード種別

|ノード|例|
|---|---|
|document|記事、論文、repo、メモ|
|concept|GraphRAG、RAG、ULID|
|source|URL、DOI、GitHub repo|
|entity|人名、組織、ツール、ライブラリ|

### エッジ種別

|Edge|意味|
|---|---|
|document → concept|この文書はこの概念を含む|
|document → document|関連文書|
|concept → concept|概念同士の共起|
|document → source|元URLやDOI|
|document → entity|登場するツール・人物・組織|

### 文書間リンク生成

新しい文書 `D` が追加されたら、

1. `active graph terms` を取得
2. 各概念に紐づく既存文書を取る
3. Hub概念は使わない
4. 候補文書だけをスコアリング
5. 上位5〜10件だけを `document → document` edge として保存

スコア：

```text
related_score =
  共有する希少概念のIDF合計
+ source type補正
+ recency補正
```

**全100万文書と比較しない。**

---

## 13. SQLiteインデックス設計

`.kb/index.db` をローカルに持つ。Gitに入れない。

### docs（FTS5全文検索）

```sql
CREATE VIRTUAL TABLE docs USING fts5(
    id UNINDEXED,
    title,
    source,
    source_type UNINDEXED,
    rel_path UNINDEXED,
    content
)
```

### concepts

```sql
CREATE TABLE concepts (
    concept_id TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'concept',  -- concept | entity
    df         INTEGER NOT NULL DEFAULT 0,
    is_stop    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
)
```

`kind` で概念とエンティティを区別する。

### doc_concepts

```sql
CREATE TABLE doc_concepts (
    doc_id     TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT 'primary',  -- primary | candidate
    weight     REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (doc_id, concept_id)
) WITHOUT ROWID
```

### edges

```sql
CREATE TABLE edges (
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    edge_type  TEXT NOT NULL,  -- related | cooccurrence | entity | source
    weight     REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id, edge_type)
) WITHOUT ROWID
```

### doc_stats

```sql
CREATE TABLE doc_stats (
    doc_id      TEXT PRIMARY KEY,
    source_type TEXT NOT NULL DEFAULT 'web',
    has_source  INTEGER NOT NULL DEFAULT 0,
    importance  REAL NOT NULL DEFAULT 0.0,
    updated_at  TEXT NOT NULL
)
```

文書の重要度スコアとメタデータを保持する。

### sources（重複判定用）

```sql
CREATE TABLE sources (
    source_key    TEXT PRIMARY KEY,
    canonical_url TEXT,
    first_doc_id  TEXT NOT NULL,
    last_doc_id   TEXT NOT NULL,
    content_hash  TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
```

### kv_meta（インデックス管理用）

```sql
CREATE TABLE kv_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
)
```

`last_indexed_commit` の保存に使い、差分インデックス更新の基準点とする。

---

## 14. インデックス更新方式

GitのHEADを使って差分更新する。

`kv_meta` テーブルに `last_indexed_commit` を保存する。

```text
last_indexed_commit = abc123
current_head = def456
```

更新時：

```text
git diff --name-status abc123 def456 -- records/doc
```

で変更ファイルだけ取得し、そのMarkdownだけ再パースする。

- 初回clone時：`kb index --rebuild`（全件rebuild）
- 通常運用：`kb index --sync`（差分更新）

差分更新時は追加・更新・削除をすべて処理し、古いエントリのクリーンアップも行う。

---

## 15. CLI仕様

Python Clickで実装。`kb` コマンドとしてエントリポイントを提供する。

### 基本コマンド

```bash
kb init                       # ディレクトリ構造と設定ファイルを初期化
kb add <url-or-text>          # inboxに追加（--title, --source, --content, --type, --concepts）
kb ingest                     # inbox内の未処理アイテムを処理
kb index --sync               # 差分インデックス更新
kb index --rebuild            # 全件インデックス再構築
kb search "<query>"           # FTS5検索（--limit, --semantic）
kb show <doc_id>              # 文書メタデータと本文表示（--full）
kb related <doc_id>           # 関連文書表示（--limit）
kb concept <concept_id>       # 概念メタデータ・関連文書・共起概念表示
kb export parquet             # グラフデータをParquetでエクスポート
kb sync                       # Git同期パイプライン
```

### 専用入力コマンド

```bash
kb add-paper <arxiv-or-doi>   # 論文をarXiv URL/ID、DOI、またはタイトルで追加
kb add-pdf <file>             # PDFをテキスト抽出して追加
kb add-repo <github-url>      # GitHubリポジトリをメタデータ取得して追加
```

### グラフ・分析コマンド

```bash
kb graph build                # 概念グラフを構築
kb concepts suggest           # concept noteの昇格候補を提案・生成
kb analyze                    # 文書のLLM分析プロンプトを構築
kb health                     # グラフ品質メトリクス表示（--json）
kb vectorize                  # TF-IDF vector index構築
kb collections                # 仮想コレクション一覧表示
kb serve                      # ローカルWeb UI起動（Flask）
```

### `kb add`

入力を inbox に追加するだけ。この時点ではAI分析しない。即座に終わる。

### `kb ingest`

inbox内の未処理アイテム（.md, .txt, .url）を処理する：

1. ファイル拡張子判定
2. 内容読み込み・空ファイル除去
3. 入力タイプ分類（web / memo）
4. 本文抽出
5. ULID生成
6. source_key・content_hash生成
7. 重複判定（既存なら内容比較 → 同じならスキップ、変更あればin-place更新）
8. Front Matter生成・ファイル書き出し
9. inbox内ファイル削除
10. sourcesテーブル登録

### `kb sync`

1. `git pull --rebase`
2. `kb index --sync`
3. `kb ingest`
4. `kb index --sync`
5. `git add records config prompts`
6. batch commit
7. `git push`

---

## 16. Git運用ルール

### コミット単位

1件ごとにcommitしない。推奨：50件ごと、100件ごと、1時間ごと、1日ごと。

```text
kb: ingest 128 items on 2026-06-07
```

### コミット対象

Gitに入れるもの：

```text
records/doc/**/*.md
records/concept/**/*.md
config/**
prompts/**
README.md
```

Gitに入れないもの：

```text
.kb/index.db
.kb/vectors.npz
.kb/exports/*
.kb/inbox/*
raw/*
cache/*
tmp/*
```

### Rawデータ

記事全文、HTML、PDF、巨大repo snapshotは通常Gitに入れない。必要ならS3 / R2 / Git LFSを使う。

---

## 17. 重複判定

source_keyを必ず作る。`dedup.py` が管理する。

|入力|source_key形式|
|---|---|
|Web|`url:<canonical_url>`（UTM除去）|
|論文|`doi:<doi>` / `arxiv:<arxiv_id>` / `paper:<hash>`（優先順）|
|GitHub repo|`repo:github.com/owner/name` または `repo:github.com/owner/name@commit`|
|メモ|`memo:<ulid>`|

同じsource_keyが存在する場合、新規作成せず既存Markdownを更新する。

---

## 18. 更新ルール

同じURLを再取得したとき：

- source_keyが同じ + content_hashが同じ → 何もしない
- content_hashが変わった → 同じIDのMarkdownをin-place更新、`updated_at` を更新

ファイルパスは絶対に変えない。Git履歴で過去版を保持する。

---

## 19. カテゴリ設計

物理カテゴリフォルダは作らない。`config/taxonomy.yml` で仮想コレクションを定義する。

```yaml
collections:
  papers:
    description: "Academic papers"
    filters:
      source_type: paper
  github_repos:
    description: "GitHub repositories"
    filters:
      source_type: git_repo
  pdfs:
    description: "PDF documents"
    filters:
      source_type: pdf
```

UIでの表示：

```text
Papers / GitHub Repos / PDFs / 最近保存したもの / 重要度が高いもの
```

---

## 20. concept note

すべての概念をMarkdown化しない。重要な概念だけ `records/concept/` に昇格する。

```text
records/concept/retrieval-augmented-generation.md
records/concept/graph-rag.md
```

concept noteには、概念説明、aliases、関連概念、代表文書、自分の理解を書く。

昇格条件：

- 出現頻度が一定以上
- 自分がよく検索する
- プロジェクトに関係する
- AIが中心概念として何度も出す

`kb concepts suggest` で昇格候補を提案・生成できる。

---

## 21. 大規模化ルール

Markdownが30万〜50万件、repo sizeが5〜10GBを超えたらshard分割を検討する。

```text
kb-root/
  config/
  prompts/
  shards/
    0/ ... f/
```

`shard = sha256(id)[0]`

v1では単一repoで運用する。

---

## 22. UI設計

Flask + D3.js でローカルWeb UIを実装。`kb serve` で起動する。

### 画面一覧

|ルート|画面|
|---|---|
|`/`|検索 + 最近の文書|
|`/doc/<doc_id>`|文書詳細 + 関連文書|
|`/recent`|最近保存した文書一覧|
|`/categories`|ソースタイプ別カテゴリ一覧|
|`/categories/<type>`|カテゴリ別文書一覧|
|`/concepts`|概念一覧|
|`/concepts/<id>`|概念詳細 + 関連文書 + 共起概念|
|`/graph`|D3.js インタラクティブフォースグラフ|
|`/health`|グラフ品質メトリクス|
|`/collections`|仮想コレクション一覧|
|`/collections/<name>`|コレクション別文書一覧|

### API

|エンドポイント|機能|
|---|---|
|`/api/recent`|最近の文書JSON|
|`/api/search`|検索結果JSON|
|`/api/stats`|統計情報JSON|
|`/api/graph`|グラフデータJSON|
|`/api/health`|品質メトリクスJSON|

---

## 23. 品質管理メトリクス

`kb health` で確認する指標：

```text
総文書数
総概念数
1文書あたり平均concept数
Hub概念ランキング
orphan文書率
重複率
関連リンクの平均次数
最大次数
```

Hub概念ランキングが最も重要。AI、Python、GitHub、Web、Research が大量リンクの中心になっていたらグラフが壊れている。`stop_concepts.yml` に入れる。

---

## 24. 設定ファイル

### config/kb.yml

```yaml
records_dir: records
doc_dir: records/doc
concept_dir: records/concept
inbox_dir: .kb/inbox
shard_depth: 2
user_interests:
  - knowledge-management
  - information-retrieval
```

### config/stop_concepts.yml

```yaml
stop_concepts:
  - ai
  - artificial-intelligence
  - programming
  - software
  - web
  - article
  - research
  - paper
  - github
  - python
  - javascript
```

表示タグとしては使えるが、文書間リンク生成には使わない。

### config/aliases.yml

```yaml
aliases:
  rag: retrieval-augmented-generation
  retrieval augmented generation: retrieval-augmented-generation
  retrieval-augmented-generation: retrieval-augmented-generation
  graph rag: graph-rag
  graphrag: graph-rag
  llm: large-language-model
  large language model: large-language-model
```

AIの出力をそのまま使わず、必ずalias解決する。

---

## 25. セキュリティと著作権

- GitHubに置くならprivate repo
- API keyをMarkdownに書かない
- 記事全文やPDF全文を保存すると著作権・規約に触れる可能性がある
- 原則はURL、要約、自分のメモ、短い引用にする
- raw保存が必要ならprivate storageに保存する

---

## 26. 設計の核心

> **Gitに知識を保存する。しかしGitで知識を探さない。Gitは正本、検索とグラフは生成物。**

3つの重要原則：

1. **ファイル名はULID、パスはIDハッシュで固定** — 更新でパスが変わらない
2. **AIは少数の概念候補だけ出す** — タグ爆発を防ぐ
3. **相互リンクはMarkdownではなくインデックスDBで生成する** — リンク爆発を防ぐ

この設計でWeb記事、GitHub repo、論文、メモが同じパイプラインに乗り、100万件規模でも「タグ爆発」「リンク爆発」「フォルダ破綻」を抑えられる。
