
# shortner_postgres.py
import http.server
import socketserver
import urllib.parse
import os
import time
import threading
import random
import re
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool

# -------------------- Config --------------------
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))  # Render define PORT automaticamente
DATABASE_URL = os.getenv("DATABASE_URL")  # defina no Render (use a Internal Database URL)
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
RESERVED = {"new", "list", "stats", "help", "index.html", "get", "update", "delete"}

# -------------------- Base62 --------------------
def base62_encode(n: int) -> str:
    if n == 0:
        return ALPHABET[0]
    s = []
    base = len(ALPHABET)
    while n > 0:
        n, r = divmod(n, base)
        s.append(ALPHABET[r])
    return "".join(reversed(s))

# -------------------- Validações --------------------
def is_http_url(u: str) -> bool:
    return isinstance(u, str) and (u.startswith("http://") or u.startswith("https://"))

def validate_slug_path(slug: str) -> bool:
    """
    Valida slug com múltiplos segmentos separados por '/'. Cada segmento: [A-Za-z0-9-], 1..32 chars.
    Sem barra inicial/final e sem '//' duplicado. Bloqueia nomes reservados.
    Ex.: 'PromocaoMercadoPago/Whats'
    """
    if not isinstance(slug, str):
        return False
    if len(slug) < 1 or len(slug) > 128:
        return False
    if slug.startswith("/") or slug.endswith("/") or "//" in slug:
        return False
    segments = slug.split("/")
    for seg in segments:
        if seg in RESERVED:
            return False
        if not re.fullmatch(r"[A-Za-z0-9-]{1,32}", seg):
            return False
    return True

def build_short_base(handler: http.server.BaseHTTPRequestHandler) -> str:
    host_hdr = handler.headers.get("Host")
    if host_hdr:
        # Render fornece host com HTTPS automaticamente (mas aqui é HTTP simples)
        return f"http://{host_hdr}"
    return f"http://{HOST}:{PORT}"

