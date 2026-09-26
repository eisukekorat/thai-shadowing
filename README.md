# タイ語 瞬間作文（thai-shadowing）

仕事で使うタイ語を「聞いて → 3秒で自分で言う → タイ語を2回聞いてシャドーイング」する、自分専用ポッドキャスト。
音声は GitHub Pages で配信し、iPhone の Podcast アプリで聴く。

## 聴き方（iPhone）
- 一番早い: iPhone の Safari で https://eisukekorat.github.io/thai-shadowing/ を開き「この1タップで Podcast アプリに追加」を押す（`podcast://` リンク）
- 手動: Podcast アプリ → ライブラリ → 右上「…」→「URLで番組をフォロー」→ `https://eisukekorat.github.io/thai-shadowing/feed.xml`
- 以後、新しいエピソードは自動で届く。再生画面のチャプター一覧で文ごとに戻れる。

一覧ページ: https://eisukekorat.github.io/thai-shadowing/ （文とカタカナ・解説・ブラウザ再生）

## 1文の形式
日本語 → 無音3秒（自分で言う）→ タイ語（少しゆっくり）→ 1.5秒 → タイ語（自然速度）→ 3秒

## 中身
- `content/sets/S01〜S10.json` … 3場面の定型セット（通訳の繋ぎ／指示・依頼／客先説明／数字ドリル／品質対応／会議／電話／職場会話／翻訳対応／商社語彙）
- `content/daily/YYYY-MM-DD.json` … その日「言えなかった文」。1・3・7日前の文が復習として自動で付く
- `content/weekly/` … 週まとめ（`weekly` コマンドで生成）
- `content/STYLE.md` … 文体・カタカナ・音声用表記のルール

## コマンド（Mac / Windows 共通）
```
uv run tools/generate.py check              内容の検査だけ
uv run tools/generate.py build              未生成・変更ありを全部作る（feed.xml / index.html も更新）
uv run tools/generate.py daily 2026-10-01   その日のエピソード
uv run tools/generate.py weekly 2026-10-05  週まとめ
```
必要なもの: [uv](https://docs.astral.sh/uv/)（Python と依存を自動で用意）と ffmpeg。Windows の初回設定は `WINDOWS_SETUP.md`。

## 仕組み
- 音声合成: [edge-tts](https://github.com/rany2/edge-tts)（Microsoft Edge のニューラル音声・無料・キー不要）。日本語 Keita／タイ語 Niwat
- 結合と mp3 化: ffmpeg。文ごとに ID3 チャプター（mutagen）
- 生成済みの音声は `audio/` にコミット（再生成は変更があった文だけ）。`audio/.cache/` はクリップの一時置き場（git 管理外）
- ファイル名は `S01-<内容ハッシュ>.mp3`。文を直すとファイル名が変わり、GUID は固定のまま（Podcast アプリでは同じエピソードとして扱われ、未ダウンロードの端末は新しい音声を取る）
- リポが 500MB を超えたら mp3 を GitHub Releases に移す（`config.json` の `audio_base_url` を変えるだけ）
- 会社ネットがプロキシ必須なら `config.json` に `"proxy": "http://..."`

## 注意
- 公開リポジトリなので、実在の社名・顧客名・人名・金額は入れない（すべて架空・一般形）
- edge-tts は非公式ツール。止まったら `config.json` の声を変えるか、macOS `say -v Kanya` / Google Cloud TTS 無料枠に切り替える
