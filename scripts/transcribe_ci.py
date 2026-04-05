#!/usr/bin/env python3
"""
GitHub Actions 转录脚本
支持两种模式:
  1. 手动触发: EPISODE_URL 环境变量指定单集
  2. 定时触发: RSS_FEEDS 环境变量指定要监控的 feed 列表，自动发现新集
"""

import os
import sys
import re
import json
import math
import hashlib
import requests
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET

CHUNK_SIZE_MB = 24
STATE_FILE = "state/processed.json"


# ============================================================
#  页面解析
# ============================================================

def parse_xiaoyuzhou(episode_url: str) -> dict:
    """从小宇宙页面提取音频 URL + 元数据。"""
    from bs4 import BeautifulSoup

    print(f"  解析: {episode_url}")
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    resp = requests.get(episode_url, headers=headers, timeout=30)
    resp.raise_for_status()

    info = {"title": "", "audio_url": "", "shownotes": "", "source_url": episode_url}
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            props = data.get("props", {}).get("pageProps", {})
            ep = props.get("episode", props.get("episodeData", props))
            if isinstance(ep, dict):
                enc = ep.get("enclosure", {})
                info["audio_url"] = (
                    (enc.get("url") if isinstance(enc, dict) else None)
                    or ep.get("mediaUrl") or ep.get("audioUrl") or ""
                )
                info["title"] = ep.get("title", "")
                info["shownotes"] = ep.get("shownotes", ep.get("description", ""))
                if not info["audio_url"]:
                    mk = ep.get("mediaKey")
                    if mk:
                        info["audio_url"] = f"https://media.xyzcdn.net/{mk}"
        except (json.JSONDecodeError, KeyError):
            pass

    if not info["audio_url"]:
        for pat in [r'https?://[^"\'<>\s]*?\.mp3[^"\'<>\s]*',
                    r'https?://media\.xyzcdn\.net/[^"\'<>\s]+']:
            m = re.findall(pat, resp.text)
            if m:
                info["audio_url"] = m[0]
                break

    if not info["title"]:
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og:
            info["title"] = og.get("content", "untitled")

    return info


def parse_rss_entry(entry_xml) -> dict:
    """从 RSS <item> 解析出单集信息。"""
    ns = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}
    title = entry_xml.findtext("title", "untitled")
    enc = entry_xml.find("enclosure")
    audio_url = enc.get("url", "") if enc is not None else ""
    desc = entry_xml.findtext("description", "")
    guid = entry_xml.findtext("guid", audio_url)
    return {
        "title": title,
        "audio_url": audio_url,
        "shownotes": desc,
        "source_url": entry_xml.findtext("link", ""),
        "guid": guid,
    }


# ============================================================
#  RSS 新集检测
# ============================================================

def check_rss_feeds(feeds: list[str]) -> list[dict]:
    """检查 RSS feeds 中是否有未处理的新集。"""
    processed = load_state()
    new_episodes = []

    for feed_url in feeds:
        feed_url = feed_url.strip()
        if not feed_url:
            continue
        print(f"  检查 RSS: {feed_url}")
        try:
            resp = requests.get(feed_url, timeout=30,
                                headers={"User-Agent": "PodcastBot/1.0"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            items = root.findall(".//item")
            # 只取最新的 3 集
            for item in items[:3]:
                info = parse_rss_entry(item)
                ep_id = hashlib.md5(info["guid"].encode()).hexdigest()[:12]
                if ep_id not in processed:
                    print(f"    新集: {info['title']}")
                    info["_id"] = ep_id
                    new_episodes.append(info)
        except Exception as e:
            print(f"    ⚠ RSS 获取失败: {e}")

    return new_episodes


def load_state() -> dict:
    """加载已处理记录。"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    """保存已处理记录。"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
#  下载 + 切分
# ============================================================

def download_audio(url: str, out_dir: str) -> str:
    """下载音频文件。"""
    ext = ".mp3"
    for e in (".m4a", ".mp4", ".wav", ".ogg", ".flac"):
        if e in url:
            ext = e
            break

    path = os.path.join(out_dir, f"episode{ext}")
    resp = requests.get(url, stream=True, timeout=60,
                        headers={"User-Agent": "PodcastBot/1.0"})
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))

    with open(path, "wb") as f:
        dl = 0
        for chunk in resp.iter_content(8192):
            f.write(chunk)
            dl += len(chunk)
            if total and dl % (1024 * 512) == 0:
                print(f"\r  下载: {dl * 100 // total}%", end="", flush=True)
    print(f"\n  ✓ {os.path.getsize(path) / 1048576:.1f} MB")
    return path


