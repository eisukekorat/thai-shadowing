---
name: thai
description: 「今日の文」を受け取って、その日のタイ語エピソード（content/daily/YYYY-MM-DD.json → 音声 → push）を作る。「/thai 明日までに見積を送って」「今日の文: …」「inbox を処理して」で発火。
---

# /thai — 今日のエピソードを作る

引数（$ARGUMENTS）に日本語の文があればそれを、無ければ `inbox.md` を入力にする。

手順は CLAUDE.md「毎日の流れ」のとおり:
1. 文を `content/STYLE_daily.md` のルールでタイ語化し `content/daily/<今日の日付>.json` を書く（既にあれば追記。詳細は `content/STYLE.md`）
2. `uv run tools/generate.py check` → `uv run tools/generate.py daily <日付>`
3. inbox.md の処理済み行を消す
4. `git add -A && git commit -m "daily: <日付>" && git push`
5. 3行で報告（文数・タイトル・ https://eisukekorat.github.io/thai-shadowing/ ）

日付はバンコク時間（UTC+7）。実在の社名・人名・金額は一般化する。
