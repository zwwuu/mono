"""Demo portal for the sample project: a login page and one PDF download.

Run: python3 demo_portal.py  (serves on 127.0.0.1:8901)

The login form is a demo -- any username/password posts to /reports,
which lists the monthly report link. The PDF is a minimal valid
one-page document built here, so the run needs no fixtures.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8901

LOGIN_PAGE = b"""<html><body>
<h1>Vendor portal</h1>
<form action="/reports" method="post">
  <label>Username <input id="username" name="username"></label><br>
  <label>Password <input id="password" name="password" type="password"></label><br>
  <button type="submit">Sign in</button>
</form>
</body></html>"""

REPORTS_PAGE = b"""<html><body>
<h1>Monthly reports</h1>
<a class="download" href="/files/Monthly-Report-ACME-20261122.pdf">Download report</a>
</body></html>"""

PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length 44 >> stream\n"
    b"BT /F1 24 Tf 100 700 Td (ACME monthly report) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"trailer << /Size 6 /Root 1 0 R >>\n"
    b"%%EOF\n"
)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, content_type, filename=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # http.server naming
        if self.path == "/":
            self._send(LOGIN_PAGE, "text/html")
        elif self.path == "/files/Monthly-Report-ACME-20261122.pdf":
            self._send(PDF, "application/pdf", "Monthly-Report-ACME-20261122.pdf")
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):  # http.server naming
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path == "/reports":
            self._send(REPORTS_PAGE, "text/html")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
