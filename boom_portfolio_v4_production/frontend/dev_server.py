from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import argparse
import os


BACKEND_ORIGIN = os.environ.get("BACKEND_ORIGIN", "http://127.0.0.1:8000")
ROUTES = {
    "/": "index.html",
    "/work": "work.html",
    "/photography": "photography.html",
    "/about": "about.html",
    "/contact": "contact.html",
    "/admin": "admin.html",
    "/project": "project.html",
    "/album": "album.html",
}


class PortfolioDevHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/api/"):
            self.proxy_api()
            return

        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path in ROUTES:
            self.path = "/" + ROUTES[path]
        super().do_GET()

    def do_POST(self):
        self.proxy_api()

    def do_PUT(self):
        self.proxy_api()

    def do_PATCH(self):
        self.proxy_api()

    def do_DELETE(self):
        self.proxy_api()

    def proxy_api(self):
        if not self.path.startswith("/api/"):
            self.send_error(404, "File not found")
            return

        body = None
        if "Content-Length" in self.headers:
            body = self.rfile.read(int(self.headers["Content-Length"]))

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        target = BACKEND_ORIGIN.rstrip("/") + self.path
        request = Request(target, data=body, headers=headers, method=self.command)

        try:
            with urlopen(request, timeout=30) as response:
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() not in {"connection", "transfer-encoding"}:
                        self.send_header(key, value)
                self.end_headers()
                self.wfile.write(response.read())
        except HTTPError as error:
            self.send_response(error.code)
            for key, value in error.headers.items():
                if key.lower() not in {"connection", "transfer-encoding"}:
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(error.read())
        except URLError as error:
            self.send_error(502, f"Backend unavailable: {error.reason}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), PortfolioDevHandler)
    print(f"Frontend running at http://{args.host}:{args.port}")
    print(f"Proxying /api/* to {BACKEND_ORIGIN}")
    server.serve_forever()


if __name__ == "__main__":
    main()
