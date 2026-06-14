# ドメイン特化学習データ 生成指示書

ナレッジバケット（kb）に蓄積した curated corpus から、自社ドメイン特化 LLM 向けの教師データ（SFT）を生成する手順書。

> 対象バージョン: kb-tools 0.1.0 / 生成基盤: ai-hub-agent-proxy（Claude Code）

---

## 0. 前提

| 要件 | 確認コマンド |
|---|---|
| kb インストール済み | `kb --version` |
| agent proxy 設定 | `echo $KB_AGENT_PROXY`（未設定なら `~/ai-hub_agent_proxy/dist/cli.js` を自動検出） |
| corpus 分析済み | `kb doctor`（QC4 analysis coverage = all analyzed であること） |
| ドメイン候補の把握 | `kb health` の Top Concepts を確認 |

```bash
# 前提チェック（全て通ること）
export KB_AGENT_PROXY=~/ai-hub_agent_proxy/dist/cli.js
kb doctor        # QC1-5 全 ok
kb health        # ドメイン概念と df を確認
```

---

## 1. ドメインの選定

生成対象ドメインは **概念（concept）** または **ソースタイプ（source_type）** で指定する。

```bash
# ドメイン候補の確認
kb concept retrieval-augmented-generation   # この概念に属する doc 一覧
kb search "<ドメインキーワード>" --semantic  # 関連 doc を意味検索で確認
```

選定基準:
- `df >= 3` の概念を選ぶと doc が揃い、多様なペアが生成できる
- 狭すぎる概念（df=1）は生成量が伸びない。広すぎる概念（hub）はノイズ化

---

## 2. 生成の実行

### 基本形

```bash
kb generate \
  --concept <概念スラグ> \      # front matter の概念で絞り込み（部分一致）
  --pairs 5 \                    # doc 1件あたりの生成ペア数
  --format openai \              # openai（messages）または alpaca（instruction/input/output）
  --limit 50 \                   # 処理する doc 上限（省略=全件）
  -o .kb/training/sft-<domain>.jsonl
```

### 実例（RAG ドメイン）

```bash
kb generate --concept retrieval-augmented-generation \
            --pairs 5 --format openai --limit 20 \
            -o .kb/training/sft-rag.jsonl
```

### オプション一覧

| オプション | 既定 | 説明 |
|---|---|---|
| `--concept` | なし | front matter の概念で doc を絞る（部分一致・大小無視） |
| `--type` | なし | `web` / `paper` / `git_repo` / `memo` / `pdf` / `video` で絞る |
| `--pairs`, `-n` | 5 | doc 1件あたりの生成ペア数 |
| `--format` | openai | `openai`（messages）または `alpaca` |
| `--limit` | なし | 処理 doc 上限 |
| `--output`, `-o` | 自動 | 出力 JSONL パス（省略時 `.kb/training/sft-<tag>.jsonl`） |

`--concept` と `--type` は **AND 条件** で併用可（例: 論文の RAG のみ）。

---

## 3. 出力仕様

1行1レコードの JSONL。

### openai 形式

```json
{
  "messages": [
    {"role": "system", "content": "あなたはドメイン特化のAIアシスタントです…"},
    {"role": "user", "content": "<指示＋制約>"},
    {"role": "assistant", "content": "<本文事実に基づく応答>"}
  ],
  "_source_doc": "01KV1XB7NV4FSZW6...",
  "_difficulty": "intermediate",
  "_tags": ["zep", "sdk", "compliance"]
}
```

### alpaca 形式

```json
{
  "instruction": "<指示>",
  "input": "<追加前提・制約>",
  "output": "<応答>",
  "_source_doc": "...", "_difficulty": "...", "_tags": [...]
}
```

### 品質保証（実装済み）

- **grounding**: プロンプトが「本文の事実に基づく・憶測禁止・未記載なら正直に」と指示。出力に「文書によると…」が現れる
- **難易度分散**: basic / intermediate / advanced を毎ドキュメントで混在
- **重複排除**: 指示文の sha1 下位12桁で全ドキュメント横断 dedup
- **スキップ**: 指示または応答が空のペアは破棄
- **fenced-JSON 耐性**: agent が ` ```json ` で囲んでも正しく抽出

`_` 始まりキーは出所メタ。学習時は無視するか、評価用に残すこと。

---

## 4. 生成物の検証

```bash
# 行数
wc -l .kb/training/sft-rag.jsonl

# 形式チェック（全行が valid JSON か）
python3 -c "import json,sys; [json.loads(l) for l in open('.kb/training/sft-rag.jsonl')]; print('all valid')"

# 難易度・出所の偏り確認
python3 -c "
import json, collections
diff = collections.Counter(json.loads(l)['_difficulty'] for l in open('.kb/training/sft-rag.jsonl'))
print('difficulty:', dict(diff))
src = collections.Counter(json.loads(l)['_source_doc'][:12] for l in open('.kb/training/sft-rag.jsonl'))
print('sources:', len(src), 'docs')
"
```

合格基準:
- 全行 valid JSON
- `_difficulty` が3種類揃っている（偏りがない）
- 出所 doc が `--limit` 件に分布している

---

## 5. スケールと所要時間

- **逐次**: doc 1件（pairs=3）≈ 100秒。`pairs` 増やすと概ね比例
- **目安**: 39 doc × pairs=5 ≈ 逐次で 30〜40分
- 現状 `kb generate` は **逐次**。大量生成時は、ドメインを分割して複数プロセスで並列実行（`kb generate` を別出力ファイルで同時起動）することで線形に高速化
- `--workers` 並列オプションは未実装（次期追加候補）

---

## 6. ワークフローまとめ

```
[1] 前提チェック      kb doctor / kb health
[2] ドメイン選定      kb concept <slug> で doc 数確認
[3] 小規模試行        kb generate --concept X --pairs 3 --limit 3 -o trial.jsonl
[4] 品質目視          trial.jsonl を確認（grounding / 難易度 / 実務性）
[5] 本格生成          --limit を外して全件、または複数ドメインを並列
[6] 検証              4. のスクリプトで形式・偏りチェック
[7] （仕分け）        後工程で不要ペアを除去・ドメイン再編成
```

---

## 7. 既知の制限・注意

- 生成は doc の **要約＋概念＋本文先頭4000字** を入力とする。本文が短い doc はペアの多様性が落ちる
- agent が稀に JSON 以外を返すとその doc はスキップされ warning に出る（生成自体は継続）
- `_source_doc` で出所を追跡できるので、後の仕分け（ドメイン再割当・不適切ペア除去）は出所ベースで行える
- 同一ドキュメントから多様なペアを得たい場合は `--pairs` を大きく（8〜10）する。重複排除が効くので冗長にはならない

---

## 付録: corpus へのドメイン追加

自社業務ドメインの doc が不足している場合は、分析済み doc を追加してから生成する。

```bash
kb add-repo https://github.com/<org>/<repo>     # 既存docなら更新・新規なら作成＋即時index
kb add --title "..." --type web < article.md
kb analyze --retry-failed -w 4                  # 並列分析（概念・要約を付与）
kb graph build                                  # グラフ更新（概念 df 再計算）
```

追加後、`kb health` の Top Concepts に新しいドメイン概念が現れたら `kb generate --concept <新概念>` で生成可能になる。
