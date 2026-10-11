"""Cartoon renderer: colourful animated scenes + a talking mascot + kinetic captions.

Everything is drawn with Pillow (no stock footage, no extra services).
Frames are piped straight into ffmpeg.
"""
import bisect, math, os, random, subprocess, tempfile
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, SS = 720, 1280, 24, 2
INK = (43, 45, 66)


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(c, k):  # k<1 darker, k>1 lighter
    if k <= 1:
        return tuple(int(v * k) for v in c)
    return tuple(int(v + (255 - v) * (k - 1)) for v in c)


MOODS = {
    "happy":    dict(g=[("#FFE66D", "#FF9F1C"), ("#7BDFF2", "#B2F7EF"), ("#FFD6E0", "#FFB5A7")], e=1.0),
    "excited":  dict(g=[("#FF5D8F", "#FF9E00"), ("#F72585", "#7209B7"), ("#FF6B6B", "#FFD93D")], e=1.5),
    "curious":  dict(g=[("#4CC9F0", "#4361EE"), ("#A0E7E5", "#B4F8C8")], e=0.7),
    "surprised": dict(g=[("#FFD166", "#EF476F"), ("#06D6A0", "#118AB2")], e=1.1),
    "sad":      dict(g=[("#8EA7E9", "#5C6BC0"), ("#A8B5C8", "#6C7A96")], e=0.25),
    "calm":     dict(g=[("#A8E6CF", "#56CCF2"), ("#C3B1E1", "#8EC5FC")], e=0.45),
    "funny":    dict(g=[("#FF9A9E", "#FAD0C4"), ("#F9F871", "#4FFBDF"), ("#FF8FAB", "#FFD670")], e=1.25),
}
ICONS = ["heart", "star", "sun", "moon", "bulb", "rocket", "cloud", "coin"]
BODY_COLS = ["#FF6B6B", "#4D96FF", "#6BCB77", "#FFB703", "#B983FF", "#FF8FAB", "#2EC4B6"]
CAP_STYLES = {  # (fill, stroke) pairs per mood family
    "bright": [("#FFE600", "#2B2D42"), ("#FFFFFF", "#FF3D81"), ("#00F5D4", "#12355B"),
               ("#FF7AA2", "#3A0CA3"), ("#FFB703", "#6A040F"), ("#FFFFFF", "#3A86FF")],
    "soft": [("#FFFFFF", "#3A5A98"), ("#FFF1A8", "#5C6BC0"), ("#E0F7FA", "#2B2D42")],
}
EMPH_STYLE = [("#FFFFFF", "#E5195E"), ("#FFE600", "#D00000"), ("#B6FF3B", "#1B4332")]


@lru_cache(maxsize=None)
def font(size):
    for p in (os.getenv("FONT_PATH", ""),
              "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if p and os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def ease_out_back(x):
    x = max(0.0, min(1.0, x)); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def ease_out_cubic(x):
    x = max(0.0, min(1.0, x)); return 1 - (1 - x) ** 3


def ease_out_bounce(x):
    x = max(0.0, min(1.0, x)); n1, d1 = 7.5625, 2.75
    if x < 1 / d1: return n1 * x * x
    if x < 2 / d1: x -= 1.5 / d1; return n1 * x * x + 0.75
    if x < 2.5 / d1: x -= 2.25 / d1; return n1 * x * x + 0.9375
    x -= 2.625 / d1; return n1 * x * x + 0.984375


def rot(pts, cx, cy, a):
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


def star_pts(cx, cy, r, a=0.0, n=5, inner=0.45):
    pts = []
    for i in range(n * 2):
        rr = r if i % 2 == 0 else r * inner
        ang = a - math.pi / 2 + i * math.pi / n
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    return pts


def heart_pts(cx, cy, r, a=0.0):
    pts = []
    for k in range(40):
        t = k / 40 * 2 * math.pi
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * r / 17, cy + y * r / 17))
    return rot(pts, cx, cy, a)


