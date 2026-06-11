# Git Repository Analyzer

## 対象

GitHubリポジトリ、GitLabリポジトリ、その他コードリポジトリ。

## 抽出対象

- リポジトリ名とdescription
- READMEの概要
- Topics / tags（リポジトリに設定されているもの）
- 主要言語
- スター数（わかる場合）
- 主要ファイル構成（src/, lib/, docs/など）
- ビルド・実行方法（READMEから）

## 分析のポイント

1. リポジトリが何をするものかを要約する
2. なぜこのリポジトリが自分にとって重要かを書く
3. 最大5個の重要ポイント（主要機能、特徴、技術スタックなど）を抽出する
4. リポジトリが実装または利用する技術・概念をprimary_conceptsに挙げる
5. 関連技術や代替ツールをcandidate_conceptsに挙げる
6. 使用言語、フレームワーク、依存ライブラリ、CIツールをentitiesに挙げる

## 注意

- コード全文は保存しない。README、概要、用途、主要構造、参照URLを保存する。
- 別途shallow cloneやGit submoduleが必要な場合はsource URLを参照する。

## 入力形式

```
Title: <リポジトリ名>
Source: <URL>
Description: <description>

<README内容>
```