# -------------------- HTML simples --------------------
INDEX_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<title>EncCurtador • Painel</title>
<style>
body{font-family:system-ui,Arial,sans-serif;max-width:980px;margin:40px auto;padding:0 16px;color:#222}
h1{margin:0 0 8px} small{color:#666}
input,textarea,select,button{font-size:16px;padding:8px;margin:6px 0;width:100%}
code,pre{background:#f7f7f7;border:1px solid #eee;padding:8px;border-radius:6px}
table{border-collapse:collapse;width:100%} th,td{border:1px solid #ddd;padding:8px}
th{background:#fafafa;text-align:left}
.actions a{margin-right:8px}
</style>
</head>
<body>
<h1>EncCurtador • Painel</h1>
<p><small>Servidor simples. Use a API abaixo para criar/editar links.</small></p>
<pre>
POST /new        JSON: { "urls": ["https://..."], "weights": [1,2,...], "code": "slug/opcional" }
POST /update     JSON: { "code": "atual", "new_code": "novo/opcional", "urls": [...], "weights": [...] }
POST /delete     JSON: { "code": "slug" }
GET  /list
GET  /get/{code}
GET  /stats/{code}
GET  /{code}     (redireciona)
</pre>
</body>
</html>
"""

# -------------------- DB Pool & Schema --------------------
DB_POOL = None

def init_db_pool():
    global DB_POOL
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL não definido nas variáveis de ambiente.")
    # Cria um pool simples de conexões
    DB_POOL = pool.SimpleConnectionPool(
        minconn=1, maxconn=10, dsn=DATABASE_URL
    )

def get_conn():
    return DB_POOL.getconn()

def put_conn(conn):
    DB_POOL.putconn(conn)

def with_conn(fn):
    """Decorator utilitário para abrir/fechar conexão."""
    def _wrap(*args, **kwargs):
        conn = get_conn()
        try:
            return fn(conn, *args, **kwargs)
        finally:
            put_conn(conn)
    return _wrap

@with_conn
def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS urls (
          code        TEXT PRIMARY KEY,
          type        TEXT NOT NULL CHECK (type IN ('single','multi')),
          url         TEXT,
          created_at  TIMESTAMP WITH TIME ZONE NOT NULL,
          hits        BIGINT NOT NULL DEFAULT 0
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS targets (
          id     SERIAL PRIMARY KEY,
          code   TEXT NOT NULL REFERENCES urls(code) ON DELETE CASCADE,
          url    TEXT NOT NULL,
          weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
          hits   BIGINT NOT NULL DEFAULT 0
        );
        """)
        # contador global (opcional)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS counters (
          name   TEXT PRIMARY KEY,
          value  BIGINT NOT NULL
        );
        """)
        # inicializa contador se não existir
        cur.execute("INSERT INTO counters(name, value) VALUES ('short_counter', 1000) ON CONFLICT (name) DO NOTHING;")
    conn.commit()

@with_conn
def next_code(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("UPDATE counters SET value = value + 1 WHERE name = 'short_counter' RETURNING value;")
        (val,) = cur.fetchone()
    conn.commit()
    return base62_encode(val)

@with_conn
def get_entry(conn, code: str):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM urls WHERE code = %s;", (code,))
        url_row = cur.fetchone()
        if not url_row:
            return None
        if url_row["type"] == "single":
            return {"type": "single", "url": url_row["url"], "hits": url_row["hits"], "created_at": url_row["created_at"]}
        else:
            cur.execute("SELECT id, url, weight, hits FROM targets WHERE code = %s ORDER BY id;", (code,))
            targets = [dict(r) for r in cur.fetchall()]
            return {"type": "multi", "targets": targets, "hits": url_row["hits"], "created_at": url_row["created_at"]}

@with_conn
def list_all(conn):
    out = []
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM urls ORDER BY created_at DESC;")
        for u in cur.fetchall():
            if u["type"] == "single":
                out.append({"code": u["code"], "type": "single", "url": u["url"], "hits": u["hits"]})
            else:
                cur.execute("SELECT url, weight, hits FROM targets WHERE code = %s ORDER BY id;", (u["code"],))
                ts = [dict(r) for r in cur.fetchall()]
                out.append({"code": u["code"], "type": "multi", "targets": ts, "hits": u["hits"]})
    return out

@with_conn
def create_short(conn, urls, weights, custom_code=None):
    # reaproveita slug se mesma config já existir
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT code, type, url, hits FROM urls;")
        existing = cur.fetchall()
        for e in existing:
            code = e["code"]
            if e["type"] == "single" and len(urls) == 1:
                if e["url"] == urls[0]:
                    return code
            elif e["type"] == "multi":
                cur.execute("SELECT url, weight FROM targets WHERE code=%s ORDER BY id;", (code,))
                ex_targets = cur.fetchall()
                ex_urls = [t["url"] for t in ex_targets]
                ex_weights = [float(t["weight"]) for t in ex_targets]
                if ex_urls == urls and ex_weights == weights:
                    return code

        # gerar código
        if custom_code:
            # verificar colisão
            cur.execute("SELECT 1 FROM urls WHERE code = %s;", (custom_code,))
            if cur.fetchone():
                raise ValueError("Erro: slug já está em uso.")
            code = custom_code
        else:
            code = next_code(conn)

        # inserir
        now = time.strftime('%Y-%m-%d %H:%M:%S%z', time.localtime())
        if len(urls) == 1:
            cur.execute("""
                INSERT INTO urls(code, type, url, created_at, hits)
                VALUES (%s,'single',%s,%s,0);
            """, (code, urls[0], now))
        else:
            cur.execute("""
                INSERT INTO urls(code, type, url, created_at, hits)
                VALUES (%s,'multi',NULL,%s,0);
            """, (code, now))
            for u, w in zip(urls, weights):
                cur.execute("""
                    INSERT INTO targets(code, url, weight, hits)
                    VALUES (%s,%s,%s,0);
                """, (code, u, float(w)))
    conn.commit()
    return code

@with_conn
def update_short(conn, code, new_code, urls, weights):
    with conn.cursor() as cur:
        # existe?
        cur.execute("SELECT type FROM urls WHERE code = %s;", (code,))
        row = cur.fetchone()
        if not row:
            return None
        # renomear?
        if new_code and new_code != code:
            # reservado/colisão checado fora
            cur.execute("SELECT 1 FROM urls WHERE code = %s;", (new_code,))
            if cur.fetchone():
                raise ValueError("Erro: slug já está em uso.")
            cur.execute("UPDATE urls SET code = %s WHERE code = %s;", (new_code, code))
            cur.execute("UPDATE targets SET code = %s WHERE code = %s;", (new_code, code))
            code = new_code

        # aplicar nova configuração
        if len(urls) == 1:
            cur.execute("UPDATE urls SET type='single', url=%s WHERE code=%s;", (urls[0], code))
            cur.execute("DELETE FROM targets WHERE code=%s;", (code,))
        else:
            cur.execute("UPDATE urls SET type='multi', url=NULL WHERE code=%s;", (code,))
            cur.execute("DELETE FROM targets WHERE code=%s;", (code,))
            for u, w in zip(urls, weights):
                cur.execute("INSERT INTO targets(code, url, weight, hits) VALUES (%s,%s,%s,0);", (code, u, float(w)))
    conn.commit()
    return code

@with_conn
def delete_short(conn, code):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM urls WHERE code=%s;", (code,))
    conn.commit()

@with_conn
def pick_target_and_count(conn, code):
    """Seleciona destino e incrementa hits, tudo dentro da transação."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT type, url FROM urls WHERE code=%s;", (code,))
        u = cur.fetchone()
        if not u:
            return None
        if u["type"] == "single":
            cur.execute("UPDATE urls SET hits = hits + 1 WHERE code=%s;", (code,))
            conn.commit()
            return u["url"]
        else:
            cur.execute("SELECT id, url, weight FROM targets WHERE code=%s ORDER BY id;", (code,))
            ts = cur.fetchall()
            if not ts:
                return "ERR_NO_TARGETS"
            weights = [float(t["weight"]) for t in ts]
            if sum(weights) == 0:
                weights = [1.0] * len(ts)
            idx = random.choices(range(len(ts)), weights=weights, k=1)[0]
            target_row = ts[idx]
            # incrementos
            cur.execute("UPDATE urls SET hits = hits + 1 WHERE code=%s;", (code,))
            cur.execute("UPDATE targets SET hits = hits + 1 WHERE id=%s;", (target_row["id"],))
            conn.commit()
            return target_row["url"]

# -------------------- HTTP Handler --------------------
class ShortenerHandler(http.server.SimpleHTTPRequestHandler):
    # GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")
        # UI
        if path == "" or path == "index.html":
            return self.respond_html(INDEX_HTML)
        # ajuda
        if path == "help":
            return self.respond_text(
                "EncCurtador (PostgreSQL)\n"
                "Endpoints:\n"
                " POST /new { urls:[...], weights:[...], code?:slug }\n"
                " POST /update { code, new_code?, urls, weights }\n"
                " POST /delete { code }\n"
                " GET  /list\n"
                " GET  /get/{code}\n"
                " GET  /stats/{code}\n"
                " GET  /{code}\n"
            )
        if path == "list":
            data = list_all()
            # resposta texto simples
            lines = []
            for e in data:
                if e["type"] == "single":
                    lines.append(f"{e['code']} -> {e['url']} (hits: {e['hits']})")
                else:
                    parts = [f"{t['url']} [w={t['weight']} hits={t['hits']}]" for t in e["targets"]]
                    lines.append(f"{e['code']} -> MULTI: {', '.join(parts)} (total hits: {e['hits']})")
            return self.respond_text("\n".join(lines) if lines else "Sem links ainda.")
        if path.startswith("get/"):
            code = path.split("/", 1)[1] if "/" in path else ""
            if not code:
                return self.respond_text("Uso: /get/{code}", status=400)
            entry = get_entry(code)
            if not entry:
                return self.respond_text("Código não encontrado.", status=404)
            raw = json.dumps({"code": code, **entry}, ensure_ascii=False, default=str)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(raw.encode("utf-8"))
            return
        if path.startswith("stats/"):
            code = path.split("/", 1)[1] if "/" in path else ""
            if not code:
                return self.respond_text("Uso: /stats/{code}", status=400)
            entry = get_entry(code)
            if not entry:
                return self.respond_text("Código não encontrado.", status=404)
            if entry["type"] == "single":
                text = (
                    f"Código: {code}\n"
                    f"Tipo: SINGLE\n"
                    f"URL: {entry['url']}\n"
                    f"Hits: {entry['hits']}\n"
                    f"Criado em: {entry['created_at']}\n"
                )
                return self.respond_text(text)
            else:
                lines = [
                    f"Código: {code}",
                    "Tipo: MULTI",
                    f"Criado em: {entry['created_at']}",
                    f"Total hits: {entry['hits']}",
                    "Destinos:"
                ]
                total = entry["hits"] if entry["hits"] > 0 else 1
                for t in entry["targets"]:
                    pct = (t["hits"] / total) * 100.0
                    lines.append(f" - {t['url']} w={t['weight']} hits={t['hits']} ({pct:.2f}%)")
                return self.respond_text("\n".join(lines))
        # redirecionamento
        target = pick_target_and_count(path)
        if target is None:
            return self.respond_text("Código não encontrado.", status=404)
        if target == "ERR_NO_TARGETS":
            return self.respond_text("Configuração inválida para MULTI (sem targets).", status=500)
        self.send_response(302)
        self.send_header("Location", target)
        # Evita cache do redirect
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        return

    # POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
        except Exception as e:
            return self.respond_text(f"Erro ao ler JSON: {e}", status=400)

        if path == "new":
            urls = payload.get("urls", [])
            weights = payload.get("weights", [])
            custom_code = payload.get("code", None)

            # valida slug
            if custom_code is not None and (not isinstance(custom_code, str) or not validate_slug_path(custom_code)):
                return self.respond_text("Erro: 'code' inválido. Use letras/números/hífen por segmento (1–32), separados por '/'.", status=400)

            # valida URLs/pesos
            if not urls or not isinstance(urls, list):
                return self.respond_text("Erro: 'urls' deve ser lista com ao menos 1 item.", status=400)
            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if not urls:
                return self.respond_text("Erro: nenhuma URL válida em 'urls'.", status=400)
            if not all(is_http_url(u) for u in urls):
                return self.respond_text("Erro: todas as URLs devem começar com http:// ou https://", status=400)

            try:
                wtmp = [float(w) for w in weights] if weights else [1.0] * len(urls)
                weights = [(0.0 if (isinstance(w, float) and w < 0) else (w if isinstance(w, float) else 1.0)) for w in wtmp]
            except Exception:
                return self.respond_text("Erro: 'weights' deve conter números.", status=400)

            if weights and len(weights) != len(urls):
                return self.respond_text("Erro: 'weights' deve ter o mesmo tamanho de 'urls'.", status=400)
            if custom_code and custom_code in RESERVED:
                return self.respond_text("Erro: slug reservado. Escolha outro nome.", status=400)

            try:
                code = create_short(urls, weights, custom_code)
                short = f"{build_short_base(self)}/{code}"
                return self.respond_text(short)
            except ValueError as e:
                return self.respond_text(str(e), status=409)
            except Exception as e:
                return self.respond_text(f"Erro ao criar link: {e}", status=500)

        if path == "update":
            code = payload.get("code")
            new_code = payload.get("new_code", None)
            urls = payload.get("urls", [])
            weights = payload.get("weights", [])

            if not code or not isinstance(code, str):
                return self.respond_text("Erro: 'code' é obrigatório.", status=400)
            if new_code is not None and (not isinstance(new_code, str) or not validate_slug_path(new_code)):
                return self.respond_text("Erro: 'new_code' inválido.", status=400)
            if not urls or not isinstance(urls, list):
                return self.respond_text("Erro: 'urls' deve ser lista com ao menos 1 item.", status=400)
            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if not urls:
                return self.respond_text("Erro: nenhuma URL válida em 'urls'.", status=400)
            if not all(is_http_url(u) for u in urls):
                return self.respond_text("Erro: todas as URLs devem começar com http:// ou https://", status=400)

            try:
                wtmp = [float(w) for w in weights] if weights else [1.0] * len(urls)
                weights = [(0.0 if (isinstance(w, float) and w < 0) else (w if isinstance(w, float) else 1.0)) for w in wtmp]
            except Exception:
                return self.respond_text("Erro: 'weights' deve conter números.", status=400)
            if weights and len(weights) != len(urls):
                return self.respond_text("Erro: 'weights' deve ter o mesmo tamanho de 'urls'.", status=400)
            if new_code and new_code in RESERVED:
                return self.respond_text("Erro: slug reservado.", status=400)

            try:
                code2 = update_short(code, new_code, urls, weights)
                if not code2:
                    return self.respond_text("Código não encontrado.", status=404)
                short = f"{build_short_base(self)}/{code2}"
                return self.respond_text(short)
            except ValueError as e:
                return self.respond_text(str(e), status=409)
            except Exception as e:
                return self.respond_text(f"Erro ao atualizar link: {e}", status=500)

        if path == "delete":
            code = payload.get("code")
            if not code or not isinstance(code, str):
                return self.respond_text("Erro: 'code' é obrigatório.", status=400)
            try:
                delete_short(code)
                return self.respond_text(f"Excluído: {code}")
            except Exception as e:
                return self.respond_text(f"Erro ao excluir: {e}", status=500)

        return self.respond_text("Endpoint POST não encontrado.", status=404)

    # helpers de resposta
    def respond_text(self, text, status=200):
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def respond_html(self, html, status=200):
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

# -------------------- Run --------------------
def run():
    init_db_pool()
    ensure_schema()
    with socketserver.TCPServer((HOST, PORT), ShortenerHandler) as httpd:
        print(f"Servidor rodando em http://{HOST}:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
