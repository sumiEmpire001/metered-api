import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .meter import QuotaExceeded


def make_server(meter, port=8080):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def reply(self, status, value, csv=False):
            body = (value if csv else json.dumps(value)).encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/csv; charset=utf-8" if csv else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.dispatch()
        def do_POST(self):
            self.dispatch()
        def dispatch(self):
            try:
                header = self.headers.get("Authorization", "")
                client = meter.authenticate(header[7:] if header.startswith("Bearer ") else "")
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                if self.command == "GET" and parts.path == "/usage":
                    self.reply(200, meter.status(client))
                elif self.command == "GET" and parts.path == "/events":
                    self.reply(200, {"events": meter.events(client, limit=int(query.get("limit", [100])[0]), offset=int(query.get("offset", [0])[0]))})
                elif self.command == "GET" and parts.path == "/export.csv":
                    self.reply(200, meter.export_csv(client, offset=int(query.get("offset", [0])[0])), csv=True)
                elif self.command == "POST" and parts.path == "/charge":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384 or self.headers.get("Transfer-Encoding"):
                        self.reply(413, {"error": "A JSON body of 1..16384 bytes with Content-Length is required"})
                        return
                    self.connection.settimeout(5)
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict) or set(value) - {"event_id", "units", "label"}:
                        raise ValueError("Unknown charge fields")
                    result = meter.charge(client, **value)
                    self.reply(200 if result["duplicate"] else 201, result)
                else:
                    self.reply(404, {"error": "Route not found"})
            except PermissionError:
                self.reply(401, {"error": "Unauthorized"})
            except QuotaExceeded as exc:
                self.reply(429, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
            except TimeoutError:
                self.reply(408, {"error": "Request body timed out"})
            except Exception:
                self.reply(500, {"error": "Internal error"})
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