class FG:
    """Transparent super-sampled layer (draw in 720x1280 coordinates)."""

    def __init__(self):
        self.im = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)

    def ell(self, cx, cy, rx, ry, fill=None, outline=None, w=0):
        s = SS
        self.d.ellipse([(cx - rx) * s, (cy - ry) * s, (cx + rx) * s, (cy + ry) * s],
                       fill=fill, outline=outline, width=max(1, int(w * s)) if outline else 0)

    def poly(self, pts, fill=None, outline=None, w=0):
        s = SS
        sp = [(x * s, y * s) for x, y in pts]
        if fill:
            self.d.polygon(sp, fill=fill)
        if outline:
            self.d.line(sp + [sp[0]], fill=outline, width=max(1, int(w * s)), joint="curve")

    def line(self, pts, fill, w):
        s = SS
        self.d.line([(x * s, y * s) for x, y in pts], fill=fill, width=max(1, int(w * s)), joint="curve")

    def arc(self, cx, cy, rx, ry, a0, a1, fill, w):
        s = SS
        self.d.arc([(cx - rx) * s, (cy - ry) * s, (cx + rx) * s, (cy + ry) * s], a0, a1,
                   fill=fill, width=max(1, int(w * s)))

    def rrect(self, x0, y0, x1, y1, r, fill=None, outline=None, w=0):
        s = SS
        self.d.rounded_rectangle([x0 * s, y0 * s, x1 * s, y1 * s], r * s, fill=fill,
                                 outline=outline, width=max(1, int(w * s)) if outline else 0)

    def result(self):
        return self.im.resize((W, H), Image.BOX)


def gradient(c1, c2):
    mask = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), c2), Image.new("RGB", (W, H), c1), mask)


# ---------------------------------------------------------------- icons
def draw_icon(fg, kind, x, y, r, t):
    a = math.sin(t * 2.2) * 0.25
    if kind == "heart":
        fg.poly(heart_pts(x, y, r, a), fill=(255, 64, 100, 255), outline=INK, w=6)
        fg.ell(x - r * 0.35, y - r * 0.3, r * 0.14, r * 0.1, fill=(255, 255, 255, 200))
    elif kind == "sun":
        for k in range(12):
            ang = t * 1.2 + k * math.pi / 6
            fg.line([(x + math.cos(ang) * r * 1.05, y + math.sin(ang) * r * 1.05),
                     (x + math.cos(ang) * r * 1.5, y + math.sin(ang) * r * 1.5)], (255, 183, 3, 255), 9)
        fg.ell(x, y, r, r, fill=(255, 214, 10, 255), outline=INK, w=6)
        fg.arc(x, y + 4, r * 0.5, r * 0.4, 20, 160, INK, 5)
        fg.ell(x - r * 0.3, y - r * 0.15, 5, 7, fill=INK); fg.ell(x + r * 0.3, y - r * 0.15, 5, 7, fill=INK)
    elif kind == "moon":
        fg.ell(x, y, r, r, fill=(255, 224, 102, 255), outline=INK, w=6)
        fg.ell(x + r * 0.55, y - r * 0.2, r * 0.85, r * 0.85, fill=(0, 0, 0, 0))
        fg.poly(star_pts(x + r * 1.1, y - r * 0.9, r * 0.3, t), fill=(255, 255, 255, 255))
    elif kind == "bulb":
        for k in range(7):
            ang = -math.pi * 0.95 + k * math.pi * 0.9 / 6
            fg.line([(x + math.cos(ang) * r * 1.2, y - r * 0.1 + math.sin(ang) * r * 1.2),
                     (x + math.cos(ang) * r * 1.6, y - r * 0.1 + math.sin(ang) * r * 1.6)],
                    (255, 214, 10, 255), 8)
        fg.ell(x, y - r * 0.1, r, r, fill=(255, 236, 110, 255), outline=INK, w=6)
        fg.rrect(x - r * 0.45, y + r * 0.8, x + r * 0.45, y + r * 1.3, 8, fill=(170, 175, 190, 255), outline=INK, w=5)
        fg.arc(x, y, r * 0.4, r * 0.4, 200, 340, (255, 140, 0, 255), 5)
    elif kind == "rocket":
        fl = 0.6 + 0.4 * math.sin(t * 30)
        fg.poly([(x - r * 0.35, y + r * 0.9), (x, y + r * (1.5 + 0.4 * fl)), (x + r * 0.35, y + r * 0.9)],
                fill=(255, 140, 0, 255), outline=INK, w=5)
        fg.poly([(x - r * 0.5, y + r * 0.3), (x - r * 1.0, y + r * 1.0), (x - r * 0.4, y + r * 0.9)],
                fill=(239, 71, 111, 255), outline=INK, w=5)
        fg.poly([(x + r * 0.5, y + r * 0.3), (x + r * 1.0, y + r * 1.0), (x + r * 0.4, y + r * 0.9)],
                fill=(239, 71, 111, 255), outline=INK, w=5)
        fg.ell(x, y, r * 0.55, r * 1.05, fill=(245, 245, 250, 255), outline=INK, w=6)
        fg.poly([(x - r * 0.5, y - r * 0.5), (x, y - r * 1.4), (x + r * 0.5, y - r * 0.5)],
                fill=(239, 71, 111, 255), outline=INK, w=5)
        fg.ell(x, y - r * 0.1, r * 0.25, r * 0.25, fill=(76, 201, 240, 255), outline=INK, w=5)
    elif kind == "cloud":
        for dx, dy, rr in ((-0.7, 0.1, 0.55), (0, -0.25, 0.75), (0.75, 0.1, 0.55), (0.1, 0.25, 0.6)):
            fg.ell(x + dx * r, y + dy * r, rr * r, rr * r, fill=(255, 255, 255, 255), outline=INK, w=5)
        for dx, dy, rr in ((-0.7, 0.1, 0.5), (0, -0.25, 0.7), (0.75, 0.1, 0.5), (0.1, 0.25, 0.55)):
            fg.ell(x + dx * r, y + dy * r, rr * r, rr * r, fill=(255, 255, 255, 255))
    elif kind == "coin":
        sx = abs(math.cos(t * 3)) * 0.9 + 0.1
        fg.ell(x, y, r * sx, r, fill=(255, 195, 0, 255), outline=INK, w=6)
        fg.ell(x, y, r * 0.7 * sx, r * 0.7, outline=(200, 140, 0, 255), w=5)
    else:  # star
        fg.poly(star_pts(x, y, r * 1.1, a * 2), fill=(255, 214, 10, 255), outline=INK, w=6)


