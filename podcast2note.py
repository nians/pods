#!/usr/bin/env python3
"""
本地播客转录工具（使用本地 Whisper 或云端 API）

用法:
  # 本地 Whisper 转录（需要 GPU 或耐心等 CPU）
  python podcast2note.py https://www.xiaoyuzhoufm.com/episode/xxx

  # 使用 Groq API（快，推荐）
  python podcast2note.py --api groq https://www.xiaoyuzhoufm.com/episode/xxx

  # 转录本地音频文件
  python podcast2note.py --audio podcast.mp3

  # 上传到 GitHub Gist
  python podcast2note.py --upload gist https://www.xiaoyuzhoufm.com/episode/xxx

  # 上传到 GitHub Repo
  python podcast2note.py --upload repo:user/podcast-notes https://www.xiaoyuzhoufm.com/episode/xxx

依赖:
  pip install requests beautifulsoup4
  pip install faster-whisper          # 本地转录
  pip install pydub                   # API 模式切分大文件
"""

import os, sys, re, json, math, time, argparse, hashlib, requests
from pathlib import Path
from datetime import datetime


def parse_page(url):
    from bs4 import BeautifulSoup
    print(f"[1] 解析页面...")
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    info = {"title": "", "audio_url": "", "shownotes": "", "source_url": url}
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            p = d.get("props",{}).get("pageProps",{})
            ep = p.get("episode", p.get("episodeData", p))
            if isinstance(ep, dict):
                enc = ep.get("enclosure",{})
                info["audio_url"] = (enc.get("url") if isinstance(enc,dict) else None) \
                    or ep.get("mediaUrl") or ep.get("audioUrl") or ""
                info["title"] = ep.get("title","")
                info["shownotes"] = ep.get("shownotes", ep.get("description",""))
                if not info["audio_url"]:
                    mk = ep.get("mediaKey")
                    if mk: info["audio_url"] = f"https://media.xyzcdn.net/{mk}"
        except: pass
    if not info["audio_url"]:
        for pat in [r'https?://[^"\'<>\s]*?\.mp3', r'https?://media\.xyzcdn\.net/[^"\'<>\s]+']:
            found = re.findall(pat, r.text)
            if found: info["audio_url"] = found[0]; break
    if not info["title"]:
        soup = BeautifulSoup(r.text, "html.parser")
        og = soup.find("meta", property="og:title")
        if og: info["title"] = og.get("content","untitled")
    if not info["audio_url"]:
        print("❌ 无法提取音频 URL")
        print("备选: 小宇宙 app → 分享到微信 → 电脑 Chrome 打开 → 右键下载")
        sys.exit(1)
    print(f"  标题: {info['title']}")
    return info


def download(url, out_dir):
    print(f"[2] 下载音频...")
    ext = ".mp3"
    for e in (".m4a",".mp4",".wav"): 
        if e in url: ext = e; break
    path = os.path.join(out_dir, f"episode{ext}")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        print(f"  ✓ 已存在，跳过"); return path
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    total = int(r.headers.get("content-length",0))
    dl, t0 = 0, time.time()
    with open(path,"wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk); dl += len(chunk)
            if total:
                print(f"\r  {dl*100//total}% {dl//1048576}/{total//1048576}MB", end="", flush=True)
    print(f"\n  ✓ {os.path.getsize(path)/1048576:.1f} MB")
    return path


def split_if_needed(path, limit_mb=24):
    sz = os.path.getsize(path)/1048576
    if sz <= limit_mb: return [path]
    from pydub import AudioSegment
    print(f"  切分 {sz:.0f}MB...")
    audio = AudioSegment.from_file(path)
    n = math.ceil(sz/limit_mb)
    chunk_ms = len(audio)//n
    out = []
    for i in range(n):
        seg = audio[i*chunk_ms:min((i+1)*chunk_ms, len(audio))]
        cp = path.replace(".", f"_p{i}.")
        seg.export(cp, format=path.rsplit(".",1)[-1])
        out.append(cp)
    return out


def transcribe_local(audio_path, model_size="large-v3", lang="zh"):
    from faster_whisper import WhisperModel
    print(f"[3] 本地 Whisper ({model_size})...")
    try: model = WhisperModel(model_size, device="auto", compute_type="float16")
    except: model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio_path, language=lang, beam_size=5,
                                   vad_filter=True, vad_parameters={"min_silence_duration_ms":500})
    result = []
    for s in segs:
        result.append({"start":s.start,"end":s.end,"text":s.text})
        pct = s.end/info.duration*100 if info.duration else 0
        print(f"\r  {pct:.0f}%", end="", flush=True)
    print()
    return result


