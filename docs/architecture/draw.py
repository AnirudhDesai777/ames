"""Draw the README diagrams as light and dark SVG pairs.

The Mermaid files in this directory are the source of truth. This script
redraws three of them for the root README. Run it after a Mermaid change:

    python3 docs/architecture/draw.py
"""

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).parent / "svg"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"

# GitHub page colors, so each file sits on its theme like native content.
THEMES = {
    "light": {
        "text": "#1f2328",
        "muted": "#59636e",
        "line": "#8c959f",
        "card": "#ffffff",
        "cardline": "#d1d9e0",
        "soft": "#f6f8fa",
        "accent": "#0969da",
        "accentfill": "#ddf4ff",
    },
    "dark": {
        "text": "#e6edf3",
        "muted": "#9198a1",
        "line": "#656c76",
        "card": "#151b23",
        "cardline": "#3d444d",
        "soft": "#212830",
        "accent": "#4493f8",
        "accentfill": "#0c2d6b",
    },
}


class Svg:
    def __init__(self, width, height, label, theme):
        self.w, self.h, self.label, self.t = width, height, label, THEMES[theme]
        self.parts = []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=12, color="text", weight="normal", anchor="middle"):
        self.add(
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{self.t[color]}" text-anchor="{anchor}">{escape(s)}</text>'
        )

    def lines(self, x, y, rows, size=12, color="text", gap=16, anchor="middle"):
        for i, row in enumerate(rows):
            self.text(x, y + i * gap, row, size, color, anchor=anchor)

    def boundary(self, x, y, w, h, label):
        t = self.t
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
            f'stroke="{t["line"]}" stroke-dasharray="6 5"/>'
        )
        self.text(x + 16, y + 22, label, 12, "muted", "600", "start")

    def card(self, x, y, w, h, title, tech, desc=(), kind="card", gap=16):
        t = self.t
        fill, stroke, dash = t["card"], t["cardline"], ""
        if kind == "accent":
            fill, stroke = t["accentfill"], t["accent"]
        elif kind == "external":
            fill, stroke, dash = t["soft"], t["line"], ' stroke-dasharray="5 4"'
        self.add(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{1.5 if kind == "accent" else 1}"{dash}/>'
        )
        cx = x + w / 2
        top = y + (h - (34 if tech else 18) - gap * len(desc)) / 2 + 14
        self.text(cx, top, title, 13, "text", "600")
        if tech:
            self.text(cx, top + 17, tech, 11, "muted")
        self.lines(cx, top + (36 if tech else 20), desc, 12, "text", gap)

    def person(self, x, y, w, h, title, desc=()):
        t = self.t
        cx = x + w / 2
        self.add(
            f'<circle cx="{cx}" cy="{y}" r="13" fill="{t["card"]}" stroke="{t["text"]}" stroke-width="1.5"/>'
        )
        self.add(
            f'<rect x="{x}" y="{y + 18}" width="{w}" height="{h}" rx="18" fill="{t["card"]}" '
            f'stroke="{t["text"]}" stroke-width="1.5"/>'
        )
        self.text(cx, y + 18 + h / 2 - 8 * len(desc) + 5, title, 13, "text", "600")
        self.lines(cx, y + 18 + h / 2 - 8 * len(desc) + 22, desc, 12, "muted")

    def store(self, x, y, w, h, title, tech):
        t = self.t
        ry = 7
        self.add(
            f'<path d="M{x},{y + ry} a{w / 2},{ry} 0 0,0 {w},0 a{w / 2},{ry} 0 0,0 {-w},0 '
            f'v{h - 2 * ry} a{w / 2},{ry} 0 0,0 {w},0 v{-(h - 2 * ry)}" fill="{t["card"]}" '
            f'stroke="{t["cardline"]}"/>'
        )
        self.text(x + w / 2, y + h / 2 + 6, title, 13, "text", "600")
        self.text(x + w / 2, y + h / 2 + 22, tech, 11, "muted")

    def arrow(self, points, dashed=False, color="line"):
        d = " ".join(f"{px},{py}" for px, py in points)
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        self.add(
            f'<polyline points="{d}" fill="none" stroke="{self.t[color]}" stroke-width="1.5"'
            f'{dash} marker-end="url(#head-{color})"/>'
        )

    def render(self):
        t = self.t
        markers = "".join(
            f'<marker id="head-{c}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{t[c]}"/></marker>'
            for c in ("line", "accent", "text")
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" aria-label="{escape(self.label)}" '
            f'font-family="{FONT}"><title>{escape(self.label)}</title><defs>{markers}</defs>'
            + "".join(self.parts)
            + "</svg>\n"
        )


