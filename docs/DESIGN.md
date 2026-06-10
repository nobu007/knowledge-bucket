# Git Knowledge Bucket 設計書

## 1. 基本方針

このシステムは、Web記事、GitHubリポジトリ、論文、メモ、PDFなどを、あとからAIと人間が再利用できるようにGitへ記録する。

ただし、Gitを人間向けフォルダ管理には使わない。

採用する設計は以下。

- 各情報は **1つのMarkdownドキュメント** として保存する
- 各ドキュメントには **不変ID** を付ける
- ファイルパスは意味ではなく、IDから生成したハッシュで分散する
- タグ・カテゴリ・関連リンクはMarkdownに大量に直書きしない
- AIは「候補概念」を少数だけ出す
- 実際のグラフ構造はプログラムがSQLite / DuckDB / Parquetなどで生成する
- Gitに保存するものは「正本」
- 検索インデックスやグラフDBは「再生成可能なキャッシュ」

---

## 2. 重要な設計判断

### 採用するもの

|項目|方針|
|---|---|
|永続ID|ULID|
|物理配置|`sha256(id)` によるシャーディング|
|正本|Markdown|
|検索|ローカルSQLite FTS / optional vector index|
|グラフ|プログラムで生成|
|カテゴリ|物理フォルダではなく仮想ビュー|
|Git運用|バッチコミット|
|Rawデータ|原則Git外。必要ならS3 / R2 / Git LFS|

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

S3をそのまま使う必要はないが、設計思想はかなり参考になる。

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

  config/
    kb.yml
    taxonomy.yml
    aliases.yml
    stop_concepts.yml

  prompts/
    analyzer_v1.md

  records/
    doc/
      ab/
        cd/
          01K2Z9P7Y8QWERTY1234567890.md
    concept/
      retrieval-augmented-generation.md
      graph-rag.md

  exports/
    README.md
    # 任意。Parquet等のスナップショットを置く場合だけ使う。

  scripts/
    README.md

  .kb/
    index.sqlite
    vector.index
    cache/
    inbox/

```

`.kb/` はGit管理しない。

`.gitignore` はこうする。

```gitignore
.kb/
*.sqlite
*.duckdb
*.db
*.log
raw/
cache/
tmp/
```

---

## 5. ファイルIDと物理パス

各ドキュメントにはULIDを付ける。

例：

```text
01K2Z9P7Y8QWERTY1234567890
```

ただし、**ULIDの先頭をフォルダに使ってはいけない。**

ULIDの先頭は時刻なので、大量インポート時に同じフォルダへ偏る。
そのため、物理パスは次で決める。

```text
id = ULID
shard = sha256(id)[0:4]

path = records/doc/{shard[0:2]}/{shard[2:4]}/{id}.md
```

例：

```text
records/doc/ab/cd/01K2Z9P7Y8QWERTY1234567890.md
```

このパスは本文が変わっても変えない。

---

## 6. Markdownスキーマ

各Markdownはこの形式にする。

```markdown
---
schema_version: 1
id: "01K2Z9P7Y8QWERTY1234567890"
type: "web" # web | paper | git_repo | memo | pdf | video
status: "active"

title: "記事または論文またはリポジトリのタイトル"

source:
  source_key: "url:https://example.com/article"
  url: "https://example.com/article"
  canonical_url: "https://example.com/article"
  captured_at: "2026-06-07T12:00:00+09:00"
  retrieved_at: "2026-06-07T12:00:03+09:00"
  content_hash: "sha256:..."
  raw_ref: null

analysis:
  analyzer_version: "analyzer_v1"
  model: "gpt-..."
  language: "ja"
  confidence: 0.82
  importance: 0.67

concepts:
  primary:
    - id: "concept:retrieval-augmented-generation"
      label: "Retrieval-Augmented Generation"
      weight: 0.94
    - id: "concept:knowledge-graph"
      label: "Knowledge Graph"
      weight: 0.81
  candidates:
    - id: "concept:graph-rag"
      label: "GraphRAG"
      weight: 0.76
    - id: "concept:markdown-knowledge-base"
      label: "Markdown Knowledge Base"
      weight: 0.65
  entities:
    - id: "tool:github"
      label: "GitHub"
    - id: "tool:sqlite"
      label: "SQLite"

tags_display:
  - "AI"
  - "Git"
  - "Knowledge Management"

user:
  note: ""
  rating: null
  project: null
---

# 概要

