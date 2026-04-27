"""
Shared local OAuth callback server.
Starts an HTTP server on localhost:8080, waits for a redirect with ?code=,
returns the code (and state) to the caller.
"""
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def wait_for_callback(port: int = 8080, timeout: int = 120) -> dict:
    """
    Blocks until the browser hits /callback?code=...
    Returns dict with keys from the query string (e.g. 'code', 'state', 'error').
    """
    result = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/callback"):
                params = parse_qs(urlparse(self.path).query)
                result.update({k: v[0] for k, v in params.items()})
                self._respond("<h2 style='font-family:sans-serif;color:green'>Done — you can close this tab.</h2>")
                done.set()
            else:
                self._respond("<h2 style='font-family:sans-serif'>Waiting for auth callback…</h2>")

        def _respond(self, body: str):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", port), Handler)
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    done.wait(timeout=timeout)
    server.shutdown()
    return result
