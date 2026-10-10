"""Daily Video Bot: makes N vertical videos every day.
Default delivery: GitHub Release (open on phone, tap video, download -> Gallery).
Telegram is optional (only used if its secrets are set).

Pipeline per video: script (Gemini or scripts.json) -> voice (edge-tts)
-> stock clips (Pexels or Pixabay, both optional) -> ffmpeg compose (9:16 + captions) -> Telegram.
"""
import asyncio, json, os, random, subprocess, sys, textwrap, time
from pathlib import Path
import requests

def env(name, default):
    """Like os.getenv, but an empty value (unset GitHub variable) also uses the default."""
    return (os.getenv(name) or "").strip() or default


VIDEOS_PER_RUN = int(env("VIDEOS_PER_RUN", "6"))
NICHE = env("NICHE", "motivation, self improvement and healthy habits")
VOICE = env("VOICE", "en-US-AndrewNeural")
CAPTIONS = env("CAPTIONS", "1") == "1"
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
PEXELS_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "")  # alternative to Pexels (both optional)
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"  # no network: test the video builder only

W, H, FPS = 1080, 1920, 30
WORK, OUT = Path("work"), Path("out")
WORK.mkdir(exist_ok=True); OUT.mkdir(exist_ok=True)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:3])} failed:\n{r.stderr[-1500:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(path)]).strip())


# ---------- 1. scripts ----------
def gemini_scripts(n):
    prompt = (
        f"Write {n} different short-video scripts for the niche: {NICHE}. "
        "Each voiceover must be 70-110 words, a strong hook in the first sentence, "
        "plain spoken language, no emojis, no hashtags, no made-up statistics. "
        'Return ONLY a JSON array of objects with keys: "title" (max 70 chars), '
        '"voiceover", "keywords" (3 simple visual search words in English).'
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    r = requests.post(url, params={"key": GEMINI_KEY}, timeout=90, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0}})
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text.replace("```json", "").replace("```", "").strip())
    return [d for d in data if d.get("voiceover")][:n]


def get_scripts(n):
    if GEMINI_KEY and not DRY_RUN:
        try:
            s = gemini_scripts(n)
            if s:
                print(f"Gemini gave {len(s)} scripts"); return s
        except Exception as e:
            print("Gemini failed, using scripts.json:", e)
    pool = json.loads(Path("scripts.json").read_text(encoding="utf-8"))
    random.shuffle(pool)
    return [pool[i % len(pool)] for i in range(n)]


# ---------- 2. voice ----------
async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE).save(str(path))


def make_voice(text, path):
    if DRY_RUN:  # silent audio of a plausible length
        secs = max(8, len(text.split()) / 2.5)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", f"{secs:.1f}", "-c:a", "libmp3lame", str(path)])
        return
    asyncio.run(_tts(text, path))


# ---------- 3. background clips ----------
def pexels_clips(keywords, k=4):
    got = []
    hdr = {"Authorization": PEXELS_KEY}
    for kw in (keywords or ["nature"]) * 2:
        if len(got) >= k:
            break
        try:
            r = requests.get("https://api.pexels.com/videos/search", headers=hdr, timeout=30,
                             params={"query": kw, "orientation": "portrait", "per_page": 8})
            r.raise_for_status()
            vids = r.json().get("videos", [])
            random.shuffle(vids)
            for v in vids:
                files = [f for f in v["video_files"] if f.get("height", 0) >= 1080]
                files.sort(key=lambda f: f["height"])
                if not files:
                    continue
                p = WORK / f"clip_{v['id']}.mp4"
                if not p.exists():
                    p.write_bytes(requests.get(files[0]["link"], timeout=120).content)
                if p not in got:
                    got.append(p); break
        except Exception as e:
            print("pexels skip:", kw, e)
    return got


def pixabay_clips(keywords, k=4):
    got = []
    for kw in (keywords or ["nature"]) * 2:
        if len(got) >= k:
            break
        try:
            r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                             params={"key": PIXABAY_KEY, "q": kw, "per_page": 20, "safesearch": "true"})
            r.raise_for_status()
            hits = r.json().get("hits", [])
            random.shuffle(hits)
            for h in hits:
                vids = h.get("videos", {})
                v = vids.get("medium") or vids.get("large") or vids.get("small")
                if not v or not v.get("url"):
                    continue
                p = WORK / f"pix_{h['id']}.mp4"
                if not p.exists():
                    p.write_bytes(requests.get(v["url"], timeout=120).content)
                if p not in got:
                    got.append(p); break
        except Exception as e:
            print("pixabay skip:", kw, e)
    return got


