"""Daily Cartoon Video Bot: makes N colourful animated cartoon videos (30+ sec) every day.
Default delivery: GitHub Release (open on phone, tap video, download -> Gallery).
Telegram is optional (only used if its secrets are set).

Pipeline per video: script with scenes+moods (Gemini or scripts.json) -> voice with
word timings (edge-tts) -> cartoon animation + kinetic captions (cartoon.py) -> mp4.
"""
import asyncio, json, os, random, re, subprocess, sys, time
from pathlib import Path
import requests

import cartoon


def env(name, default=""):
    """Like os.getenv, but an empty value (unset GitHub variable) also uses the default."""
    return (os.getenv(name) or "").strip() or default


VIDEOS_PER_RUN = int(env("VIDEOS_PER_RUN", "6"))
NICHE = env("NICHE", "motivation, self improvement and healthy habits")
VOICE = env("VOICE", "en-US-AndrewNeural")
CAPTIONS = env("CAPTIONS", "1") == "1"
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_KEY = env("GEMINI_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")
DRY_RUN = env("DRY_RUN", "0") == "1"  # no network: test the animation only
MIN_SECONDS = 30.5

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
def clean_script(d):
    scenes = []
    for s in d.get("scenes", []):
        text = str(s.get("text", "")).strip()
        if not text:
            continue
        mood = str(s.get("mood", "happy")).lower()
        icon = str(s.get("icon", "")).lower()
        scenes.append({"text": text,
                       "mood": mood if mood in cartoon.MOODS else "happy",
                       "icon": icon if icon in cartoon.ICONS else random.choice(cartoon.ICONS)})
    words = sum(len(s["text"].split()) for s in scenes)
    if len(scenes) < 4 or words < 60:
        return None
    return {"title": str(d.get("title", "Video"))[:90], "scenes": scenes}


def gemini_scripts(n):
    prompt = (
        f"Write {n} different short cartoon-video scripts for the niche: {NICHE}. "
        "Each script has 8 to 10 scenes. Each scene is ONE spoken sentence of 10 to 16 words "
        "(100 to 130 words per script in total). Scene 1 is a strong curious hook. Use simple "
        "spoken language, a friendly tone, no emojis, no hashtags, no invented statistics. "
        "Wrap 1 or 2 important words per scene in asterisks like *this*. "
        "Give each scene a mood from: happy, excited, curious, surprised, sad, calm, funny "
        "(vary them to match the meaning) and an icon from: heart, star, sun, moon, bulb, rocket, cloud, coin. "
        'Return ONLY a JSON array. Each item: {"title": "max 70 chars", "scenes": '
        '[{"text": "...", "mood": "...", "icon": "..."}]}.'
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    r = requests.post(url, params={"key": GEMINI_KEY}, timeout=120, json={
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 1.0,
                             "maxOutputTokens": 8192}})
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    data = json.loads(text.replace("```json", "").replace("```", "").strip())
    return [x for x in (clean_script(d) for d in data) if x][:n]


def get_scripts(n):
    out = []
    if GEMINI_KEY and not DRY_RUN:
        try:
            out = gemini_scripts(n)
            print(f"Gemini gave {len(out)} scripts")
        except Exception as e:
            print("Gemini failed, using scripts.json:", e)
    if len(out) < n:
        pool = [x for x in (clean_script(d) for d in
                            json.loads(Path("scripts.json").read_text(encoding="utf-8"))) if x]
        random.shuffle(pool)
        while len(out) < n:
            out.append(pool[len(out) % len(pool)])
    return out[:n]


# ---------- 2. voice + word timings ----------
async def _tts(text, path, rate):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, VOICE, rate=rate, boundary="WordBoundary")
    except TypeError:
        comm = edge_tts.Communicate(text, VOICE, rate=rate)
    events = []
    with open(path, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                events.append((ch["offset"] / 1e7, ch["duration"] / 1e7))
    return events


def make_voice(text, path):
    """Returns (audio_seconds, word_events). Slows the voice a little if it is under ~31s."""
    if DRY_RUN:
        secs = max(31.0, len(text.split()) / 2.4)
        run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
             "-t", f"{secs:.1f}", "-c:a", "libmp3lame", str(path)])
        return duration(path), []
    ev = asyncio.run(_tts(text, path, "+0%"))
    d = duration(path)
    if d < 31:
        pct = min(30, int((1 - d / 32.5) * 100) + 1)
        ev = asyncio.run(_tts(text, path, f"-{pct}%"))
        d = duration(path)
    return d, ev


def build_words(script, audio_dur, events):
    words = []
    for si, sc in enumerate(script["scenes"]):
        for tok in sc["text"].split():
            clean = tok.replace("*", "").strip()
            if clean:
                words.append({"w": clean, "emph": "*" in tok, "scene": si})
    n = len(words)
    if events:
        m = len(events)
        for i, w in enumerate(words):
            st, du = events[min(m - 1, int(i * m / n))]
            w["start"], w["dur"] = st, du
    else:  # estimate from word lengths
        lead, tail = 0.25, 0.3
        weights = [len(w["w"]) + 2 for w in words]
        total_w, acc = sum(weights), 0
        span = max(1.0, audio_dur - lead - tail)
        for w, wt in zip(words, weights):
            w["start"] = lead + span * acc / total_w
            acc += wt
            w["dur"] = span * wt / total_w
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < n else audio_dur
        w["end"] = max(w["start"] + 0.08, min(w["start"] + w["dur"], nxt))
    return words


# ---------- 3. send (optional) ----------
def send_telegram(path, caption):
    if not (TG_TOKEN and TG_CHAT):
        return
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendVideo", timeout=300,
                          data={"chat_id": TG_CHAT, "caption": caption[:900], "supports_streaming": "true",
                                "width": cartoon.W, "height": cartoon.H},
                          files={"video": f})
    if not r.ok:
        raise RuntimeError(f"Telegram error: {r.text[:300]}")


def main():
    scripts = get_scripts(VIDEOS_PER_RUN)
    sent = 0
    for i, s in enumerate(scripts, 1):
        tag = f"v{i}_{int(time.time())}"
        try:
            t0 = time.time()
            print(f"[{i}/{len(scripts)}] {s['title']}")
            text = " ".join(w for sc in s["scenes"] for w in sc["text"].replace("*", "").split())
            audio = WORK / f"{tag}.mp3"
            adur, events = make_voice(text, audio)
            words = build_words(s, adur, events)
            total = max(adur + 1.0, MIN_SECONDS)
            show = cartoon.Show(s["title"], s["scenes"], words, total,
                                seed=abs(hash(s["title"] + tag)) % 10**8, caps=CAPTIONS)
            final = OUT / f"{tag}.mp4"
            cartoon.render_video(show, audio, final, total)
            send_telegram(final, s["title"])
            with open(OUT / "titles.txt", "a", encoding="utf-8") as tf:
                tf.write(f"{final.name} ({total:.0f}s): {s['title']}\n")
            sent += 1
            print(f"done: {final} {total:.0f}s {final.stat().st_size / 1e6:.1f} MB in {time.time() - t0:.0f}s")
        except Exception as e:
            print("FAILED video", i, repr(e))
    print(f"Finished: {sent}/{len(scripts)} videos")
    if sent == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
