const fs = require("fs");
const http = require("http");
const path = require("path");

const PORT = Number(process.env.PORT || 5173);
const ROOT = __dirname;
const UPSTREAM = process.env.API_BASE || "http://127.0.0.1:8000";

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".gif": "image/gif",
  ".svg": "image/svg+xml",
};

const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/") || req.url.startsWith("/visualizations/")) {
    proxyApi(req, res);
    return;
  }

  serveStatic(req, res);
});

function proxyApi(req, res) {
  const upstreamUrl = new URL(req.url, UPSTREAM);
  const headers = {
    accept: req.headers.accept || "application/json",
  };

  if (req.headers["content-type"]) {
    headers["content-type"] = req.headers["content-type"];
  }

  if (req.headers["content-length"]) {
    headers["content-length"] = req.headers["content-length"];
  }

  const proxyReq = http.request(
    upstreamUrl,
    {
      method: req.method,
      headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode || 502, {
        "content-type": proxyRes.headers["content-type"] || "application/json",
        "access-control-allow-origin": "*",
      });
      proxyRes.pipe(res);
    },
  );

  proxyReq.on("error", (error) => {
    res.writeHead(502, { "content-type": "application/json; charset=utf-8" });
    res.end(JSON.stringify({ error: `API proxy failed: ${error.message}` }));
  });

  req.pipe(proxyReq);
}

function serveStatic(req, res) {
  const requestPath = decodeURIComponent(new URL(req.url, `http://localhost:${PORT}`).pathname);
  const relativePath = requestPath === "/" ? "index.html" : requestPath.slice(1);
  const filePath = path.normalize(path.join(ROOT, relativePath));

  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403);
    res.end("Forbidden");
    return;
  }

  fs.readFile(filePath, (error, content) => {
    if (error) {
      res.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      res.end("Not found");
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, {
      "content-type": MIME_TYPES[ext] || "application/octet-stream",
      "cache-control": "no-cache",
    });
    res.end(content);
  });
}

server.listen(PORT, "0.0.0.0", () => {
  console.log(`H5 server running at http://localhost:${PORT}/`);
  console.log(`Proxying /api to ${UPSTREAM}`);
});
