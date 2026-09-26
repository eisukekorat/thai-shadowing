# thai-shadowing — 自分専用タイ語ポッドキャスト（Claude 向けの掟）

## これは何
「日本語 → 3秒 → タイ語×2」の音声を作り、GitHub Pages で配信し、iPhone の Podcast アプリで聴く。
本人＝英介さん（日系商社のバンコク勤務・タイ語初中級）。目的は仕事の3場面（指示・客先説明・通訳）で喋れること。

## 毎日の流れ（朝の定時タスク／「今日の文」と言われたとき）
1. `inbox.md` を読む。英介さんが日本語で書いた「言えなかった文」が並ぶ。空なら何もしないで終了
2. `content/STYLE.md` のルールで各行をタイ語化し、`content/daily/YYYY-MM-DD.json` を作る
   - 形式は `content/sets/S01.json` と同じ。`set` は日付、`title` は短い見出し（例「納期の相談」）
   - 1日 3〜8 文。多ければ残りは翌日に回す（inbox に残す）
   - 実在の社名・顧客名・人名・金額は一般化する（「A社」「お客様」「約○○」）。日本語側も自然な話し言葉に整える
3. `uv run tools/generate.py check` → 問題なし → `uv run tools/generate.py daily YYYY-MM-DD`
4. `inbox.md` の処理済み行を消す（文は JSON に残っている）
5. `git add -A && git commit -m "daily: YYYY-MM-DD" && git push`（本人確認は不要）
6. 報告は3行以内：文数・タイトル・一覧ページのURL

## ほかの依頼
- 「週まとめ」→ `uv run tools/generate.py weekly YYYY-MM-DD`（直近7日の daily を1本に）→ commit / push
- セットの文を直す → `content/sets/*.json` を編集 → `uv run tools/generate.py build` → commit / push（変わった文の音声だけ作り直る）
- 覚えた文を外す → その文に `"done": true` を付ける（復習と週まとめから外れる）

## 掟
- 実行時に従量課金APIを呼ばない（edge-tts は無料）。止まったら README の代替へ切り替えを提案する
- `config.json` の声・間・速度は本人が決める。勝手に変えない
- 文は「バンコクの日系商社のタイ人社員が実際に言う」自然さを最優先。教科書調・直訳調にしない。文末は ครับ
- カタカナ読みは声調記号なし・長母音ー。note は40字以内で「なぜその言い方か」
- `audio/*.mp3` と `audio/manifest.json` はコミットする。`audio/.cache/` は git 管理外
- Windows での初回セットアップは `WINDOWS_SETUP.md`