# ---------------------------------------------------------------- mascot
def draw_mascot(fg, cx, cy, t, mood, speak, body, ears, blink):
    e = MOODS[mood]["e"]
    b = math.sin(t * (4 + 2 * e))
    oy = -abs(b) * 34 * e
    sy = 1 + 0.035 * math.sin(t * 5 * e + 1)
    sx = 2 - sy
    bodyc = body + (255,)
    dark = mix(body, 0.8) + (255,)
    light = mix(body, 1.55) + (255,)
    gy = cy + 196
    fg.ell(cx, gy, 190 * (1 - 0.12 * abs(b)), 26, fill=(0, 0, 0, 55))
    top = cy + oy - 185 * sy
    # ears / antenna behind the body
    if ears == "cat":
        for sgn in (-1, 1):
            fg.poly([(cx + sgn * 40, top + 40), (cx + sgn * 150, top - 55), (cx + sgn * 150, top + 90)],
                    fill=bodyc, outline=INK + (255,), w=7)
            fg.poly([(cx + sgn * 80, top + 45), (cx + sgn * 135, top - 15), (cx + sgn * 135, top + 70)],
                    fill=(255, 170, 190, 255))
    elif ears == "bear":
        for sgn in (-1, 1):
            fg.ell(cx + sgn * 112, top + 30, 52, 52, fill=bodyc, outline=INK + (255,), w=7)
            fg.ell(cx + sgn * 112, top + 34, 28, 28, fill=(255, 170, 190, 255))
    elif ears == "antenna":
        wob = math.sin(t * 6) * 14
        fg.line([(cx, top + 20), (cx + wob * 0.5, top - 50), (cx + wob, top - 85)], INK + (255,), 9)
        fg.ell(cx + wob, top - 95, 24, 24, fill=(255, 214, 10, 255), outline=INK + (255,), w=6)
    # arms
    if mood == "sad":
        hands = [(cx - 168, cy + oy + 135), (cx + 168, cy + oy + 135)]
    else:
        w1 = math.sin(t * 7 * e + 1) * 38
        hands = [(cx - 215, cy + oy + 60 + w1 * 0.5), (cx + 215, cy + oy - 70 + w1)]
    for sgn, (hx, hy) in zip((-1, 1), hands):
        fg.line([(cx + sgn * 150, cy + oy + 25), (hx, hy)], INK + (255,), 26)
        fg.line([(cx + sgn * 150, cy + oy + 25), (hx, hy)], bodyc, 15)
        fg.ell(hx, hy, 27, 27, fill=bodyc, outline=INK + (255,), w=6)
    # feet
    for sgn in (-1, 1):
        fg.ell(cx + sgn * 72, cy + 186 + min(0, oy) * 0.15, 54, 30, fill=dark, outline=INK + (255,), w=6)
    # body + belly
    fg.ell(cx, cy + oy, 168 * sx, 185 * sy, fill=bodyc, outline=INK + (255,), w=8)
    fg.ell(cx, cy + oy + 62, 112 * sx, 100 * sy, fill=light)
    fg.ell(cx - 98, cy + oy - 120, 30, 16, fill=(255, 255, 255, 90))
    # face
    ex, ey = 60, cy + oy - 52
    er = 48 if mood == "surprised" else 42
    for sgn in (-1, 1):
        x = cx + sgn * ex
        if blink or mood == "calm":
            fg.arc(x, ey + 6, er * 0.8, er * 0.6, 200, 340, INK + (255,), 8) if mood == "calm" else \
                fg.line([(x - er * 0.7, ey + 4), (x + er * 0.7, ey + 4)], INK + (255,), 8)
        else:
            fg.ell(x, ey, er, er * 1.18, fill=(255, 255, 255, 255), outline=INK + (255,), w=6)
            px, py = math.sin(t * 1.3) * 8, math.cos(t * 1.7) * 6
            fg.ell(x + px, ey + py, er * 0.46, er * 0.55, fill=INK + (255,))
            fg.ell(x + px - 6, ey + py - 9, er * 0.15, er * 0.15, fill=(255, 255, 255, 255))
            if mood == "excited":
                fg.poly(star_pts(x + px + 8, ey + py + 6, er * 0.22, t * 3), fill=(255, 230, 0, 255))
        # eyebrows
        if mood == "sad":
            by = ey - er * 1.3
            fg.line([(x - sgn * 34, by - 10), (x + sgn * 34, by + 12)], INK + (255,), 8)
        elif mood == "curious" and sgn == 1:
            fg.arc(x, ey - er * 1.3 - 6, er * 0.95, er * 0.5, 200, 340, INK + (255,), 8)
        elif mood in ("surprised", "excited"):
            fg.arc(x, ey - er * 1.45 - 6, er * 0.95, er * 0.5, 200, 340, INK + (255,), 8)
        elif mood != "calm":
            fg.arc(x, ey - er * 1.3, er * 0.9, er * 0.4, 205, 335, INK + (255,), 7)
    # cheeks
    for sgn in (-1, 1):
        fg.ell(cx + sgn * 108, cy + oy + 6, 24, 15, fill=(255, 120, 150, 150))
    # mouth
    my = cy + oy + 34
    m = speak
    if m > 0.08:
        ry = 10 + 38 * m
        fg.ell(cx, my + ry * 0.4, 34 + 14 * m, ry, fill=(150, 30, 55, 255), outline=INK + (255,), w=6)
        fg.ell(cx, my + ry * 1.05, 22 + 8 * m, ry * 0.45, fill=(255, 120, 140, 255))
    elif mood == "sad":
        fg.arc(cx, my + 30, 36, 24, 200, 340, INK + (255,), 8)
    elif mood == "surprised":
        fg.ell(cx, my + 14, 22, 28, fill=(150, 30, 55, 255), outline=INK + (255,), w=6)
    elif mood == "funny":
        fg.arc(cx, my - 8, 46, 38, 15, 165, INK + (255,), 8)
        fg.ell(cx + 14, my + 38, 16, 18 + 4 * math.sin(t * 9), fill=(255, 110, 140, 255), outline=INK + (255,), w=5)
    else:
        fg.arc(cx, my - 6, 44, 34, 15, 165, INK + (255,), 8)
    if mood == "sad":  # falling tear
        k = (t * 0.9) % 1
        fg.ell(cx - ex - 8, ey + 52 + k * 120, 9, 14, fill=(120, 200, 255, int(255 * (1 - k))))
    if mood == "excited":  # sparkles around the head
        for i in range(4):
            ang = t * 2 + i * math.pi / 2
            fg.poly(star_pts(cx + math.cos(ang) * 235, cy + oy - 190 + math.sin(ang) * 60, 20, t * 4),
                    fill=(255, 245, 120, 255))


