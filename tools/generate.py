#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["edge-tts>=7.2.8", "mutagen>=1.48"]
# ///
"""
thai-shadowing 音声パイプライン（Mac / Windows 共通）

  uv run tools/generate.py build              未生成・変更ありのエピソードを全部作り feed.xml / index.html を更新
  uv run tools/generate.py build S01 S02      指定エピソードだけ作る
  uv run tools/generate.py daily 2026-10-01   その日の文（content/daily/2026-10-01.json）＋復習(1・3・7日前)でエピソードを作る
  uv run tools/generate.py weekly 2026-10-05  その日までの7日分の daily をまとめた復習エピソードを作る
  uv run tools/generate.py feed               feed.xml / index.html だけ更新
  uv run tools/generate.py check              content/ の JSON を検査するだけ（TTSは呼ばない）
  オプション: --force（音声を作り直す） --dry（TTSを呼ばず構成だけ表示）

必要なもの: uv（Python と依存は自動）、ffmpeg（PATH に通っていること）
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import html
import json
import re
import os
import shutil
import subprocess
import sys
from array import array
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
AUDIO = ROOT / "audio"
CACHE = AUDIO / ".cache"
MANIFEST = AUDIO / "manifest.json"
CONFIG = ROOT / "config.json"
SAMPLE_RATE = 24000  # edge-tts の出力に合わせる
BYTES_PER_SAMPLE = 2
TZ = dt.timezone(dt.timedelta(hours=7))  # バンコク

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- 設定
DEFAULT_CONFIG = {
    "base_url": "https://eisukekorat.github.io/thai-shadowing",
    "title": "タイ語 瞬間作文",
    "subtitle": "仕事で使うタイ語を、聞いて・言って・シャドーイング",
    "description": "日本語 → 3秒で自分で言う → タイ語（ゆっくり）→ タイ語（自然速度）。日系商社の3場面（指示・客先説明・通訳）の文を毎日少しずつ。",
    "author": "eisuke",
    "email": "",
    "language": "ja",
    "voices": {"jp": "ja-JP-KeitaNeural", "th": "th-TH-NiwatNeural"},
    "rates": {"jp": "+0%", "th_slow": "-10%", "th_normal": "+0%"},
    "gaps": {"after_intro": 1.0, "after_jp": 3.0, "between_th": 1.5, "after_th": 3.0},
    "bitrate": "48k",
    "id3_version": 3,
    "intro": True,
    "review_days": [1, 3, 7],
    "review_per_day": 2,
    "target_rms_dbfs": -20.0,
    "tts_concurrency": 3,
    "audio_base_url": "",
    "proxy": "",
    "sets_pubdate": "2026-09-27T05:00:00+07:00",
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG.exists():
        user = json.loads(CONFIG.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


# ---------------------------------------------------------------- 内容の読み込み
class Episode:
    def __init__(self, ep_id: str, kind: str, path: Path, data: dict, date: dt.date | None):
        self.id = ep_id          # S01 / 2026-10-01 / W2026-40
        self.kind = kind         # set / daily / weekly
        self.path = path
        self.data = data
        self.date = date
        self.title = data.get("title", ep_id)
        self.title_th = data.get("title_th", "")
        self.sentences = [s for s in data.get("sentences", [])]
        self.extra_review: list[dict] = []  # daily に足す復習文
        self._review_attached = False

    def all_sentences(self) -> list[tuple[dict, bool]]:
        return [(s, False) for s in self.sentences] + [(s, True) for s in self.extra_review]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def discover_episodes() -> dict[str, Episode]:
    eps: dict[str, Episode] = {}
    for p in sorted((CONTENT / "sets").glob("*.json")):
        d = read_json(p)
        eps[p.stem] = Episode(p.stem, "set", p, d, None)
    for p in sorted((CONTENT / "daily").glob("*.json")):
        d = read_json(p)
        eps[p.stem] = Episode(p.stem, "daily", p, d, dt.date.fromisoformat(p.stem))
    for p in sorted((CONTENT / "weekly").glob("*.json")) if (CONTENT / "weekly").exists() else []:
        d = read_json(p)
        eps[p.stem] = Episode(p.stem, "weekly", p, d, dt.date.fromisoformat(d.get("date", "1970-01-01")))
    return eps


def attach_review(ep: Episode, cfg: dict) -> None:
    """daily エピソードに 1・3・7 日前の文を復習として足す（done: true は除く）"""
    if ep.kind != "daily" or ep.date is None or ep._review_attached:
        return
    ep._review_attached = True
    for back in cfg["review_days"]:
        prev = CONTENT / "daily" / f"{ep.date - dt.timedelta(days=back)}.json"
        if not prev.exists():
            continue
        picked = [s for s in read_json(prev).get("sentences", []) if not s.get("done")][: cfg["review_per_day"]]
        ep.extra_review.extend(picked)


# ---------------------------------------------------------------- 検査
KATAKANA_RE = re.compile(r"^[゠-ヿ゙゚ーー・ 　]+$")
LATIN_DIGIT_RE = re.compile(r"[A-Za-z0-9()\[\]\"“”'‘’]")
FORBIDDEN = ["サンワ", "Sanwa", "SANWA", "KMC"]
REQUIRED = ["id", "jp", "th", "th_tts", "reading", "note", "tags", "level"]


def check_content(eps: dict[str, Episode]) -> list[str]:
    problems: list[str] = []
    seen_ids: set[str] = set()
    for ep in eps.values():
        if ep.kind == "weekly":  # 週まとめは daily の文の再掲なので検査対象外（id が重なる）
            continue
        for i, s in enumerate(ep.sentences, 1):
            where = f"{ep.path.name}#{i}"
            for k in REQUIRED:
                if k not in s or s[k] in ("", None, []):
                    problems.append(f"{where}: {k} が空")
            sid = s.get("id", "")
            if sid in seen_ids:
                problems.append(f"{where}: id 重複 {sid}")
            seen_ids.add(sid)
            if s.get("level") not in (1, 2, 3):
                problems.append(f"{where}: level は 1〜3 ({s.get('level')!r})")
            if LATIN_DIGIT_RE.search(s.get("th_tts", "")):
                problems.append(f"{where}: th_tts に英字/数字/括弧 → {s.get('th_tts')}")
            if s.get("reading") and not KATAKANA_RE.match(s["reading"]):
                problems.append(f"{where}: reading にカタカナ以外 → {s['reading']}")
            for word in FORBIDDEN:
                for k in ("jp", "th", "note"):
                    if word in s.get(k, ""):
                        problems.append(f"{where}: {k} に実在社名らしき語 {word!r}")
            if len(s.get("note", "")) > 60:
                problems.append(f"{where}: note が長い ({len(s['note'])}字)")
    return problems


# ---------------------------------------------------------------- TTS（キャッシュつき）
def seg_key(voice: str, rate: str, text: str, rms: float) -> str:
    # キャッシュは「正規化＋トリム後」の PCM。処理を変えたら v を上げる
    return hashlib.sha1(f"v3|{rms}|{voice}|{rate}|{text}".encode("utf-8")).hexdigest()


SUBPROCESS_FLAGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def ffmpeg_bin(name: str = "ffmpeg") -> str:
    p = shutil.which(name)
    if not p and os.name == "nt":  # winget の portable 版は Links にエイリアスが置かれる（PATH 未反映のことがある）
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / f"{name}.exe"
        if cand.exists():
            p = str(cand)
    if not p:
        sys.exit(f"{name} が見つかりません。Mac: brew install ffmpeg / Windows: winget install -e --id Gyan.FFmpeg（入れた後はターミナルを開き直す）")
    return p


def mp3_to_pcm(mp3: Path) -> bytes:
    out = subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-hide_banner", "-v", "error", "-i", str(mp3), "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "pipe:1"],
        capture_output=True, check=True, **SUBPROCESS_FLAGS,
    )
    return out.stdout


def normalize(pcm: bytes, target_dbfs: float) -> bytes:
    """クリップ単位で RMS を揃える（日本語・タイ語の声の音量差をなくす）"""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if len(samples) == 0:
        return pcm
    rms = (sum(x * x for x in samples) / len(samples)) ** 0.5
    if rms < 1:
        return pcm
    target = 32768 * (10 ** (target_dbfs / 20))
    gain = target / rms
    peak = max(abs(x) for x in samples) or 1
    gain = min(gain, 32000 / peak)  # クリップ防止
    if abs(gain - 1.0) < 0.02:
        return pcm
    out = array("h", (max(-32768, min(32767, int(x * gain))) for x in samples))
    return out.tobytes()


def trim_silence(pcm: bytes, threshold_dbfs: float = -45.0, pad_ms: int = 120) -> bytes:
    """TTS クリップの前後の無音を落とす（無音の長さを config どおりにし、チャプター頭を発話に合わせる）"""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    win = SAMPLE_RATE // 100  # 10ms
    thr = 32768 * (10 ** (threshold_dbfs / 20))
    loud = []
    for i in range(0, len(samples), win):
        chunk = samples[i : i + win]
        if chunk and (sum(x * x for x in chunk) / len(chunk)) ** 0.5 > thr:
            loud.append(i)
    if not loud:
        return pcm
    pad = SAMPLE_RATE * pad_ms // 1000
    start = max(0, loud[0] - pad)
    end = min(len(samples), loud[-1] + win + pad)
    return samples[start:end].tobytes()


async def fetch_segment(voice: str, rate: str, text: str, cfg: dict, sem: asyncio.Semaphore) -> Path:
    import edge_tts

    CACHE.mkdir(parents=True, exist_ok=True)
    key = seg_key(voice, rate, text, cfg["target_rms_dbfs"])
    pcm_path = CACHE / f"{key}.pcm"
    if pcm_path.exists() and pcm_path.stat().st_size > 0:
        return pcm_path
    mp3_path = CACHE / f"{key}.mp3"
    async with sem:
        last = None
        for attempt in range(1, 6):
            try:
                await edge_tts.Communicate(text, voice=voice, rate=rate, proxy=cfg.get("proxy") or None).save(str(mp3_path))
                if mp3_path.stat().st_size < 500:
                    raise RuntimeError("出力が小さすぎる")
                await asyncio.sleep(0.3)  # 連続生成の間隔（レート制限よけ）
                break
            except Exception as e:  # noqa: BLE001
                last = e
                await asyncio.sleep(1.5 * attempt)
        else:
            raise RuntimeError(f"TTS に5回失敗: {text[:30]}… ({last})")
    pcm = trim_silence(normalize(mp3_to_pcm(mp3_path), cfg["target_rms_dbfs"]))
    pcm_path.write_bytes(pcm)
    mp3_path.unlink(missing_ok=True)
    return pcm_path


def silence(seconds: float) -> bytes:
    return b"\x00" * (int(seconds * SAMPLE_RATE) * BYTES_PER_SAMPLE)


# ---------------------------------------------------------------- エピソード構成
def plan_episode(ep: Episode, cfg: dict) -> list[dict]:
    """[{kind: tts|sil, voice, rate, text, chapter}] の並び"""
    v, r, g = cfg["voices"], cfg["rates"], cfg["gaps"]
    items = ep.all_sentences()
    plan: list[dict] = []
    if cfg.get("intro", True):
        n = len(items)
        intro = f"{ep.title}。{n}文です。日本語のあと、3秒でタイ語を言ってみてください。"
        plan.append({"kind": "tts", "voice": v["jp"], "rate": r["jp"], "text": intro, "chapter": "はじめに"})
        plan.append({"kind": "sil", "sec": g["after_intro"]})
    for n, (s, is_review) in enumerate(items, 1):
        th_tts = s.get("th_tts") or s["th"]
        tag = "（復習）" if is_review else ""
        plan.append({"kind": "tts", "voice": v["jp"], "rate": r["jp"], "text": s["jp"], "chapter": f"{n:02d}{tag} {s['jp']}"})
        plan.append({"kind": "sil", "sec": g["after_jp"]})
        plan.append({"kind": "tts", "voice": v["th"], "rate": r["th_slow"], "text": th_tts, "chapter": None})
        plan.append({"kind": "sil", "sec": g["between_th"]})
        plan.append({"kind": "tts", "voice": v["th"], "rate": r["th_normal"], "text": th_tts, "chapter": None})
        plan.append({"kind": "sil", "sec": g["after_th"]})
    return plan


def episode_hash(ep: Episode, cfg: dict) -> str:
    material = json.dumps(
        {"plan": plan_episode(ep, cfg), "bitrate": cfg["bitrate"], "rms": cfg["target_rms_dbfs"], "id3": cfg["id3_version"]},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()


async def build_episode(ep: Episode, cfg: dict, out: Path | None = None, dry: bool = False) -> dict:
    plan = plan_episode(ep, cfg)
    if dry:
        total = 0.0
        for it in plan:
            total += it["sec"] if it["kind"] == "sil" else 3.5
        print(f"[dry] {ep.id} {ep.title}: {len(ep.all_sentences())}文 / 推定 {total/60:.1f}分 / セグメント {len(plan)}")
        return {}

    sem = asyncio.Semaphore(cfg["tts_concurrency"])
    tts_items = [it for it in plan if it["kind"] == "tts"]
    paths = await asyncio.gather(*(fetch_segment(it["voice"], it["rate"], it["text"], cfg, sem) for it in tts_items))
    pcm_by_idx = dict(zip([id(it) for it in tts_items], paths))

    pcm = bytearray()
    chapters: list[tuple[int, str]] = []  # (start_ms, title)
    for it in plan:
        if it["kind"] == "sil":
            pcm += silence(it["sec"])
            continue
        if it.get("chapter"):
            chapters.append((len(pcm) // BYTES_PER_SAMPLE * 1000 // SAMPLE_RATE, it["chapter"]))
        pcm += pcm_by_idx[id(it)].read_bytes()
    duration_ms = len(pcm) // BYTES_PER_SAMPLE * 1000 // SAMPLE_RATE

    AUDIO.mkdir(parents=True, exist_ok=True)
    out = out or AUDIO / f"{ep.id}.mp3"
    tmp = out.with_suffix(".tmp.mp3")
    subprocess.run(
        [ffmpeg_bin(), "-nostdin", "-hide_banner", "-v", "error", "-y", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", cfg["bitrate"], "-id3v2_version", str(cfg["id3_version"]), str(tmp)],
        input=bytes(pcm), check=True, **SUBPROCESS_FLAGS,
    )
    write_id3(tmp, ep, chapters, duration_ms, cfg)
    tmp.replace(out)
    return {"duration_ms": duration_ms, "bytes": out.stat().st_size, "chapters": [{"ms": ms, "title": t} for ms, t in chapters]}


def write_id3(path: Path, ep: Episode, chapters: list[tuple[int, str]], duration_ms: int, cfg: dict) -> None:
    from mutagen.id3 import CHAP, CTOC, ID3, TALB, TIT2, TPE1, CTOCFlags, ID3NoHeaderError

    try:
        tags = ID3(str(path))  # ffmpeg が書いた既存タグを読み込んで中身を入れ替える
    except ID3NoHeaderError:
        tags = ID3()
    tags.clear()
    tags.add(TIT2(encoding=3, text=[episode_title(ep)]))
    tags.add(TPE1(encoding=3, text=[cfg["author"]]))
    tags.add(TALB(encoding=3, text=[cfg["title"]]))
    ids = []
    for i, (start, title) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else duration_ms
        cid = f"ch{i:03d}"
        ids.append(cid)
        tags.add(CHAP(element_id=cid, start_time=start, end_time=end, sub_frames=[TIT2(encoding=3, text=[title])]))
    tags.add(CTOC(element_id="toc", flags=CTOCFlags.TOP_LEVEL | CTOCFlags.ORDERED, child_element_ids=ids,
                  sub_frames=[TIT2(encoding=3, text=["Chapters"])]))
    tags.save(str(path), v2_version=cfg["id3_version"])


def episode_title(ep: Episode) -> str:
    if ep.kind == "set":
        return f"{ep.id} {ep.title}"
    if ep.kind == "daily":
        d = ep.date
        return f"{d.month}月{d.day}日の文 {ep.title}".strip()
    return f"週まとめ {ep.title}"


# ---------------------------------------------------------------- manifest / feed / index
def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}


def save_manifest(m: dict) -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def hms(ms: int) -> str:
    sec = ms // 1000
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def entry_items(ep: Episode | None, entry: dict) -> list[dict]:
    """音声を作った時点の文（manifest の控え）。古い manifest には無いので content から作る"""
    if entry.get("items"):
        return entry["items"]
    if ep is None:
        return []
    return [{"jp": s["jp"], "th": s["th"], "reading": s.get("reading", ""), "note": s.get("note", ""), "review": r}
            for s, r in ep.all_sentences()]


def entry_pub(ep_id: str, entry: dict, cfg: dict) -> dt.datetime:
    kind, date = entry.get("kind"), entry.get("date")
    if kind == "daily" and date:
        return dt.datetime.combine(dt.date.fromisoformat(date), dt.time(6, 0), TZ)
    if kind == "weekly" and date:
        return dt.datetime.combine(dt.date.fromisoformat(date), dt.time(7, 0), TZ)
    if re.fullmatch(r"S\d+", ep_id):
        # 一覧で S01 が一番上に来るよう、S01 を最新にして1分ずつ古くする
        return dt.datetime.fromisoformat(cfg["sets_pubdate"]) - dt.timedelta(minutes=int(ep_id[1:]))
    iso = entry.get("built_at")
    return dt.datetime.fromisoformat(iso) if iso else dt.datetime.now(TZ)


def published(eps: dict[str, Episode], m: dict, cfg: dict) -> list[tuple[str, dict, Episode]]:
    """feed / index に載せる回。content が残っていて、音声が配信先にあるもの"""
    remote = bool(cfg.get("audio_base_url"))
    out = []
    for ep_id, entry in m.items():
        ep = eps.get(ep_id)
        if ep is None or not entry.get("file"):
            continue
        if not remote and not (AUDIO / entry["file"]).exists():
            continue
        out.append((ep_id, entry, ep))
    out.sort(key=lambda t: entry_pub(t[0], t[1], cfg), reverse=True)
    return out


def audio_url(entry: dict, cfg: dict, absolute: bool) -> str:
    if cfg.get("audio_base_url"):
        return f"{cfg['audio_base_url'].rstrip('/')}/{entry['file']}"
    return f"{cfg['base_url'].rstrip('/')}/audio/{entry['file']}" if absolute else f"audio/{entry['file']}"


def show_notes_html(entry: dict, items: list[dict]) -> str:
    parts = []
    if entry.get("title_th"):
        parts.append(f"<p>{esc(entry.get('ep_title', ''))}（{esc(entry['title_th'])}）</p>")
    for n, s in enumerate(items, 1):
        tag = "（復習）" if s.get("review") else ""
        parts.append(
            f"<p><b>{n:02d}</b>{tag} {esc(s['jp'])}<br>{esc(s['th'])}<br>{esc(s.get('reading',''))}<br><i>{esc(s.get('note',''))}</i></p>"
        )
    parts.append("<p>形式: 日本語 → 3秒（自分で言う）→ タイ語（ゆっくり）→ タイ語（自然速度）</p>")
    if entry.get("chapters"):
        parts.append("<p>" + "<br>".join(f"{hms(c['ms'])} {esc(c['title'])}" for c in entry["chapters"]) + "</p>")
    return "\n".join(parts)


def short_description(entry: dict, items: list[dict], page_url: str, limit: int = 3800) -> str:
    """Apple の <description> 上限（4000バイト）に収まる短い版。全文は content:encoded と一覧ページに置く"""
    tail = f"<p>読み・解説つきの全文: {esc(page_url)}</p>"
    parts, size = [], len(tail.encode("utf-8"))
    for n, s in enumerate(items, 1):
        tag = "（復習）" if s.get("review") else ""
        line = f"<p>{n:02d}{tag} {esc(s['jp'])}<br>{esc(s['th'])}</p>"
        b = len(line.encode("utf-8"))
        if size + b > limit:
            parts.append("<p>…</p>")
            break
        parts.append(line)
        size += b
    return "".join(parts) + tail


def write_feed(eps: dict[str, Episode], m: dict, cfg: dict) -> None:
    base = cfg["base_url"].rstrip("/")
    items = []
    for ep_id, entry, ep in published(eps, m, cfg):
        its = entry_items(ep, entry)
        title = entry.get("title") or episode_title(ep)
        items.append(f"""    <item>
      <title>{esc(title)}</title>
      <itunes:title>{esc(title)}</itunes:title>
      <itunes:subtitle>{len(its)}文 ・ {esc(entry.get('ep_title') or ep.title)}</itunes:subtitle>
      <description><![CDATA[{short_description(entry, its, f"{base}/#{ep_id}")}]]></description>
      <content:encoded><![CDATA[{show_notes_html(entry, its)}]]></content:encoded>
      <enclosure url="{audio_url(entry, cfg, True)}" length="{entry['bytes']}" type="audio/mpeg"/>
      <guid isPermaLink="false">thai-shadowing-{ep_id}</guid>
      <pubDate>{format_datetime(entry_pub(ep_id, entry, cfg))}</pubDate>
      <link>{base}/#{ep_id}</link>
      <itunes:duration>{entry['duration_ms'] // 1000}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:explicit>false</itunes:explicit>
    </item>""")
    now = format_datetime(dt.datetime.now(TZ))
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{esc(cfg['title'])}</title>
    <link>{base}/</link>
    <atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>
    <language>{cfg['language']}</language>
    <description>{esc(cfg['description'])}</description>
    <itunes:subtitle>{esc(cfg['subtitle'])}</itunes:subtitle>
    <itunes:author>{esc(cfg['author'])}</itunes:author>
    <itunes:owner><itunes:name>{esc(cfg['author'])}</itunes:name>{f"<itunes:email>{esc(cfg['email'])}</itunes:email>" if cfg.get('email') else ''}</itunes:owner>
    <itunes:image href="{base}/cover.png"/>
    <image><url>{base}/cover.png</url><title>{esc(cfg['title'])}</title><link>{base}/</link></image>
    <itunes:category text="Education"><itunes:category text="Language Learning"/></itunes:category>
    <itunes:explicit>false</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <itunes:block>Yes</itunes:block>
    <lastBuildDate>{now}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
    write_atomic(ROOT / "feed.xml", feed)


def write_index(eps: dict[str, Episode], m: dict, cfg: dict) -> None:
    base = cfg["base_url"].rstrip("/")
    pubs = published(eps, m, cfg)
    sections = []
    for ep_id, entry, ep in pubs:
        rows = []
        for n, s in enumerate(entry_items(ep, entry), 1):
            tag = " <span class=tag>復習</span>" if s.get("review") else ""
            rows.append(
                f"<tr><td class=n>{n:02d}{tag}</td><td><div class=jp>{esc(s['jp'])}</div><div class=th>{esc(s['th'])}</div>"
                f"<div class=rd>{esc(s.get('reading',''))}</div><div class=nt>{esc(s.get('note',''))}</div></td></tr>"
            )
        mins = entry["duration_ms"] / 60000
        sections.append(f"""<section id="{ep_id}">
<h2>{esc(entry.get('title') or episode_title(ep))} <small>{len(rows)}文・{mins:.1f}分</small></h2>
<audio controls preload="none" src="{audio_url(entry, cfg, False)}"></audio>
<table>{''.join(rows)}</table>
</section>""")
    toc = " ・ ".join(f'<a href="#{ep_id}">{esc(ep_id)}</a>' for ep_id, _, _ in pubs)
    page = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(cfg['title'])}</title>
<style>
:root{{--bg:#fff;--fg:#1a1a1a;--mut:#666;--line:#e5e5e5;--acc:#0a66c2}}
@media(prefers-color-scheme:dark){{:root{{--bg:#111;--fg:#eee;--mut:#aaa;--line:#333;--acc:#6db3ff}}}}
body{{margin:0;padding:16px;background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans","Noto Sans Thai","Noto Sans JP",sans-serif;line-height:1.5;max-width:760px;margin:0 auto}}
h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem;border-top:1px solid var(--line);padding-top:1rem}}
small{{color:var(--mut);font-weight:normal}} audio{{width:100%;margin:.5rem 0}}
table{{width:100%;border-collapse:collapse}} td{{padding:.5rem .3rem;border-bottom:1px solid var(--line);vertical-align:top}}
td.n{{width:2.2rem;color:var(--mut)}} .th{{font-size:1.15rem}} .rd,.nt{{color:var(--mut);font-size:.85rem}}
.tag{{font-size:.7rem;color:var(--acc)}} .sub{{background:var(--line);padding:.8rem;border-radius:8px;font-size:.9rem}}
code{{user-select:all;word-break:break-all}} a{{color:var(--acc)}}
</style></head><body>
<h1>{esc(cfg['title'])}</h1>
<p>{esc(cfg['description'])}</p>
<div class=sub><b>iPhone で聴く</b>: <a href="podcast://{base.split('://',1)[1]}/feed.xml">▶ この1タップで Podcast アプリに追加</a><br>
うまく開かないときは Podcast アプリ → ライブラリ → 右上「…」→「URLで番組をフォロー」に<br><code>{base}/feed.xml</code></div>
<p>{toc}</p>
{chr(10).join(sections)}
<p><small>ここに出てくる会社・人・数字はすべて架空です。</small></p>
</body></html>
"""
    write_atomic(ROOT / "index.html", page)


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def snapshot_items(ep: Episode) -> list[dict]:
    return [{"jp": s["jp"], "th": s["th"], "reading": s.get("reading", ""), "note": s.get("note", ""), "review": r}
            for s, r in ep.all_sentences()]


def refresh_display(eps: dict[str, Episode], m: dict, cfg: dict) -> bool:
    """音声が今の content と一致している回は、表示用の控え（読み・解説・表示用タイ語・見出し）を今の内容にする。
    音声が古い回（未ビルド）は控えをそのまま残し、説明文が音声と食い違わないようにする"""
    changed = False
    for ep_id, entry in m.items():
        ep = eps.get(ep_id)
        if ep is None:
            continue
        attach_review(ep, cfg)
        if episode_hash(ep, cfg) != entry.get("hash"):
            continue
        new = {"items": snapshot_items(ep), "title": episode_title(ep), "ep_title": ep.title, "title_th": ep.title_th}
        if any(entry.get(k) != v for k, v in new.items()):
            entry.update(new)
            changed = True
    return changed


def collect_garbage(m: dict) -> list[str]:
    """manifest のどこからも指されていない mp3 を消す（feed を書き終えた後に呼ぶ）"""
    keep = {e.get("file") for e in m.values()}
    removed = []
    for f in AUDIO.glob("*.mp3"):
        if f.name not in keep:
            f.unlink()
            removed.append(f.name)
    return removed


# ---------------------------------------------------------------- weekly
def make_weekly(date: dt.date, cfg: dict, write: bool = True) -> Episode:
    wk = date.isocalendar()
    ep_id = f"W{wk[0]}-{wk[1]:02d}"
    sentences = []
    for back in range(6, -1, -1):
        d = date - dt.timedelta(days=back)
        p = CONTENT / "daily" / f"{d}.json"
        if p.exists():
            sentences += [s for s in read_json(p).get("sentences", []) if not s.get("done")]
    if not sentences:
        sys.exit(f"{date} までの7日間に daily の文がありません")
    start = date - dt.timedelta(days=6)
    data = {"set": ep_id, "title": f"{start.month}/{start.day}〜{date.month}/{date.day}", "title_th": "", "date": str(date), "sentences": sentences}
    path = CONTENT / "weekly" / f"{ep_id}.json"
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return Episode(ep_id, "weekly", path, data, date)


# ---------------------------------------------------------------- main
async def run_build(targets: list[Episode], cfg: dict, force: bool, dry: bool) -> None:
    m = load_manifest()
    for ep in targets:
        h = episode_hash(ep, cfg)
        fname = f"{ep.id}-{h[:8]}.mp3"  # 内容が変わるとファイル名が変わる（GUID は固定のまま）
        if not force and not dry and m.get(ep.id, {}).get("file") == fname and (AUDIO / fname).exists():
            print(f"= {ep.id} 変更なし")
            continue
        print(f"> {ep.id} {episode_title(ep)} ({len(ep.all_sentences())}文) を生成中…")
        info = await build_episode(ep, cfg, out=AUDIO / fname, dry=dry)
        if dry:
            continue
        prev = m.get(ep.id, {})
        m[ep.id] = {
            "hash": h,
            "file": fname,
            "kind": ep.kind,
            "date": str(ep.date) if ep.date else None,
            "built_at": prev.get("built_at") or dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "updated_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "duration_ms": info["duration_ms"],
            "bytes": info["bytes"],
            "title": episode_title(ep),
            "ep_title": ep.title,
            "title_th": ep.title_th,
            "chapters": info["chapters"],
            "items": snapshot_items(ep),
        }
        save_manifest(m)  # 古い mp3 はここでは消さない（feed を書いた後にまとめて消す）
        print(f"  ✓ {info['duration_ms']/60000:.1f}分 / {info['bytes']//1024}KB / チャプター{len(info['chapters'])} → audio/{fname}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["build", "daily", "weekly", "feed", "check"])
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    cfg = load_config()
    eps = discover_episodes()

    problems = check_content(eps)
    if a.command == "check":
        print("\n".join(problems) if problems else "OK: 問題なし")
        print(f"episodes={len(eps)} sentences={sum(len(e.sentences) for e in eps.values())}")
        sys.exit(1 if problems else 0)
    if problems:
        print("内容に問題があります（check で確認）:\n  " + "\n  ".join(problems[:20]))
        sys.exit(1)

    try:
        if a.command == "build":
            unknown = [t for t in a.targets if t not in eps]
            if unknown:
                sys.exit(f"そのエピソードはありません: {', '.join(unknown)}")
            targets = [eps[t] for t in a.targets] if a.targets else list(eps.values())
            for ep in targets:
                attach_review(ep, cfg)
            asyncio.run(run_build(targets, cfg, a.force, a.dry))
        elif a.command == "daily":
            if not a.targets:
                sys.exit("日付を指定: daily 2026-10-01")
            date = a.targets[0]
            if date not in eps:
                sys.exit(f"content/daily/{date}.json がありません（先に文を書いて）")
            attach_review(eps[date], cfg)
            asyncio.run(run_build([eps[date]], cfg, a.force, a.dry))
        elif a.command == "weekly":
            date = dt.date.fromisoformat(a.targets[0]) if a.targets else dt.datetime.now(TZ).date()
            ep = make_weekly(date, cfg, write=not a.dry)
            eps[ep.id] = ep
            asyncio.run(run_build([ep], cfg, a.force, a.dry))
    finally:
        # 途中で失敗しても、作り終えた回までは feed に反映し、feed が消えたファイルを指さないようにする
        if not a.dry:
            m = load_manifest()
            if refresh_display(eps, m, cfg):
                save_manifest(m)
            write_feed(eps, m, cfg)
            write_index(eps, m, cfg)
            removed = collect_garbage(m)
            print(f"feed.xml / index.html 更新（エピソード {len(published(eps, m, cfg))}本）"
                  + (f"・古い音声 {len(removed)} 本を削除" if removed else ""))


if __name__ == "__main__":
    main()
