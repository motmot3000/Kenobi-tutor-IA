"""Mise en forme du texte pour la voix : ce qui se dit, et en quels morceaux.

Adapté au tuteur :
- normalize() : la réponse telle qu'elle doit être dite. Ni Markdown, ni lien, ni émoji ; le code n'est pas lu
  (il s'affiche à l'écran, et c'est le tuteur qui le dit) ; les blocs kenobi-… de l'interface sont muets.
- split_segments() : un premier morceau court (premier son plus tôt), puis des morceaux plus longs.
- StreamSegmenter : libère les phrases au fil du flux du modèle, dès qu'elles sont terminées.
- spoken_fr() : heures et nombres écrits comme on les dit en Suisse romande (septante, huitante, nonante), au choix.
"""
import re
import unicodedata

FIRST_MIN, FIRST_MAX, SEGMENT_MAX = 25, 140, 320
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"»)\]]*\s+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_KENOBI_BLOCK = re.compile(r"```[ \t]*kenobi[-_][^\n]*\n[\s\S]*?(?:```|$)")
_CODE_BLOCK = re.compile(r"```.*?(?:```|$)", re.S)
_SYMBOLS = [
    (re.compile(r"\s*%"), " pour cent"), (re.compile(r"\s*€"), " euros"),
    (re.compile(r"\s+&\s+"), " et "), (re.compile(r"\s*(?:->|→|=>)\s*"), ", "),
    (re.compile(r"≈\s*"), "environ "), (re.compile(r"\s*°C\b"), " degrés"),
    (re.compile(r"\betc\.", re.I), "et cetera."),
]


def _drop_symbols(text):
    # Les émojis et pictogrammes ne se lisent pas.
    return "".join(c for c in text if unicodedata.category(c) not in ("So", "Sk", "Cs", "Co")
                   and c not in "\u200d\ufe0f")


def strip_interface_blocks(text):
    """Retire les blocs kenobi-… : l'interface les affiche en cartes, la voix ne les dit pas."""
    return _KENOBI_BLOCK.sub(" ", text or "")


def normalize(text):
    if not isinstance(text, str):
        return ""
    text = strip_interface_blocks(text)
    text = _CODE_BLOCK.sub(" ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = _URL.sub("lien", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", line)
        line = re.sub(r"^\s*>\s?", "", line)
        line = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", line)
        line = re.sub(r"(?<![\w*])[*_](?!\s)(.+?)(?<!\s)[*_](?![\w*])", r"\1", line)
        line = re.sub(r"^\s*\|.*\|\s*$", "", line)
        line = re.sub(r"^\s*[-=_*|:\s]{3,}$", "", line)
        line = line.strip()
        if line:
            lines.append(line)
    # Une ligne devient une phrase (une pause), sans point ajouté à la dernière.
    text = " ".join(line if i == len(lines) - 1 or line[-1] in ".!?…:;," else line + "."
                    for i, line in enumerate(lines))
    for pattern, spoken in _SYMBOLS:
        text = pattern.sub(spoken, text)
    text = _drop_symbols(text)
    text = re.sub(r"\s+([,.!?…;:])", r"\1", text)
    text = re.sub(r"([.!?…])\1+", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


_UNITS = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit",
          "neuf", "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize"]
_TENS = {20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante",
         70: "septante", 80: "huitante", 90: "nonante"}
_HOUR = re.compile(r"\b(\d{1,2})\s*h(?:\s*(\d{2}))?\b")
_NUMBER = re.compile(r"\b\d{1,6}\b")


def _number_fr(n):
    if n < 17:
        return _UNITS[n]
    if n < 20:
        return "dix-" + _UNITS[n - 10]
    if n < 100:
        t, u = divmod(n, 10)
        base = _TENS[t * 10]
        if u == 0:
            return base
        return base + (" et un" if u == 1 else "-" + _UNITS[u])
    if n < 1000:
        h, r = divmod(n, 100)
        head = "cent" if h == 1 else _UNITS[h] + " cent"
        return head if r == 0 else head + " " + _number_fr(r)
    th, r = divmod(n, 1000)
    head = "mille" if th == 1 else _number_fr(th) + " mille"
    return head if r == 0 else head + " " + _number_fr(r)


def _hour(match):
    h, mn = int(match.group(1)), match.group(2)
    spoken = ("une" if h == 1 else _number_fr(h)) + (" heure" if h <= 1 else " heures")
    if mn and int(mn):
        spoken += " " + _number_fr(int(mn))
    return spoken


def spoken_fr(text):
    """Heures et nombres sous la forme à prononcer, à la suisse (septante, huitante, nonante)."""
    text = _HOUR.sub(_hour, text)
    return _NUMBER.sub(lambda m: _number_fr(int(m.group(0))), text)


def sentences(text):
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


def _cut_long(sentence, limit):
    parts, current = [], ""
    for piece in re.split(r"(?<=[,;:])\s+|\s+", sentence):
        candidate = (current + " " + piece).strip()
        if current and len(candidate) > limit:
            parts.append(current)
            current = piece
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_segments(text, first_max=FIRST_MAX, segment_max=SEGMENT_MAX):
    """Regroupe les phrases : un premier morceau court, puis jusqu'à segment_max."""
    units = []
    for sentence in sentences(text):
        units.extend(_cut_long(sentence, segment_max) if len(sentence) > segment_max else [sentence])
    segments, current = [], ""
    for unit in units:
        limit = first_max if not segments else segment_max
        candidate = (current + " " + unit).strip()
        if current and (len(candidate) > limit or (not segments and len(current) >= FIRST_MIN)):
            segments.append(current)
            current = unit
        else:
            current = candidate
    if current:
        segments.append(current)
    return segments


class StreamSegmenter:
    """Libère des morceaux à dire depuis le flux du modèle, phrase par phrase."""

    def __init__(self):
        self.buffer, self.released = "", 0

    def _ready(self):
        if self.buffer.count("```") % 2:
            return []  # dans un bloc de code ou d'interface : attendre sa fin
        cuts = [match.end() for match in _SENTENCE_END.finditer(self.buffer)]
        if not cuts:
            return []
        cut = cuts[-1]
        ready, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return self._emit(ready)

    def _emit(self, raw):
        text = normalize(raw)
        if not text:
            return []
        groups = split_segments(text) if self.released == 0 else split_segments(text, SEGMENT_MAX)
        self.released += len(groups)
        return groups

    def feed(self, delta):
        if not isinstance(delta, str) or not delta:
            return []
        self.buffer += delta
        return self._ready()

    def finish(self):
        raw, self.buffer = self.buffer, ""
        return self._emit(raw)