def transcribe_api(paths, provider="groq", lang="zh"):
    print(f"[3] API 转录 ({provider})...")
    if provider == "groq":
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        key = os.environ.get("GROQ_API_KEY","")
        model = "whisper-large-v3"
    else:
        url = "https://api.openai.com/v1/audio/transcriptions"
        key = os.environ.get("OPENAI_API_KEY","")
        model = "whisper-1"
    if not key: sys.exit(f"❌ 请设置 {provider.upper()}_API_KEY")
    segments, offset = [], 0.0
    for i,p in enumerate(paths):
        print(f"  第 {i+1}/{len(paths)} 段...")
        with open(p,"rb") as f:
            r = requests.post(url, headers={"Authorization":f"Bearer {key}"},
                files={"file":(os.path.basename(p),f)},
                data={"model":model,"language":lang,
                      "response_format":"verbose_json",
                      "timestamp_granularities[]":"segment"}, timeout=300)
        if r.status_code == 429:
            wait = int(r.headers.get("retry-after",60))
            print(f"    限流，等待 {wait}s..."); time.sleep(wait)
            with open(p,"rb") as f:
                r = requests.post(url, headers={"Authorization":f"Bearer {key}"},
                    files={"file":(os.path.basename(p),f)},
                    data={"model":model,"language":lang,
                          "response_format":"verbose_json",
                          "timestamp_granularities[]":"segment"}, timeout=300)
        if r.status_code != 200: sys.exit(f"❌ {r.status_code} {r.text[:300]}")
        d = r.json()
        for s in d.get("segments",[]):
            segments.append({"start":s["start"]+offset,"end":s["end"]+offset,"text":s["text"]})
        offset += d.get("duration",0)
        print(f"    ✓ +{d.get('duration',0):.0f}s")
    return segments