ここにAIが生成した短い概要を書く。

# 重要ポイント

- 重要ポイント1
- 重要ポイント2
- 重要ポイント3

# なぜ保存したか

この情報を後で参照する理由を書く。

# 詳細メモ

必要なら人間またはAIの詳細メモを書く。

# 引用・抜粋

著作権に注意し、必要最小限の抜粋だけ保存する。

# 今後の使い道

- 関連しそうなプロジェクト
- 調べ直したい論点
- 実装に使えそうな部分

```

ポイントは、Markdownに保存するのは **候補概念** までにすること。
実際にどの概念をグラフの起点にするかは、あとでインデックス側が決める。

---

## 7. 入力タイプごとの差分

Web記事、論文、Gitリポジトリ、メモは、最終的には同じMarkdownになる。
違うのは最初の抽出処理だけ。

|入力|抽出するもの|
|---|---|
|Web記事|タイトル、本文、著者、公開日、URL|
|論文|タイトル、著者、Abstract、DOI、arXiv ID、結論|
|Gitリポジトリ|README、description、topics、language、stars、主要ファイル構成|
|PDF|テキスト、章構造、図表キャプション|
|メモ|本文、作成日時、ユーザー指定タグ|

Gitリポジトリを保存するときは、最初から全コードをknowledge repoへコピーしない。
まずはREADME、概要、用途、主要構造、参照URLを保存する。
必要な場合だけ、別途 shallow clone や Git submodule / Git bundle を検討する。

---

## 8. AI分析ルール

AIには自由に大量タグを作らせない。
必ず制約付きJSONを返させる。

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

### Analyzer Prompt 方針

```text
あなたは個人用ナレッジベースの情報圧縮器です。

目的:
入力されたWeb記事、論文、Gitリポジトリ、メモを、
あとで検索・グラフ化・AI再利用しやすいMarkdownレコードへ変換する。

制約:
- primary_concepts は最大3個
- candidate_concepts は最大5個
- display_tags は最大8個
- genericすぎる語を避ける
- 可能なら複合語・固有名詞・技術名を優先する
- 「AI」「Web」「開発」「ツール」など単独では広すぎる語をprimaryにしない
- 出力はJSONのみ
```

---

## 9. タグ・概念・グラフ生成ルール

ここが最重要。

### 用語を3種類に分ける

|種類|役割|上限|
|---|---|---|
|`tags_display`|人間向け表示ラベル|最大8|
|`primary concepts`|その文書の中心概念|最大3|
|`active graph terms`|実際にグラフ接続に使う概念|最大5|

AIが出した候補をそのままリンクに使わない。
必ずプログラム側でフィルタする。

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

例：

|N|hub_threshold|
|---|---|
|1,000|50|
|10,000|50|
|100,000|200|
|1,000,000|2,000|

`df > hub_threshold` の概念は、広すぎるので文書同士の直接リンクには使わない。

例：

- AI
- Python
- GitHub
- Machine Learning
- Web

これらは表示タグや大分類としては使ってよい。
しかし、文書間リンクの根拠にはしない。

---

## 11. active graph terms の選び方

各候補概念にスコアを付ける。

```text
score =
  0.40 * AI重要度
+ 0.25 * IDFスコア
+ 0.15 * 固有名詞/技術名ブースト
+ 0.10 * 複合語ブースト
+ 0.10 * ユーザー関心との一致
- generic penalty
- hub penalty
```

その上で、

- 最大5個
- Hub概念は除外
- あまりに一般的な語は除外
- `df = 1` の完全新規概念は保持するが、文書間リンクにはまだ使わない
- `df >= 2` になってからリンク候補に使う

---

## 12. グラフ構造

グラフはMarkdown内に大量に書かない。
SQLiteなどのインデックスDBに生成する。

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

文書間リンクのスコア例：

```text
related_score =
  共有する希少概念のIDF合計
+ BM25類似度
+ embedding類似度
+ source type補正
+ recency補正
```

重要なのは、**全100万文書と比較しない** こと。

---

## 13. SQLiteインデックス設計

`.kb/index.sqlite` をローカルに持つ。
これはGitに入れない。

主要テーブルは以下。

```text
docs
  id
  path
  type
  title
  source_key
  url
  created_at
  updated_at
  language
  summary
  content_hash
  importance
  analyzer_version

concepts
  concept_id
  label
  kind
  df
  is_stop
  created_at

