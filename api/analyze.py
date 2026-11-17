"""POST /api/analyze: run Luma on uploaded tables and return the dashboard document.

The body is JSON, optionally gzip-compressed (header ``X-Luma-Encoding: gzip``):

    {"files": {"sales": "<csv>", "catalog": "<csv>", ...},
     "options": {"brand": "Northline", "currency": "USD"}}

Files are read in memory and dropped when the response is sent. Nothing is written or kept.
"""

from __future__ import annotations

import ctypes
import gzip
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

# LightGBM needs the OpenMP runtime. Serverless images often lack it, so a portable copy ships
# with the function and is loaded first when the system has none.
try:
    ctypes.CDLL("libgomp.so.1", mode=ctypes.RTLD_GLOBAL)
except OSError:
    ctypes.CDLL(os.path.join(HERE, "_lib", "libgomp.so.1"), mode=ctypes.RTLD_GLOBAL)

from luma.data.loader import DataError  # noqa: E402
from luma.web import analyze  # noqa: E402

MAX_BODY = 4_400_000  # the platform refuses request bodies above 4.5 MB


class handler(BaseHTTPRequestHandler):  # noqa: N801 - the name the runtime looks for
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, {"ready": True})

    def do_POST(self) -> None:  # noqa: N802
        started = time.perf_counter()
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._send(400, {"error": "The upload was empty."})
        if length > MAX_BODY:
            return self._send(
                413,
                {"error": "The files are too large to upload here. Keep the last year of sales, or run Luma locally."},
            )
        try:
            raw = self.rfile.read(length)
            if (self.headers.get("X-Luma-Encoding") or "").lower() == "gzip":
                raw = gzip.decompress(raw)
            request = json.loads(raw)
            files = request.get("files") or {}
            options = request.get("options") or {}
            if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
                return self._send(400, {"error": "The upload was not in the expected format."})
            lead_time = options.get("leadTime")
            payload = analyze(
                files,
                brand=str(options.get("brand") or "")[:60] or None,
                currency=str(options.get("currency") or "")[:3] or None,
                lead_time=int(lead_time) if lead_time else None,
            )
        except DataError as err:
            message = str(err)
            return self._send(422, {"error": message[:1].upper() + message[1:] + "."})
        except (ValueError, OSError) as err:
            print("analyze: bad request", repr(err), file=sys.stderr)
            return self._send(400, {"error": "The upload could not be read. Check that each file is a CSV."})
        except Exception:  # noqa: BLE001 - report any failure to the browser as a clean message
            traceback.print_exc()
            return self._send(500, {"error": "Something went wrong while analysing the data."})
        payload["meta"]["analysisSeconds"] = round(time.perf_counter() - started, 1)
        print(
            f"analyze: {payload['meta']['counts']} in {payload['meta']['analysisSeconds']}s",
            file=sys.stderr,
        )
        return self._send(200, payload)
