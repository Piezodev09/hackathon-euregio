"""Sehr kleiner PDF-Erzeuger (A4, Helvetica, Text, Linien, Flächen) – ohne zusätzliche Abhängigkeit.

Reicht für Berichte mit Tabellen und Balken. Zeichen außerhalb von Windows-1252 werden als "?" ausgegeben.
Koordinaten: Punkte, Ursprung oben links (wird intern umgerechnet).
"""

from __future__ import annotations

W, H = 595.28, 841.89

# Zeichenbreiten (1/1000 em) für Helvetica / Helvetica-Bold; nicht aufgeführte Zeichen: Mittelwert.
_NARROW = {" ": 278, ",": 278, ".": 278, ":": 278, ";": 278, "i": 222, "l": 222, "j": 222, "I": 278, "f": 278, "t": 278,
           "r": 333, "-": 333, "(": 333, ")": 333, "/": 278, "|": 260, "!": 278, "'": 191}
_WIDE = {"m": 833, "w": 722, "M": 833, "W": 944, "%": 889, "@": 1015, "€": 556}


def _width(ch: str, bold: bool) -> int:
    if ch.isdigit():
        return 556
    if ch in _NARROW:
        return _NARROW[ch] + (30 if bold else 0)
    if ch in _WIDE:
        return _WIDE[ch] + (40 if bold else 0)
    if ch.isupper():
        return 722 if bold else 667
    return 611 if bold else 556


def text_width(s: str, size: float, bold: bool = False) -> float:
    return sum(_width(c, bold) for c in s) * size / 1000


def _esc(s: str) -> bytes:
    raw = s.encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _rgb(c: tuple[float, float, float]) -> str:
    return " ".join(f"{v:.3f}" for v in c)


class Pdf:
    def __init__(self, title: str = ""):
        self.title = title
        self.pages: list[list[bytes]] = []
        self.page()

    def page(self) -> None:
        self.pages.append([])

    @property
    def _ops(self) -> list[bytes]:
        return self.pages[-1]

    def text(self, x: float, y: float, s: str, size: float = 10, bold: bool = False,
             color=(0.1, 0.13, 0.16), align: str = "left") -> None:
        if align == "right":
            x -= text_width(s, size, bold)
        elif align == "center":
            x -= text_width(s, size, bold) / 2
        self._ops.append(b"BT /%s %.1f Tf %s rg 1 0 0 1 %.2f %.2f Tm (" % (b"F2" if bold else b"F1", size, _rgb(color).encode(), x, H - y)
                         + _esc(s) + b") Tj ET")

    def rect(self, x: float, y: float, w: float, h: float, color=(0.9, 0.9, 0.9)) -> None:
        self._ops.append(b"%s rg %.2f %.2f %.2f %.2f re f" % (_rgb(color).encode(), x, H - y - h, w, h))

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5, color=(0.75, 0.78, 0.8)) -> None:
        self._ops.append(b"%s RG %.2f w %.2f %.2f m %.2f %.2f l S" % (_rgb(color).encode(), width, x1, H - y1, x2, H - y2))

    def render(self) -> bytes:
        objs: list[bytes] = []
        n_pages = len(self.pages)
        page_ids = [5 + 2 * i for i in range(n_pages)]
        objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objs.append(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (b" ".join(b"%d 0 R" % p for p in page_ids), n_pages))
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        for i, ops in enumerate(self.pages):
            content = b"\n".join(ops)
            objs.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
                        b"/Contents %d 0 R >>" % (W, H, page_ids[i] + 1))
            objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        # Metadaten als UTF-16 (Hex), damit Umlaute und Gedankenstriche im Titel stimmen
        title = ("\ufeff" + self.title).encode("utf-16-be").hex().upper().encode()
        objs.append(b"<< /Title <" + title + b"> /Producer (Smart Bicycle Box) >>")
        info_id = len(objs)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info_id, xref)
        return bytes(out)