def get_clips(keywords):
    if DRY_RUN:
        return []
    clips = []
    if PEXELS_KEY:
        clips = pexels_clips(keywords)
    if not clips and PIXABAY_KEY:
        clips = pixabay_clips(keywords)
    return clips


# ---------- 4. compose ----------
def pick_font():
    try:
        f = run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"]).strip()
        if f and Path(f).exists():
            return f
    except Exception:
        pass
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def build_background(clips, total, tag):
    segs = []
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
          f"fps={FPS},format=yuv420p")
    if not clips:  # solid colour fallback so you still get a video
        colour = random.choice(["0x16213e", "0x1b4332", "0x3a0ca3", "0x7f1d1d", "0x0f4c5c"])
        p = WORK / f"{tag}_bg.mp4"
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={W}x{H}:r={FPS}",
             "-t", f"{total:.2f}", "-pix_fmt", "yuv420p", "-c:v", "libx264",
             "-preset", "veryfast", str(p)])
        return p
    per = total / len(clips)
    for i, c in enumerate(clips):
        s = WORK / f"{tag}_seg{i}.mp4"
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", str(c), "-t", f"{per + 0.05:.2f}",
             "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", str(s)])
        segs.append(s)
    lst = WORK / f"{tag}_list.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    bg = WORK / f"{tag}_bg.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(bg)])
    return bg


def phrases(text, words=4):
    w = text.split()
    return [" ".join(w[i:i + words]) for i in range(0, len(w), words)]


def compose(bg, audio, script, out_path, tag):
    total = duration(audio)
    filters = ["format=yuv420p"]
    if CAPTIONS:
        font, ph = pick_font(), phrases(script["voiceover"])
        step = total / max(len(ph), 1)
        for i, p in enumerate(ph):
            tf = WORK / f"{tag}_cap{i}.txt"
            tf.write_text("\n".join(textwrap.wrap(p.upper(), 18)), encoding="utf-8")
            filters.append(
                f"drawtext=fontfile='{font}':textfile='{tf}':fontsize=84:fontcolor=white:"
                f"borderw=6:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y=h*0.62:"
                f"enable='between(t,{i * step:.2f},{(i + 1) * step:.2f})'")
    run(["ffmpeg", "-y", "-i", str(bg), "-i", str(audio), "-vf", ",".join(filters),
         "-map", "0:v", "-map", "1:a", "-t", f"{total:.2f}", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(out_path)])


# ---------- 5. send ----------
def send_telegram(path, caption):
    if not (TG_TOKEN and TG_CHAT):
        print("No Telegram secrets; skipping send"); return
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", timeout=300,
                          data={"chat_id": TG_CHAT, "caption": caption[:900],
                                "supports_streaming": "true", "width": W, "height": H},
                          files={"video": f})
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text[:300]}")


def main():
    scripts = get_scripts(VIDEOS_PER_RUN)
    sent = 0
    for i, s in enumerate(scripts, 1):
        tag = f"v{i}_{int(time.time())}"
        try:
            print(f"[{i}/{len(scripts)}] {s.get('title', 'video')}")
            audio = WORK / f"{tag}.mp3"
            make_voice(s["voiceover"], audio)
            total = duration(audio)
            clips = get_clips(s.get("keywords", []))
            bg = build_background(clips, total, tag)
            final = OUT / f"{tag}.mp4"
            compose(bg, audio, s, final, tag)
            send_telegram(final, s.get("title", ""))  # only if Telegram secrets exist
            with open(OUT / "titles.txt", "a", encoding="utf-8") as tf:
                tf.write(f"{final.name}: {s.get('title', '')}\n")
            sent += 1
            print("done:", final, f"({final.stat().st_size / 1e6:.1f} MB)")
        except Exception as e:
            print("FAILED video", i, e)
    print(f"Finished: {sent}/{len(scripts)} videos")
    if sent == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