def split_audio(path: str, chunk_mb: int = CHUNK_SIZE_MB) -> list:
    """超过 API 大小限制时自动切分。"""
    size_mb = os.path.getsize(path) / 1048576
    if size_mb <= chunk_mb:
        return [path]

    print(f"  文件 {size_mb:.0f}MB > {chunk_mb}MB，切分中...")
    from pydub import AudioSegment

    audio = AudioSegment.from_file(path)
    n = math.ceil(size_mb / chunk_mb)
    chunk_ms = len(audio) // n

    src = Path(path)
    suffix = src.suffix.lower()
    export_format = {
        ".m4a": "mp4",
        ".mp4": "mp4",
        ".mp3": "mp3",
        ".wav": "wav",
        ".ogg": "ogg",
        ".flac": "flac",
    }.get(suffix, suffix.lstrip(".") or "mp3")

    chunks = []
    for i in range(n):
        seg = audio[i * chunk_ms: min((i + 1) * chunk_ms, len(audio))]
        cp = src.with_name(f"{src.stem}_p{i}{suffix}")
        seg.export(str(cp), format=export_format)
        chunks.append(str(cp))
    print(f"  ✓ 切分为 {n} 段")
    return chunks


# ============================================================
#  Whisper API 转录
# ============================================================

def _groq_request(path: str, key: str, lang: str, max_retries: int = 5):
    """发送单个 Groq 转录请求，带指数退避重试。"""
    import time
    for attempt in range(max_retries):
        with open(path, "rb") as f:
            resp = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (os.path.basename(path), f)},
                data={"model": "whisper-large-v3", "language": lang,
                      "response_format": "verbose_json",
                      "timestamp_granularities[]": "segment"},
                timeout=300,
            )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", 0))
            backoff = max(wait, 2 ** attempt * 3)
            print(f"    ⏳ 429 限流，{backoff}s 后重试 ({attempt+1}/{max_retries})...")
            time.sleep(backoff)
            continue
        sys.exit(f"❌ Groq: {resp.status_code} {resp.text[:300]}")
    sys.exit("❌ Groq: 重试次数用尽")


def transcribe_groq(paths: list, lang: str = "zh") -> list:
    import time

    key = os.environ.get("GROQ_API_KEY")
    if not key:
        sys.exit("❌ GROQ_API_KEY 未设置")

    # 节流: Groq 免费额度 ~18 req/min，间隔 3.5s 可稳定跑满
    MIN_INTERVAL = 3.5
    last_req_time = 0.0

    segments, offset = [], 0.0
    for i, p in enumerate(paths):
        print(f"  Groq whisper-large-v3: 第 {i+1}/{len(paths)} 段...")

        # 节流等待
        elapsed = time.time() - last_req_time
        if elapsed < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - elapsed)

        last_req_time = time.time()
        data = _groq_request(p, key, lang)

        for s in data.get("segments", []):
            segments.append({"start": s["start"] + offset,
                             "end": s["end"] + offset, "text": s["text"]})
        offset += data.get("duration", 0)
        print(f"    ✓ +{data.get('duration', 0):.0f}s")
    return segments


def transcribe_openai(paths: list, lang: str = "zh") -> list:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("❌ OPENAI_API_KEY 未设置")

    segments, offset = [], 0.0
    for i, p in enumerate(paths):
        print(f"  OpenAI whisper-1: 第 {i+1}/{len(paths)} 段...")
        with open(p, "rb") as f:
            resp = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (os.path.basename(p), f)},
                data={"model": "whisper-1", "language": lang,
                      "response_format": "verbose_json",
                      "timestamp_granularities[]": "segment"},
                timeout=600,
            )
        if resp.status_code != 200:
            sys.exit(f"❌ OpenAI: {resp.status_code} {resp.text[:300]}")

        data = resp.json()
        for s in data.get("segments", []):
            segments.append({"start": s["start"] + offset,
                             "end": s["end"] + offset, "text": s["text"]})
        offset += data.get("duration", 0)
        print(f"    ✓ +{data.get('duration', 0):.0f}s")
    return segments


# ============================================================
#  Markdown 生成
# ============================================================