doc_concepts
  doc_id
  concept_id
  role
  weight

edges
  src_id
  dst_id
  edge_type
  weight
  evidence
  updated_at

sources
  source_key
  canonical_url
  first_doc_id
  last_doc_id

docs_fts
  title
  summary
  body

```

検索は必ずSQLite経由で行う。
Gitのファイルツリーを人間が直接検索しない。

---

## 14. インデックス更新方式

GitのHEADを使って差分更新する。

`.kb/index.sqlite` に最後に処理したGit commitを保存する。

```text
last_indexed_commit = abc123
current_head = def456
```

更新時は、

```text
git diff --name-status abc123 def456 -- records/doc
```

で変更ファイルだけ取得し、そのMarkdownだけ再パースする。

初回clone時だけ全件rebuildする。

```bash
kb index --rebuild
```

通常運用では、

```bash
kb index --sync
```

で差分更新する。

---

## 15. CLI仕様

最低限このCLIを作る。

```bash
kb init
kb add <url-or-file-or-text>
kb ingest
kb index --sync
kb index --rebuild
kb search "<query>"
kb show <doc_id>
kb related <doc_id>
kb concept <concept_id>
kb export parquet
kb sync
```

### `kb add`

入力を `.kb/inbox/` に入れるだけ。
この時点ではAI分析しない。
即座に終わること。

```bash
kb add https://example.com/article
kb add https://github.com/owner/repo
kb add paper.pdf
kb add "あとで調べたいメモ本文"
```

### `kb ingest`

inbox内の未処理アイテムを処理する。

処理順序：

1. 入力タイプ判定
2. URL正規化
3. 本文抽出
4. source_key生成
5. 重複判定
6. AI分析
7. Markdown生成
8. records/doc 配下へ保存
9. SQLite index更新

### `kb search`

SQLite FTSと概念インデックスで検索する。

```bash
kb search "GraphRAG Git markdown"
```

### `kb related`

指定文書の近傍グラフを表示する。

```bash
kb related 01K2Z9P7Y8QWERTY1234567890
```

### `kb sync`

以下をまとめて実行する。

1. `git pull --rebase`
2. `kb index --sync`
3. `kb ingest`
4. `kb index --sync`
5. テスト
6. `git add records config prompts`
7. batch commit
8. `git push`

---

## 16. Git運用ルール

### コミット単位

1件ごとにcommitしない。

推奨：

- 50件ごと
- 100件ごと
- 1時間ごと
- 1日ごと

例：

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

原則Gitに入れないもの：

```text
.kb/index.sqlite
.kb/vector.index
raw/*
cache/*
tmp/*
```

### Rawデータ

記事全文、HTML、PDF、巨大repo snapshotを通常Gitに入れない。
必要なら以下のどれかにする。

1. 保存しない
2. 抜粋だけMarkdownへ保存
3. ローカルraw cacheに保存
4. S3 / Cloudflare R2へ保存
5. Git LFSを使う

Markdownには参照だけ書く。

```yaml
source:
  raw_ref: "s3://my-kb-raw/web/ab/cd/01K2Z9....html.gz"
```

---

## 17. 重複判定

source_keyを必ず作る。

### Web

```text
url:<canonical_url>
```

UTMなどは除去する。

### 論文

優先順：

```text
doi:<doi>
arxiv:<arxiv_id>
paper:<normalized_title_hash>
```

### GitHub repo

```text
repo:github.com/owner/name
```

特定commitを保存する場合：

```text
repo:github.com/owner/name@commit_sha
```

### メモ

```text
memo:<ulid>
```

同じsource_keyが存在する場合、デフォルトでは新規作成しない。
既存Markdownを更新する。

---

## 18. 更新ルール

同じURLを再取得したとき、

- source_keyが同じ
- content_hashが同じ

なら何もしない。

content_hashが変わった場合、

- 同じIDのMarkdownを更新する
- Git履歴で過去版を保持する
- 必要なら `updated_at` を更新する

ファイルパスは絶対に変えない。

---

## 19. カテゴリ設計

物理カテゴリフォルダは作らない。

カテゴリはSQLite側の仮想ビューとして扱う。

例：

```yaml
virtual_collections:
  ai_agents:
    include_concepts:
      - concept:ai-agent
      - concept:tool-use
      - concept:rag

  papers:
    include_types:
      - paper

  github_repos:
    include_types:
      - git_repo
```

人間が見るときだけ、

```text
AI Agents
Papers
GitHub Repos
最近保存したもの
重要度が高いもの
未読
```

のように表示する。

---

## 20. concept note

すべての概念をMarkdown化しない。
重要な概念だけ `records/concept/` に昇格する。

例：

```text
records/concept/retrieval-augmented-generation.md
records/concept/graph-rag.md
records/concept/ulid.md
```

concept noteには、

- 概念説明
- aliases
- 関連概念
- 代表文書
- 自分の理解

を書く。

概念の昇格条件：

- 出現頻度が一定以上
- 自分がよく検索する
- プロジェクトに関係する
- AIが中心概念として何度も出す

---

## 21. 大規模化ルール

最初は1リポジトリでよい。

ただし、以下を超えたら分割を検討する。

- Markdownが30万〜50万件を超える
- repo sizeが5〜10GBを超える
- clone / status / push が明確に遅くなる
- Rawデータを保存したくなる

分割する場合は、IDハッシュで16 shardにする。

```text
kb-root/
  config/
  prompts/
  shards/
    0/
    1/
    2/
    ...
    f/
```

どのshardに入れるか：

```text
shard = sha256(id)[0]
```

ただし、最初からやると運用が面倒なので、v1では単一repoで開始する。

---

## 22. UI方針

人間はGitHubのファイル一覧を見ない。

最低限のUIは次のどれかで作る。

- CLI
- Streamlit
- FastAPI + React / Next.js
- Tauri desktop app
- Obsidianは小規模閲覧用に限定

UIが提供する画面：

1. 全文検索
2. タグ検索
3. 概念検索
4. 文書詳細
5. 関連文書
6. concept graph
7. 最近保存したもの
8. 未整理 / 要確認
9. プロジェクト別ビュー

---

## 23. 品質管理メトリクス

定期的に以下を見る。

```text
総文書数
総概念数
1文書あたり平均concept数
Hub概念ランキング
orphan文書率
重複率
関連リンクの平均次数
最大次数
検索ヒット率
AI分析失敗率
```

特に見るべきはこれ。

```text
Hub概念ランキング
```

もし、

```text
AI
Python
GitHub
Web
Research
```

のような概念が大量リンクの中心になっていたら、グラフが壊れている。

その場合は `stop_concepts.yml` に入れる。

---

## 24. stop_concepts.yml

例：

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

これらは表示タグとしては使えるが、文書間リンク生成には使わない。

---

## 25. aliases.yml

表記揺れを潰す。

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

## 26. セキュリティと著作権

必須ルール：

- GitHubに置くならprivate repo
- API keyをMarkdownに書かない
- private repoや社内文書を保存する場合は特に注意
- 記事全文やPDF全文を保存すると著作権・規約に触れる可能性がある
- 原則はURL、要約、自分のメモ、短い引用にする
- raw保存が必要ならprivate storageに保存する
- commit前にsecret scanを走らせる

---

## 27. 実装順序（完了済み）

### Phase 1: 最小版 ✅

- `kb init`
- `kb add`
- `kb ingest`
- Markdown生成
- ULID ID
- hash shard path
- SQLite index
- `kb search`
- `kb sync`

### Phase 2: グラフ生成 ✅

- concept正規化
- aliases
- stop_concepts
- df / idf計算
- active graph terms生成
- document-document edges
- `kb related`

### Phase 3: AI強化 ✅

- source type別プロンプト
- Git repo解析
- 論文解析
- PDF解析
- importance推定
- concept note自動生成候補

### Phase 4: UI ✅

- ローカルWeb UI（Flask）
- 検索画面
- 文書詳細
- 関連文書
- concept graph
- 仮想カテゴリビュー

### Phase 5: 大規模化 ✅

- TF-IDF vector index
- セマンティック検索
- Parquet export
- グラフヘルスダッシュボード

---

## 28. 設計の核心

> **Gitに知識を保存する。
> しかしGitで知識を探さない。
> Gitは正本、検索とグラフは生成物。**

特に重要なのはこの3つ。

1. **ファイル名はULID、パスはIDハッシュで固定**
2. **AIは少数の概念候補だけ出す**
3. **相互リンクはMarkdownではなくインデックスDBで生成する**

この設計なら、Web記事、GitHub repo、論文、メモが全部同じパイプラインに乗る。
しかも、100万件規模になっても「タグ爆発」「リンク爆発」「フォルダ破綻」をかなり抑えられる。