def context(theme):
    s = Svg(
        1020,
        300,
        "C4 context: the user talks to Ames on their own Mac. "
        "The only data that leaves the Mac is opt-in web search queries.",
        theme,
    )
    s.boundary(268, 24, 476, 252, "Your Mac")
    s.person(16, 104, 160, 64, "User", ["Talks by voice", "or text"])
    s.card(
        316,
        84,
        380,
        140,
        "Ames",
        "Software system",
        [
            "Local voice agent in Chrome.",
            "Speech to text, language model and",
            "text to speech run on this Mac.",
            "Listens on 127.0.0.1:8765 only.",
        ],
        "accent",
    )
    s.card(
        836, 52, 168, 84, "Search services", "SearXNG, then Brave", ["Off by default"], "external"
    )
    s.card(836, 180, 168, 84, "Hugging Face", "Planned: ames setup", ["Model weights"], "external")
    s.arrow([(176, 154), (312, 154)], color="text")
    s.lines(222, 112, ["speaks,", "types,", "listens"], 11, "muted", 13)
    s.arrow([(696, 120), (832, 94)], dashed=True)
    s.lines(790, 84, ["opt-in", "queries"], 11, "muted", 13)
    s.arrow([(832, 222), (700, 196)], dashed=True)
    s.lines(790, 232, ["planned", "download"], 11, "muted", 13)
    return s


def containers(theme):
    s = Svg(
        1180,
        660,
        "C4 containers: Chrome talks to one local Python server over HTTP and WebRTC. "
        "Models run in process. Saved facts go to ames.db. Web search is opt-in.",
        theme,
    )
    s.person(44, 14, 200, 50, "User")
    s.boundary(12, 104, 972, 540, "Your Mac")
    s.boundary(28, 140, 240, 380, "Chrome")
    s.card(
        44,
        180,
        208,
        320,
        "Web client",
        "TypeScript, Vite",
        [
            "Orb and transcript",
            "Text box",
            "Mute and end",
            "Voice choice",
            "Web search switch",
            "Saved memories",
        ],
        gap=24,
    )
    s.boundary(420, 140, 548, 380, "Ames server, one Python process")
    s.card(
        444,
        184,
        224,
        96,
        "Voice pipeline",
        "Pipecat, SmallWebRTC",
        ["VAD, STT, tool loop,", "sentence split, TTS"],
        "accent",
    )
    s.card(444, 404, 224, 88, "HTTP app", "aiohttp, 127.0.0.1:8765", ["Token, Host, Origin checks"])
    s.boundary(728, 160, 224, 176, "Models, in process (MLX)")
    s.card(744, 188, 192, 40, "Parakeet TDT 0.6B", "")
    s.card(744, 236, 192, 40, "mlx-lm, AMES_LLM_MODEL", "")
    s.card(744, 284, 192, 40, "Pocket TTS 100M", "")
    s.card(728, 356, 224, 56, "Session history", "In memory, this session only")
    s.card(728, 428, 224, 64, "Tools", "remember, recall_memory,", ["get_time, web_search"])
    s.store(560, 556, 240, 64, "ames.db", "SQLite, saved facts")
    s.card(1008, 408, 156, 76, "Search services", "SearXNG, Brave", ["Opt-in"], "external")
    # User to page.
    s.arrow([(144, 82), (144, 176)], color="text")
    # Page to server and back.
    s.arrow([(252, 214), (440, 214)], color="accent")
    s.lines(346, 206, ["mic audio, typed text"], 11, "muted")
    s.arrow([(440, 254), (256, 254)], color="accent")
    s.lines(346, 272, ["speech, transcript, events"], 11, "muted")
    s.lines(346, 288, ["WebRTC"], 11, "accent")
    s.arrow([(252, 448), (440, 448)])
    s.lines(346, 440, ["page, offer, memories"], 11, "muted")
    s.lines(346, 466, ["HTTP + token"], 11, "muted")
    s.arrow([(556, 400), (556, 284)])
    s.lines(612, 346, ["starts and", "ends sessions"], 11, "muted", 14)
    # Pipeline calls fan out to the right column.
    s.add(
        f'<polyline points="668,232 700,232" fill="none" stroke="{s.t["line"]}" stroke-width="1.5"/>'
    )
    s.add(
        f'<polyline points="700,208 700,460" fill="none" stroke="{s.t["line"]}" stroke-width="1.5"/>'
    )
    for y in (208, 256, 304, 384, 460):
        s.arrow([(700, y), (724 if y > 340 else 740, y)])
    # Storage and network.
    s.arrow([(840, 492), (840, 588), (804, 588)])
    s.lines(888, 548, ["remember,", "recall"], 11, "muted", 14)
    s.arrow([(520, 492), (520, 588), (556, 588)])
    s.lines(474, 548, ["list, delete"], 11, "muted")
    s.arrow([(952, 460), (1004, 446)], dashed=True)
    s.lines(980, 432, ["queries"], 11, "muted")
    return s