# ---------------------------------------------------------------- scenes
class Scene:
    def __init__(self, mood, icon, rnd, idx):
        self.mood, self.icon, self.idx = mood, icon, idx
        c1, c2 = rnd.choice(MOODS[mood]["g"])
        self.c1, self.c2 = hexrgb(c1), hexrgb(c2)
        self.bg = gradient(self.c1, self.c2)
        self.pattern = rnd.choice(["rays", "dots", "stripes"])
        self.shapes = [(rnd.choice(["circle", "star", "ring"]), rnd.uniform(0, W), rnd.uniform(0, H),
                        rnd.uniform(14, 40), rnd.uniform(18, 60), rnd.uniform(0, 6.28),
                        rnd.choice([(255, 255, 255, 110), (255, 255, 255, 70), mix(self.c1, 1.4) + (130,)]))
                       for _ in range(11)]
        self.side = rnd.choice([-1, 1])
        self.start = 0.0


def draw_back(fg, sc, t):
    if sc.pattern == "rays":
        cx0, cy0 = W / 2, H * 0.56
        base = t * 0.25
        for k in range(0, 16, 2):
            a0 = base + k * math.pi / 8
            a1 = a0 + math.pi / 8
            fg.poly([(cx0, cy0), (cx0 + 1600 * math.cos(a0), cy0 + 1600 * math.sin(a0)),
                     (cx0 + 1600 * math.cos(a1), cy0 + 1600 * math.sin(a1))], fill=(255, 255, 255, 34))
    elif sc.pattern == "dots":
        off = (t * 30) % 90
        for gx in range(-1, 10):
            for gy in range(-1, 16):
                x = gx * 90 + (45 if gy % 2 else 0) + off
                y = gy * 90 + off * 0.4
                fg.ell(x, y, 15, 15, fill=(255, 255, 255, 46))
    else:
        off = (t * 50) % 160
        for k in range(-2, 12):
            x = k * 160 + off
            fg.poly([(x, 0), (x + 80, 0), (x - 300 + 80, H), (x - 300, H)], fill=(255, 255, 255, 30))
    for kind, x0, y0, sz, spd, ph, col in sc.shapes:
        x = (x0 + math.sin(t * 0.8 + ph) * 40) % W
        y = (y0 - t * spd) % (H + 100) - 50
        if kind == "circle":
            fg.ell(x, y, sz, sz, fill=col)
        elif kind == "ring":
            fg.ell(x, y, sz, sz, outline=col, w=6)
        else:
            fg.poly(star_pts(x, y, sz, t + ph), fill=col)
    # hills
    g = mix(sc.c2, 0.78) + (255,)
    g2 = mix(sc.c2, 0.6) + (255,)
    fg.ell(W * 0.15, 1230, 520, 230, fill=g)
    fg.ell(W * 0.95, 1250, 540, 230, fill=g2)


