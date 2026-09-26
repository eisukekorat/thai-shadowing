# 会社 Windows での初回セットアップ（1回だけ・10分）

毎日の生成はこのPC（常時ON）でやる。Mac は不要。

## 1. 道具を入れる（PowerShell を開いて1行ずつ・管理者不要）
```powershell
winget install -e --id astral-sh.uv        # uv: Python と依存を自動で用意する道具（Python 本体は入れなくてよい）
winget install -e --id Gyan.FFmpeg         # ffmpeg: 音声の結合と mp3 化
[Environment]::SetEnvironmentVariable('PYTHONUTF8','1','User')   # 日本語・タイ語の文字化け防止（Python 3.14 以前の保険）
```
入れたら **PowerShell を開き直して** 確認:
```powershell
where.exe uv
where.exe ffmpeg
```
両方パスが出ればOK。出ない場合: winget は `%LOCALAPPDATA%\Microsoft\WinGet\Links\` にエイリアスを置く仕組みで、まれにリンクが作られない既知の不具合がある。そのときは `%LOCALAPPDATA%\Microsoft\WinGet\Links` をユーザーの PATH に足す（スクリプト側も ffmpeg はこの場所を自動で探す）。

## 2. リポジトリを持ってくる
```powershell
gh auth status           # 未ログインなら gh auth login（ブラウザで認証）
cd $HOME\Documents
git clone https://github.com/eisukekorat/thai-shadowing.git
cd thai-shadowing
uv run tools/generate.py check    # 初回は Python と依存を自動で入れる（ネット必須）→ 「OK: 問題なし」
```
会社のネットで edge-tts が通るかの確認（wss://speech.platform.bing.com への接続）:
```powershell
uv run tools/generate.py build S01 --force   # 1分弱で audio/S01.mp3 が作り直される
git checkout -- audio feed.xml index.html    # 試しの変更は戻す
```
プロキシ必須の環境で失敗するときは `config.json` に `"proxy": "http://..."` を足す（未対応なら Claude に頼めば10行で足せる）。

## 3. 毎日の使い方（これが本線・自動化なし）
Claude Code をこのフォルダで開いて、言えなかった文を投げるだけ:
```
/thai 明日の午前中までに見積書を送ってください。この型番は生産終了なので代替品を提案します。
```
→ タイ語化 → 音声 → push まで約1分。iPhone の Podcast アプリに新エピソードが届く。
帰る前に1回、が習慣にしやすい。スマホでメモした文は `inbox.md` に貼って `/thai` と言えば同じ。

## 4. （任意）朝の自動処理 — Claude デスクトップアプリのローカルタスク
Mac の /asa と同じ仕組み。**アプリが開いていて PC が起きている間だけ**動く（寝ていた分は復帰時に1回だけ追い付き実行）。
Claude Code で頼む:
> このリポで毎朝 6:00 に「inbox.md を処理して今日のエピソードを作る」ローカルタスクを作って。プロンプトは次のとおり。

```
~/Documents/thai-shadowing で作業。git pull してから、CLAUDE.md「毎日の流れ」のとおり inbox.md を処理して今日（バンコク時間）のエピソードを作り push する。inbox.md が空なら何もしないで終了。
```
作ったら **Run now を1回押して、出てくる許可を「always allow」にしておく**（そうしないと無人実行時に権限待ちで止まる）。

## 困ったとき
- 「TTS に5回失敗」: 数分待って再実行。続くなら edge-tts の更新（`uv run --refresh tools/generate.py check`）→ それでも駄目なら README の代替へ
- 文字化け: 手順1の PYTHONUTF8 を設定したか確認（設定後は PowerShell を開き直す）
- `git push` が拒否される: `gh auth status`。Claude Code の Bash から push する場合も同じ認証を使う
- 音声は作れたのに iPhone に来ない: Podcast アプリでその番組を開いて下に引っ張って更新