def turn(theme):
    s = Svg(
        880,
        430,
        "One spoken turn: audio goes to speech to text, the transcript goes to the language model, "
        "and each complete sentence goes to text to speech and plays while the next one is written.",
        theme,
    )
    names = ["You", "Chrome page", "Speech to text", "Language model", "Text to speech"]
    xs = [80, 260, 440, 620, 800]
    for x, n in zip(xs, names):
        if n == "You":
            s.person(x - 60, 22, 120, 30, n)
        else:
            s.card(x - 70, 18, 140, 40, n, "", kind="accent" if n == "Language model" else "card")
        s.add(
            f'<line x1="{x}" y1="{66 if n != "You" else 72}" x2="{x}" y2="410" stroke="{s.t["cardline"]}" stroke-dasharray="4 4"/>'
        )

    def msg(a, b, y, label):
        x1, x2 = xs[a], xs[b]
        pad = 4 if x2 > x1 else -4
        s.arrow([(x1, y), (x2 - pad, y)], color="text")
        s.text((x1 + x2) / 2, y - 7, label, 11, "muted")

    msg(0, 1, 104, "speak")
    msg(1, 2, 144, "mic audio, WebRTC")
    msg(2, 3, 184, "transcript and session history")
    s.add(
        f'<rect x="40" y="206" width="800" height="128" rx="10" fill="{s.t["soft"]}" '
        f'fill-opacity="0.6" stroke="{s.t["line"]}"/>'
    )
    s.text(56, 224, "for each complete sentence", 11, "muted", "600", "start")
    msg(3, 4, 248, "sentence")
    msg(4, 1, 284, "speech, WebRTC")
    msg(1, 0, 318, "plays")
    s.add(
        f'<rect x="40" y="352" width="800" height="40" rx="10" fill="{s.t["accentfill"]}" '
        f'stroke="{s.t["accent"]}"/>'
    )
    s.text(440, 377, "Speak for 300 ms or more and Ames stops talking and listens.", 12, "text")
    return s


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for name, draw in (("context", context), ("containers", containers), ("turn", turn)):
        for theme in THEMES:
            (OUT / f"{name}-{theme}.svg").write_text(draw(theme).render())