class Show:
    def __init__(self, title, scenes, words, total, seed, caps=True):
        rnd = random.Random(seed)
        self.rnd = rnd
        self.total = total
        self.caps = caps
        self.body = hexrgb(rnd.choice(BODY_COLS))
        self.ears = rnd.choice(["cat", "bear", "antenna", "none"])
        self.scenes = [Scene(s["mood"], s["icon"], rnd, i) for i, s in enumerate(scenes)]
        self.words = words
        for sc in self.scenes:
            ws = [w for w in words if w["scene"] == sc.idx]
            sc.start = ws[0]["start"] if ws and sc.idx > 0 else 0.0
        self.starts = [sc.start for sc in self.scenes]
        self.wstarts = [w["start"] for w in words]
        self.wends = [w["end"] for w in words]
        self.chunks = self._chunks()
        self.cstarts = [c["start"] for c in self.chunks]
        self.bg_cache = {}

    # ----- captions
    def _chunks(self):
        chunks, cur = [], []

        def flush():
            if cur:
                chunks.append({"words": list(cur)})
                cur.clear()

        for w in self.words:
            if cur and (cur[0]["scene"] != w["scene"] or w["emph"] or cur[-1]["emph"]
                        or len(cur) >= 3 or cur[-1]["w"][-1:] in ".,!?;:"
                        or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16):
                flush()
            cur.append(w)
        flush()
        prev_zone = None
        for i, c in enumerate(chunks):
            ws = c["words"]
            c["text"] = " ".join(x["w"] for x in ws).rstrip(",.;:")
            c["start"] = ws[0]["start"]
            c["emph"] = any(x["emph"] for x in ws)
            c["mood"] = self.scenes[ws[0]["scene"]].mood
            c["end"] = chunks[i + 1]["words"][0]["start"] if i + 1 < len(chunks) else self.total
            r = self.rnd
            soft = c["mood"] in ("sad", "calm")
            fill, stroke = r.choice(CAP_STYLES["soft" if soft else "bright"])
            if c["emph"]:
                fill, stroke = r.choice(EMPH_STYLE)
            c["fill"], c["stroke"] = hexrgb(fill), hexrgb(stroke)
            n = len(c["text"])
            size = 150 if n <= 5 else 124 if n <= 8 else 100 if n <= 12 else 84
            if c["emph"]:
                size = int(size * 1.25)
            size = int(size * {"excited": 1.12, "surprised": 1.08, "sad": 0.82, "calm": 0.88}.get(c["mood"], 1.0))
            c["size"] = size
            zones = [250, 470, 1120] if not soft else [250, 1120]
            zone = r.choice([z for z in zones if z != prev_zone] or zones)
            prev_zone = zone
            c["y"] = zone
            c["angle"] = r.uniform(-3, 3) if soft else r.uniform(-10, 10)
            c["enter"] = "drop" if c["mood"] == "sad" else r.choice(["pop", "pop", "zoom", "slide", "drop", "spin"])
            c["phase"] = r.uniform(0, 6)
            c["img"] = None
        return chunks

    def _text_img(self, c):
        txt, size = c["text"].upper(), c["size"]
        maxw = W * 0.9
        while True:
            f = font(size)
            lines = [txt]
            if f.getlength(txt) > maxw and " " in txt:
                ws = txt.split(" ")
                k = max(1, len(ws) // 2)
                lines = [" ".join(ws[:k]), " ".join(ws[k:])]
            if max(f.getlength(l) for l in lines) <= maxw or size <= 40:
                break
            size -= 6
        sw = max(4, int(size * 0.14))
        lh = int(size * 1.12)
        wd = int(max(f.getlength(l) for l in lines)) + sw * 2 + 24
        ht = lh * len(lines) + sw * 2 + 30
        im = Image.new("RGBA", (wd, ht), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        for i, l in enumerate(lines):
            x = (wd - f.getlength(l)) / 2
            y = sw + 6 + i * lh
            d.text((x, y + 9), l, font=f, fill=(0, 0, 0, 110), stroke_width=sw, stroke_fill=(0, 0, 0, 110))
            d.text((x, y), l, font=f, fill=c["fill"] + (255,), stroke_width=sw, stroke_fill=c["stroke"] + (255,))
        return im.rotate(c["angle"], resample=Image.BICUBIC, expand=True)

    def _draw_caption(self, frame, t):
        if not self.chunks:
            return
        i = bisect.bisect_right(self.cstarts, t) - 1
        if i < 0:
            return
        c = self.chunks[i]
        if c["img"] is None:
            c["img"] = self._text_img(c)
        base = c["img"]
        tl = t - c["start"]
        dur = 0.3
        x, y = W / 2, c["y"]
        s, ang_extra, alpha = 1.0, 0.0, 1.0
        k = min(1.0, tl / dur)
        if tl < dur:
            en = c["enter"]
            if en == "pop":
                s = ease_out_back(k)
            elif en == "zoom":
                s = 2.4 - 1.4 * ease_out_cubic(k); alpha = k
            elif en == "slide":
                y += 170 * (1 - ease_out_cubic(k)); alpha = k
            elif en == "drop":
                y -= 300 * (1 - ease_out_bounce(k))
            else:  # spin
                s = ease_out_back(k); ang_extra = (1 - ease_out_cubic(k)) * 40
        mood = c["mood"]
        bob = math.sin(t * 6 + c["phase"]) * (8 if mood in ("excited", "funny", "happy") else 4)
        if mood == "sad":
            bob = (tl * 14) % 40 * 0.2
        y += bob
        if c["emph"] and tl < 0.7:
            x += math.sin(tl * 55) * 6 * (1 - tl / 0.7)
        if mood == "excited":
            ang_extra += math.sin(t * 11 + c["phase"]) * 2.5
        img = base
        if abs(s - 1) > 0.01 or abs(ang_extra) > 0.2:
            sw_, sh_ = max(2, int(base.width * max(s, 0.05))), max(2, int(base.height * max(s, 0.05)))
            img = base.resize((sw_, sh_), Image.BILINEAR)
            if abs(ang_extra) > 0.2:
                img = img.rotate(ang_extra, resample=Image.BILINEAR, expand=True)
        if alpha < 1:
            img = img.copy(); img.putalpha(img.getchannel("A").point(lambda v: int(v * alpha)))
        frame.paste(img, (int(x - img.width / 2), int(y - img.height / 2)), img)

    # ----- scenes
    def _scene_frame(self, sc, t):
        fg = FG()
        draw_back(fg, sc, t)
        cx = W / 2 + math.sin(t * 1.5) * 10
        cy = 800
        wi = bisect.bisect_right(self.wstarts, t) - 1
        speak = 0.0
        if wi >= 0 and t <= self.wends[wi] + 0.04:
            speak = 0.3 + 0.7 * abs(math.sin((t - self.wstarts[wi]) * 15 + wi))
        blink = (t % 3.3) < 0.13
        draw_mascot(fg, cx, cy, t, sc.mood, speak, self.body, self.ears, blink)
        ix = W / 2 + sc.side * 245 + math.sin(t * 2) * 8
        iy = 585 + math.sin(t * 3 + sc.idx) * 16
        draw_icon(fg, sc.icon, ix, iy, 62, t)
        f = sc.bg.copy()
        layer = fg.result()
        f.paste(layer, (0, 0), layer)
        return f

    def frame(self, t):
        i = max(0, bisect.bisect_right(self.starts, t) - 1)
        sc = self.scenes[i]
        f = self._scene_frame(sc, t)
        tr = t - sc.start
        if i > 0 and tr < 0.42:
            old = self._scene_frame(self.scenes[i - 1], t)
            k = ease_out_cubic(tr / 0.42)
            r = k * 1500
            mask = Image.new("L", (W, H), 0)
            cxm, cym = W / 2 + sc.side * 200, 800
            ImageDraw.Draw(mask).ellipse([cxm - r, cym - r, cxm + r, cym + r], fill=255)
            f = Image.composite(f, old, mask)
        if self.caps:
            self._draw_caption(f, t)
        return f


def render_video(show, audio_path, out_path, total):
    n = int(total * FPS)
    log = tempfile.TemporaryFile()
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", str(audio_path),
           "-af", "apad", "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "22", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(out_path)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
    try:
        for i in range(n):
            p.stdin.write(show.frame(i / FPS).tobytes())
        p.stdin.close()
        p.wait()
    except BrokenPipeError:
        pass
    if p.returncode != 0:
        log.seek(0)
        raise RuntimeError("ffmpeg failed: " + log.read().decode(errors="ignore")[-1200:])
