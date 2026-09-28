"""QR codes as SVG data URIs (segno, pure Python) - e.g. to hand over invitation or reset links without e-mail.

The portal shows them as ``<img src="data:image/svg+xml;base64,...">`` which the CSP allows (``img-src data:``)
and which needs no ``innerHTML``.
"""

from __future__ import annotations

import base64
import io


def qr_data_uri(text: str, scale: int = 4) -> str:
    import segno

    buf = io.BytesIO()
    # Always dark on light with a quiet zone, so phones can scan it in dark mode too.
    segno.make(text, error="m").save(buf, kind="svg", scale=scale, border=4, xmldecl=False, dark="#000000",
                                     light="#ffffff", title="QR code")
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()