def _ts(sec):
    h, r = divmod(int(sec), 3600)
    m, s = divmod(r, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def build_markdown(segments: list, info: dict) -> str:
    dur = segments[-1]["end"] if segments else 0
    chars = sum(len(s["text"]) for s in segments)
    lines = [
        f"# {info['title']}\n",
        f"| 属性 | 值 |",
        f"|------|------|",
        f"| 来源 | {info.get('source_url', '')} |",
        f"| 时长 | {dur/60:.0f} 分钟 |",
        f"| 字数 | {chars:,} |",
        f"| 转录时间 | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} |",
        "",
    ]
    if info.get("shownotes"):
        lines += ["<details><summary>Show Notes</summary>\n",
                   info["shownotes"].strip(), "\n</details>\n"]

    lines += ["---\n", "## 转录全文\n"]

    block, buf = -1, []
    for seg in segments:
        b = int(seg["start"] // 300)
        if b != block:
            if buf:
                lines.append("".join(buf) + "\n")
            block = b
            lines.append(f"### [{_ts(block * 300)}]\n")
            buf = []
        buf.append(seg["text"])
    if buf:
        lines.append("".join(buf) + "\n")

    return "\n".join(lines)


# ============================================================
#  单集处理流水线
# ============================================================

def process_episode(info: dict, provider: str, tmp_dir: str) -> str | None:
    """处理单集: 下载 → 切分 → 转录 → 写入 Markdown。返回输出路径。"""
    if not info.get("audio_url"):
        print(f"  ⚠ 跳过（无音频URL）: {info.get('title')}")
        return None

    print(f"\n{'='*50}")
    print(f"处理: {info['title']}")
    print(f"{'='*50}")

    # 下载
    audio_path = download_audio(info["audio_url"], tmp_dir)

    # 切分
    chunks = split_audio(audio_path)

    # 转录
    print(f"  转录 (provider: {provider})...")
    if provider == "groq":
        segments = transcribe_groq(chunks)
    else:
        segments = transcribe_openai(chunks)

    if not segments:
        print(f"  ⚠ 转录结果为空")
        return None

    print(f"  ✓ {len(segments)} 个片段, {sum(len(s['text']) for s in segments)} 字")

    # 生成 Markdown
    md = build_markdown(segments, info)
    safe = re.sub(r'[\\/:*?"<>|\s]+', '_', info["title"])[:60].strip("_")
    date = datetime.now().strftime("%Y-%m-%d")
    filename = f"{date}_{safe}.md"
    out_path = os.path.join("transcripts", filename)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"  ✓ 输出: {out_path}")

    # 清理临时文件
    for c in chunks:
        try:
            os.remove(c)
        except OSError:
            pass
    try:
        os.remove(audio_path)
    except OSError:
        pass

    return out_path


# ============================================================
#  主入口
# ============================================================

def main():
    episode_url = os.environ.get("EPISODE_URL", "").strip()
    provider = os.environ.get("WHISPER_PROVIDER", "groq")
    rss_feeds_raw = os.environ.get("RSS_FEEDS", "").strip()

    os.makedirs("transcripts", exist_ok=True)
    tmp = "/tmp/podcast_dl"
    os.makedirs(tmp, exist_ok=True)

    episodes = []

    # 模式 1: 手动指定 URL
    if episode_url:
        print("[模式] 手动触发")
        info = parse_xiaoyuzhou(episode_url)
        if info.get("audio_url"):
            episodes.append(info)
        else:
            sys.exit("❌ 无法解析音频 URL")

    # 模式 2: RSS 自动发现
    elif rss_feeds_raw:
        print("[模式] RSS 自动检查")
        feeds = [f.strip() for f in rss_feeds_raw.split(",") if f.strip()]
        episodes = check_rss_feeds(feeds)
        if not episodes:
            print("没有新集")
            return

    else:
        sys.exit("❌ 请设置 EPISODE_URL 或 RSS_FEEDS")

    # 处理每一集
    state = load_state()
    results = []

    for ep in episodes:
        out = process_episode(ep, provider, tmp)
        if out:
            results.append(out)
            ep_id = ep.get("_id") or hashlib.md5(
                ep.get("guid", ep["audio_url"]).encode()
            ).hexdigest()[:12]
            state[ep_id] = {
                "title": ep["title"],
                "file": out,
                "date": datetime.utcnow().isoformat(),
            }

    save_state(state)
    print(f"\n🎉 完成，共处理 {len(results)} 集")


if __name__ == "__main__":
    main()
