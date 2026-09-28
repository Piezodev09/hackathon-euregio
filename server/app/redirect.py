"""Tiny HTTP -> HTTPS redirector for port 80 (standard library only).

    python3 -m app.redirect --port 80

Answers every request with ``301`` to the same path on HTTPS. The target host is taken from the
request only if it is in ``BIKE_ALLOWED_HOSTS``; otherwise the redirect goes to ``BIKE_BASE_URL``
(no open redirect). ``/health`` answers ``200`` so the redirector can be monitored.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from urllib.parse import urlsplit

log = logging.getLogger("redirect")
MAX_REQUEST = 8192


def target_for(host_header: str, path: str, base_url: str, allowed: set[str]) -> str:
    base = urlsplit(base_url)
    host = host_header.strip().lower()
    hostname = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    netloc = base.netloc
    if hostname and hostname in allowed:
        netloc = hostname + (f":{base.port}" if base.port and base.port != 443 else "")
    if not path.startswith("/") or path.startswith("//"):
        path = "/"
    return f"https://{netloc}{path}"


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, base_url: str, allowed: set[str]) -> None:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        writer.close()
        return
    lines = head[:MAX_REQUEST].decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    path = parts[1] if len(parts) >= 2 else "/"
    host = next((ln.split(":", 1)[1] for ln in lines[1:] if ln.lower().startswith("host:")), "")
    if path == "/health":
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 3\r\nConnection: close\r\n\r\nok\n")
    else:
        location = target_for(host, path, base_url, allowed)
        body = f"Moved to {location}\n".encode()
        writer.write(
            f"HTTP/1.1 301 Moved Permanently\r\nLocation: {location}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
    try:
        await writer.drain()
    finally:
        writer.close()


async def serve(host: str, port: int, base_url: str, allowed: set[str]) -> None:
    server = await asyncio.start_server(lambda r, w: handle(r, w, base_url, allowed), host, port, limit=MAX_REQUEST)
    log.info("Redirecting http://*:%d to %s", port, base_url)
    async with server:
        await server.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=80)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    base_url = os.environ.get("BIKE_BASE_URL", "https://127.0.0.1")
    allowed = {h.strip().lower() for h in os.environ.get("BIKE_ALLOWED_HOSTS", "").split(",") if h.strip() and "*" not in h}
    asyncio.run(serve(args.host, args.port, base_url, allowed))


if __name__ == "__main__":
    main()