def _ts(sec):
    h,r = divmod(int(sec),3600); m,s = divmod(r,60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_markdown(segments, info, out_dir):
    print(f"[4] 生成 Markdown...")
    dur = segments[-1]["end"] if segments else 0
    chars = sum(len(s["text"]) for s in segments)
    lines = [f"# {info['title']}\n",
        f"| 属性 | 值 |",f"|------|------|",
        f"| 来源 | {info.get('source_url','')} |",
        f"| 时长 | {dur/60:.0f} 分钟 |",
        f"| 字数 | {chars:,} |",
        f"| 转录时间 | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",""]
    if info.get("shownotes"):
        lines += ["<details><summary>Show Notes</summary>\n", info["shownotes"].strip(), "\n</details>\n"]
    lines += ["---\n","## 转录全文\n"]
    block, buf = -1, []
    for seg in segments:
        b = int(seg["start"]//300)
        if b != block:
            if buf: lines.append("".join(buf)+"\n")
            block = b; lines.append(f"### [{_ts(block*300)}]\n"); buf = []
        buf.append(seg["text"])
    if buf: lines.append("".join(buf)+"\n")

    safe = re.sub(r'[\\/:*?"<>|\s]+','_', info["title"])[:60].strip("_")
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(out_dir, f"{date}_{safe}.md")
    with open(path,"w",encoding="utf-8") as f: f.write("\n".join(lines))

    # 同时输出纯文本
    txt_path = path.replace(".md",".txt")
    with open(txt_path,"w",encoding="utf-8") as f:
        for s in segments: f.write(f"[{_ts(s['start'])}] {s['text'].strip()}\n")

    print(f"  ✓ {path}")
    print(f"  ✓ {txt_path}")
    return path


def upload_github(filepath, mode="gist", repo=""):
    token = os.environ.get("GITHUB_TOKEN","")
    if not token:
        print("⚠ GITHUB_TOKEN 未设置，跳过上传")
        print("  export GITHUB_TOKEN=ghp_xxx")
        return ""
    headers = {"Authorization":f"token {token}","Accept":"application/vnd.github.v3+json"}
    filename = os.path.basename(filepath)
    content = open(filepath,"r",encoding="utf-8").read()

    if mode == "gist":
        print(f"[5] 上传 Gist...")
        r = requests.post("https://api.github.com/gists", headers=headers,
            json={"description":f"Podcast: {filename}","public":False,
                  "files":{filename:{"content":content}}}, timeout=30)
        if r.status_code == 201:
            d = r.json()
            raw = d["files"][filename]["raw_url"]
            print(f"  ✓ {d['html_url']}")
            print(f"  ✓ Raw: {raw}")
            return raw
        else:
            print(f"  ❌ {r.status_code} {r.text[:200]}")
    else:
        import base64
        print(f"[5] 上传 Repo: {repo}...")
        date = datetime.now().strftime("%Y-%m-%d")
        remote = f"transcripts/{date}_{filename}"
        sha = None
        ex = requests.get(f"https://api.github.com/repos/{repo}/contents/{remote}", headers=headers)
        if ex.status_code == 200: sha = ex.json().get("sha")
        payload = {"message":f"Add {filename}",
                   "content":base64.b64encode(content.encode()).decode()}
        if sha: payload["sha"] = sha
        r = requests.put(f"https://api.github.com/repos/{repo}/contents/{remote}",
            headers=headers, json=payload, timeout=30)
        if r.status_code in (200,201):
            dl = r.json()["content"]["download_url"]
            print(f"  ✓ {dl}")
            return dl
        else:
            print(f"  ❌ {r.status_code} {r.text[:200]}")
    return ""


def main():
    ap = argparse.ArgumentParser(description="播客转录工具")
    ap.add_argument("url", nargs="?", help="小宇宙单集 URL")
    ap.add_argument("--audio","-a", help="本地音频文件")
    ap.add_argument("--api", choices=["groq","openai"], help="使用云端 API（默认本地 Whisper）")
    ap.add_argument("--model","-m", default="large-v3",
                    choices=["tiny","base","small","medium","large-v2","large-v3"])
    ap.add_argument("--lang","-l", default="zh")
    ap.add_argument("--output","-o", default="./podcast_output")
    ap.add_argument("--upload","-u", help="上传: gist 或 repo:owner/name")
    args = ap.parse_args()

    if not args.url and not args.audio: ap.print_help(); sys.exit(1)
    os.makedirs(args.output, exist_ok=True)

    if args.audio:
        info = {"title": Path(args.audio).stem, "source_url": "", "shownotes": ""}
        audio_path = args.audio
    else:
        info = parse_page(args.url)
        audio_path = download(info["audio_url"], args.output)

    if args.api:
        chunks = split_if_needed(audio_path)
        segments = transcribe_api(chunks, args.api, args.lang)
    else:
        segments = transcribe_local(audio_path, args.model, args.lang)

    md_path = to_markdown(segments, info, args.output)

    if args.upload:
        mode = "gist"
        repo = ""
        if args.upload.startswith("repo:"):
            mode = "repo"; repo = args.upload[5:]
        raw = upload_github(md_path, mode, repo)
        if raw:
            print(f"\n💡 在 Claude 中使用:")
            print(f"   请帮我分析这个播客: {raw}")

    print(f"\n🎉 完成!")


if __name__ == "__main__":
    main()
