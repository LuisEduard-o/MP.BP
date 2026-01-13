
# shortner.py  (psycopg 3 + UI + Autenticação segura com PBKDF2 + Sessões HttpOnly)
import http.server
from socketserver import ThreadingTCPServer
import urllib.parse
import os
import time
import random
import re
import json
import hmac
import hashlib
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# -------------------- Config --------------------
HOST = "0.0.0.0"
PORT = int(os.getenv("PORT", "8000"))  # Render define PORT automaticamente
DATABASE_URL = os.getenv("DATABASE_URL")  # defina no Render (Internal Database URL)

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
RESERVED = {"new", "list", "stats", "help", "index.html", "get", "update", "delete", "login", "logout"}

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")  # se None, geramos e exibimos nos logs
SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

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
        # Em produção, Render/Proxy cuida de HTTPS
        scheme = "https" if COOKIE_SECURE else "http"
        return f"{scheme}://{host_hdr}"
    return f"http://{HOST}:{PORT}"

# -------------------- HTML: UI & Login --------------------
INDEX_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8" />
<title>Encurtador • Painel</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: system-ui, Arial, sans-serif; margin: 20px; }
  h2 { margin-top: 24px; }
  input, textarea, select, button { padding: 8px; margin: 4px 0; width: 100%; max-width: 680px; }
  .row { display:flex; gap:8px; flex-wrap: wrap; align-items:center; }
  .btn { padding: 8px 12px; cursor:pointer; border:1px solid #ccc; background:#f7f7f7; border-radius:6px; }
  .btn-danger { padding: 8px 12px; cursor:pointer; border:1px solid #e66; background:#ffe5e5; border-radius:6px; }
  table { border-collapse: collapse; width: 100%; margin-top: 8px; }
  th, td { border: 1px solid #ddd; padding: 8px; vertical-align: top; }
  code { background: #f2f2f2; padding: 2px 4px; border-radius: 4px; }
  .topbar { display:flex; align-items:center; justify-content:space-between; gap:12px; }
</style>
</head>
<body>
  <div class="topbar">
    <h1>Encurtador • Painel</h1>
    <button class="btn" onclick="logout()">Sair</button>
  </div>

  <h2>Criar link curto</h2>
  <div class="row">
    <label>Slug (opcional)</label>
    <input id="slug" placeholder="PromocaoMercadoPago/Whats" />
  </div>

  <div class="row">
    <label>Tipo de destino</label>
    <select id="tipo">
      <option value="web">Web (URL)</option>
      <option value="wa">WhatsApp (wa.me)</option>
    </select>
  </div>

  <div class="row">
    <label>URLs (uma por linha)</label>
    <textarea id="urls" rows="4" placeholder="https://exemplo.com\nhttps://outro.com"></textarea>
  </div>

  <div class="row">
    <label>Pesos (opcional, na mesma ordem)</label>
    <textarea id="weights" rows="3" placeholder="1\n3\n2"></textarea>
  </div>

  <div class="row">
    <button class="btn" onclick="criar()">Criar link curto</button>
  </div>

  <h2>Links criados</h2>
  <div class="row">
    <button class="btn" onclick="carregarLista()">Atualizar lista</button>
  </div>

  <table>
    <thead>
      <tr>
        <th>Código</th>
        <th>Destinos</th>
        <th>Hits</th>
        <th>Ações</th>
      </tr>
    </thead>
    <tbody id="linksTableBody"></tbody>
  </table>

  <h3>Editar link</h3>
  <div class="row">
    <label>Slug atual</label>
    <input id="editCode" placeholder="código atual" />
  </div>
  <div class="row">
    <label>Novo slug (opcional)</label>
    <input id="newCode" placeholder="novo código (opcional)" />
  </div>
  <div class="row">
    <label>URLs (uma por linha)</label>
    <textarea id="editUrls" rows="4" placeholder="https://wa.me/5541999998888?text=..."></textarea>
  </div>
  <div class="row">
    <label>Pesos (uma por linha)</label>
    <textarea id="editWeights" rows="3" placeholder="1\n2\n1"></textarea>
  </div>
  <div class="row">
    <button class="btn" onclick="salvarEdicao()">Salvar alterações</button>
  </div>

<script>
async function logout() {
  try {
    await fetch('/logout', { method: 'POST' });
  } catch (e) {}
  location.href = '/login';
}

async function criar() {
  const slug = document.getElementById('slug').value.trim();
  const tipo = document.getElementById('tipo').value;
  let urls = document.getElementById('urls').value.split('\\n').map(s => s.trim()).filter(Boolean);
  const weights = document.getElementById('weights').value.split('\\n').map(s => s.trim()).filter(Boolean).map(Number);

  if (tipo === 'wa') {
    // aceita linhas como "https://wa.me/55DDDNÚMERO?text=..."
    urls = urls.map(u => u.startsWith('http') ? u : 'https://wa.me/' + u);
  }

  const payload = { urls, weights };
  if (slug) payload.code = slug;

  const r = await fetch('/new', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  const t = await r.text();
  if (!r.ok) { alert(t); return; }
  alert('Criado: ' + t);
  carregarLista();
}

async function carregarLista() {
  const r = await fetch('/list');
  const text = await r.text();
  if (!r.ok) {
    alert(text || 'Erro ao carregar lista');
    if (r.status === 401 || r.status === 403) location.href = '/login';
    return;
  }
  const linhas = text.split('\\n').filter(Boolean);
  const linksTableBody = document.getElementById('linksTableBody');
  linksTableBody.innerHTML = '';

  for (const l of linhas) {
    const code = l.split(' -> ')[0].trim();

    const totalHitsMatch = l.match(/(?:total\\s*)?hits\\s*[:=]\\s*(\\d+)/i);
    const hitsTotal = totalHitsMatch ? totalHitsMatch[1] : '0';

    const texto = l.replace(code + ' -> ', '');
    const semMulti = texto.replace(/^\\s*MULTI:\\s*/i, '');
    const itens = semMulti.split(',').map(s => s.trim()).filter(Boolean);

    const pares = itens.map(str => {
      const numeroMatch = str.match(/wa\\.me\\/(\\d+)/i);
      const numero = numeroMatch ? numeroMatch[1] : '';

      const hitsMatch = str.match(/hits\\s*[:=]\\s*(\\d+)/i);
      const hits = hitsMatch ? hitsMatch[1] : '0';

      return { numero, hits };
    }).filter(p => p.numero);

    const listaNumeroHitsHTML = pares.map(p => `<p>${p.numero} HITS = ${p.hits}</p>`).join('');

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${code}</code></td>
      <td>${listaNumeroHitsHTML}</td>
      <td>${hitsTotal}</td>
      <td class="row">
        <button class="btn" onclick="copiar('${location.origin}/${code}')">Copiar</button>
        /${code}Abrir</a>
        <atats/${code}Stats</a>
        <button class="btn" onclick="abrirEdicao('${code}')">Editar</button>
        <button class="btn-danger" onclick="excluirLink('${code}')">Excluir</button>
      </td>
    `;
    linksTableBody.appendChild(tr);
  }
}

async function copiar(texto) {
  try { await navigator.clipboard.writeText(texto); alert('Copiado!'); }
  catch { alert('Falha ao copiar'); }
}

function abrirEdicao(code) { document.getElementById('editCode').value = code; }

async function salvarEdicao() {
  const code = document.getElementById('editCode').value.trim();
  const new_code = document.getElementById('newCode').value.trim();
  const urls = document.getElementById('editUrls').value.split('\\n').map(s => s.trim()).filter(Boolean);
  const weights = document.getElementById('editWeights').value.split('\\n').map(s => s.trim()).filter(Boolean).map(Number);

  const r = await fetch('/update', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ code, new_code, urls, weights })
  });
  const t = await r.text();
  if (!r.ok) { alert(t); return; }
  alert('Atualizado: ' + t);
  carregarLista();
}

async function excluirLink(code) {
  if (!confirm('Excluir ' + code + '?')) return;
  const r = await fetch('/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({code}) });
  const t = await r.text();
  if (!r.ok) { alert(t); return; }
  alert(t);
  carregarLista();
}

window.addEventListener('load', carregarLista);
</script>
</body>
</html>
"""

LOGIN_HTML = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8" />
<title>Login • Encurtador</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<style>
  body { font-family: system-ui, Arial, sans-serif; margin: 20px; display:flex; align-items:center; justify-content:center; min-height:100vh; }
  .card { width: 360px; border:1px solid #ddd; border-radius:8px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,.05); }
  input, button { width:100%; padding:10px; margin-top:10px; }
  h2 { margin:0 0 10px 0; }
  .msg { color:#d00; min-height:20px; }
</style>
</head>
<body>
  <div class="card">
    <h2>Entrar</h2>
    <div class="msg" id="msg"></div>
    <input id="user" placeholder="Usuário" autocomplete="username" />
    <input id="password" type="password" placeholder="Senha" autocomplete="current-password" />
    <button onclick="logar()">Entrar</button>
  </div>
<script>
async function logar() {
  const user = document.getElementById('user').value.trim();
  const password = document.getElementById('password').value;
  const r = await fetch('/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({user, password})
  });
  if (r.ok) location.href = '/';
  else document.getElementById('msg').textContent = await r.text();
}
</script>
</body>
</html>
"""

# -------------------- DB Pool & Schema --------------------
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definido nas variáveis de ambiente.")

DB_POOL = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=20)

def ensure_schema():
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            # tabelas do encurtador
            cur.execute("""
            CREATE TABLE IF NOT EXISTS urls (
              code TEXT PRIMARY KEY,
              type TEXT NOT NULL CHECK (type IN ('single','multi')),
              url TEXT,
              created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
              hits BIGINT NOT NULL DEFAULT 0
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS targets (
              id SERIAL PRIMARY KEY,
              code TEXT NOT NULL REFERENCES urls(code) ON DELETE CASCADE,
              url TEXT NOT NULL,
              weight DOUBLE PRECISION NOT NULL DEFAULT 1.0,
              hits BIGINT NOT NULL DEFAULT 0
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS counters (
              name TEXT PRIMARY KEY,
              value BIGINT NOT NULL
            );
            """)
            cur.execute("""
            INSERT INTO counters(name, value) VALUES ('short_counter', 1000)
            ON CONFLICT (name) DO NOTHING;
            """)

            # autenticação
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
              id SERIAL PRIMARY KEY,
              username TEXT UNIQUE NOT NULL,
              password_salt TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
              last_login_at TIMESTAMPTZ
            );
            """)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TIMESTAMPTZ NOT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
              ip TEXT,
              user_agent TEXT
            );
            """)
            # usuário inicial
            cur.execute("SELECT COUNT(*) FROM users;")
            count = cur.fetchone()[0]
            if count == 0:
                # cria admin
                pwd = ADMIN_PASSWORD if ADMIN_PASSWORD else _generate_password()
                salt_hex, hash_hex = hash_password(pwd)
                cur.execute(
                    "INSERT INTO users(username, password_salt, password_hash) VALUES (%s,%s,%s) RETURNING id;",
                    (ADMIN_USER, salt_hex, hash_hex)
                )
                admin_id = cur.fetchone()[0]
                print("="*60)
                print(f"Usuário admin criado: {ADMIN_USER}")
                if ADMIN_PASSWORD:
                    print("Senha definida pelo ambiente (ADMIN_PASSWORD).")
                else:
                    print(f"Senha gerada (anote com segurança): {pwd}")
                print("="*60)
        conn.commit()

def next_code() -> str:
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE counters SET value = value + 1 WHERE name = 'short_counter' RETURNING value;")
            val = cur.fetchone()[0]
        conn.commit()
    return base62_encode(val)

# -------------------- Password hashing --------------------
def _generate_password(length: int = 14) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^&*?"
    return "".join(random.choice(alphabet) for _ in range(length))

def hash_password(password: str) -> tuple[str, str]:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 200_000)
    return salt.hex(), dk.hex()

def verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    test = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 200_000)
    return hmac.compare_digest(test, expected)

# -------------------- CRUD de links --------------------
def get_entry(code: str):
    with DB_POOL.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM urls WHERE code = %s;", (code,))
            url_row = cur.fetchone()
            if not url_row:
                return None
            if url_row["type"] == "single":
                return {
                    "type": "single",
                    "url": url_row["url"],
                    "hits": url_row["hits"],
                    "created_at": url_row["created_at"]
                }
            else:
                cur.execute("SELECT id, url, weight, hits FROM targets WHERE code = %s ORDER BY id;", (code,))
                targets = cur.fetchall()
                return {
                    "type": "multi",
                    "targets": targets,
                    "hits": url_row["hits"],
                    "created_at": url_row["created_at"]
                }

def list_all():
    out = []
    with DB_POOL.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM urls ORDER BY created_at DESC;")
            for u in cur.fetchall():
                if u["type"] == "single":
                    out.append({"code": u["code"], "type": "single", "url": u["url"], "hits": u["hits"]})
                else:
                    cur.execute("SELECT url, weight, hits FROM targets WHERE code = %s ORDER BY id;", (u["code"],))
                    ts = cur.fetchall()
                    out.append({"code": u["code"], "type": "multi", "targets": ts, "hits": u["hits"]})
    return out

def create_short(urls, weights, custom_code=None):
    with DB_POOL.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # reaproveitar se mesma configuração já existe
            cur.execute("SELECT code, type, url, hits FROM urls;")
            existing = cur.fetchall()
            for e in existing:
                code = e["code"]
                if e["type"] == "single" and len(urls) == 1 and e["url"] == urls[0]:
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
                cur.execute("SELECT 1 FROM urls WHERE code = %s;", (custom_code,))
                if cur.fetchone():
                    raise ValueError("Erro: slug já está em uso.")
                code = custom_code
            else:
                code = next_code()

            # inserir
            if len(urls) == 1:
                cur.execute("INSERT INTO urls(code, type, url) VALUES (%s,'single',%s);", (code, urls[0]))
            else:
                cur.execute("INSERT INTO urls(code, type, url) VALUES (%s,'multi',NULL);", (code,))
                for u, w in zip(urls, weights):
                    cur.execute("INSERT INTO targets(code, url, weight, hits) VALUES (%s,%s,%s,0);", (code, u, float(w)))
        conn.commit()
    return code

def update_short(code, new_code, urls, weights):
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT type FROM urls WHERE code = %s;", (code,))
            row = cur.fetchone()
            if not row:
                return None
            # renomear
            if new_code and new_code != code:
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

def delete_short(code):
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM urls WHERE code=%s;", (code,))
        conn.commit()

def pick_target_and_count(code):
    """Seleciona destino e incrementa hits (público, sem auth)."""
    with DB_POOL.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
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

# -------------------- Sessões / Cookies --------------------
def new_session(user_id: int, ip: str | None, user_agent: str | None) -> str:
    token = os.urandom(32).hex()
    expires = datetime.utcnow() + timedelta(hours=SESSION_TTL_HOURS)
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions(token, user_id, expires_at, ip, user_agent) VALUES (%s,%s,%s,%s,%s);",
                (token, user_id, expires, ip, user_agent)
            )
    return token

def get_session_user(token: str | None):
    if not token:
        return None
    with DB_POOL.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at < NOW();")  # limpeza simples
            cur.execute("""
                SELECT u.id, u.username
                FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token = %s AND s.expires_at > NOW();
            """, (token,))
            return cur.fetchone()

def destroy_session(token: str | None):
    if not token:
        return
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token = %s;", (token,))

# -------------------- HTTP Handler --------------------
class ShortenerHandler(http.server.SimpleHTTPRequestHandler):

    # -------- Helpers de resposta --------
    def send_json(self, raw_json, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw_json.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(raw_json.encode("utf-8"))

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
        # evitar cache da UI
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location: str, status=302):
        self.send_response(status)
        self.send_header("Location", location)
        self.end_headers()

    # -------- Cookies / Auth --------
    def get_cookie(self, name: str):
        cookies = self.headers.get("Cookie", "")
        for c in cookies.split(";"):
            c = c.strip()
            if not c: continue
            if "=" not in c: continue
            k, v = c.split("=", 1)
            if k.strip() == name:
                return v.strip()
        return None

    def set_session_cookie(self, token: str):
        parts = [f"session={token}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if COOKIE_SECURE:
            parts.append("Secure")
        # Opcional: Max-Age conforme SESSION_TTL_HOURS
        max_age = SESSION_TTL_HOURS * 3600
        parts.append(f"Max-Age={max_age}")
        self.send_header("Set-Cookie", "; ".join(parts))

    def clear_session_cookie(self):
        parts = ["session=; Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
        if COOKIE_SECURE:
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def current_user(self):
        token = self.get_cookie("session")
        return get_session_user(token)

    def require_auth_api(self) -> bool:
        """Para endpoints de API (retorna 401 em vez de redirecionar)."""
        if self.current_user():
            return True
        self.respond_text("Não autorizado.", status=401)
        return False

    def require_auth_page(self) -> bool:
        """Para páginas HTML (redireciona ao /login)."""
        if self.current_user():
            return True
        self.redirect("/login")
        return False

    # -------------------- GET --------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")

        # Página de login (pública). Se já logado, manda pro painel.
        if path == "login":
            if self.current_user():
                return self.redirect("/")
            return self.respond_html(LOGIN_HTML)

        # Painel exige login
        if path == "" or path == "index.html":
            if not self.require_auth_page():
                return
            return self.respond_html(INDEX_HTML)

        # Ajuda: pode deixar pública ou protegida
        if path == "help":
            return self.respond_text(
                "Encurtador (psycopg 3 / PostgreSQL)\n"
                "Endpoints:\n"
                " POST /login { user, password }\n"
                " POST /logout\n"
                " POST /new { urls:[...], weights:[...], code?:slug }\n"
                " POST /update { code, new_code?, urls, weights }\n"
                " POST /delete { code }\n"
                " GET /list (autenticado)\n"
                " GET /get/{code} (autenticado)\n"
                " GET /stats/{code} (autenticado)\n"
                " GET /{code} (público)\n"
            )

        # Lista exige login
        if path == "list":
            if not self.require_auth_api():
                return
            data = list_all()
            lines = []
            for e in data:
                if e["type"] == "single":
                    lines.append(f"{e['code']} -> {e['url']} (hits: {e['hits']})")
                else:
                    parts = [f"{t['url']} [w={t['weight']} hits={t['hits']}]" for t in e["targets"]]
                    lines.append(f"{e['code']} -> MULTI: {', '.join(parts)} (total hits: {e['hits']})")
            return self.respond_text("\n".join(lines) if lines else "Sem links ainda.")

        # GET /get/{code} (autenticado)
        if path.startswith("get/"):
            if not self.require_auth_api():
                return
            code = path.split("/", 1)[1] if "/" in path else ""
            if not code:
                return self.respond_text("Uso: /get/{code}", status=400)
            entry = get_entry(code)
            if not entry:
                return self.respond_text("Código não encontrado.", status=404)
            raw = json.dumps({"code": code, **entry}, ensure_ascii=False, default=str)
            return self.send_json(raw)

        # GET /stats/{code} (autenticado)
        if path.startswith("stats/"):
            if not self.require_auth_api():
                return
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

        # Redirecionamento público
        target = pick_target_and_count(path)
        if target is None:
            return self.respond_text("Código não encontrado.", status=404)
        if target == "ERR_NO_TARGETS":
            return self.respond_text("Configuração inválida para MULTI (sem targets).", status=500)
        self.send_response(302)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()

    # -------------------- POST --------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")

        # Leitura do corpo JSON
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
        except Exception as e:
            return self.respond_text(f"Erro ao ler JSON: {e}", status=400)

        # ---- Auth: login/logout ----
        if path == "login":
            user = payload.get("user", "")
            pwd = payload.get("password", "")
            if not user or not pwd:
                return self.respond_text("Usuário e senha são obrigatórios.", status=400)
            with DB_POOL.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("SELECT id, username, password_salt, password_hash FROM users WHERE username = %s;", (user,))
                    u = cur.fetchone()
                    if not u or not verify_password(pwd, u["password_salt"], u["password_hash"]):
                        return self.respond_text("Login inválido.", status=403)
                    # ok: cria sessão
                    token = new_session(u["id"], self.client_address[0] if self.client_address else None, self.headers.get("User-Agent"))
                    # atualiza last_login
                    with conn.cursor() as cur2:
                        cur2.execute("UPDATE users SET last_login_at = NOW() WHERE id = %s;", (u["id"],))
                    conn.commit()
            self.send_response(200)
            self.set_session_cookie(token)
            self.end_headers()
            self.wfile.write(b"OK")
            return

        if path == "logout":
            token = self.get_cookie("session")
            destroy_session(token)
            self.send_response(200)
            self.clear_session_cookie()
            self.end_headers()
            self.wfile.write(b"OK")
            return

        # ---- Demais endpoints exigem auth ----
        if path in {"new", "update", "delete"}:
            if not self.require_auth_api():
                return

        if path == "new":
            urls = payload.get("urls", [])
            weights = payload.get("weights", [])
            custom_code = payload.get("code", None)

            if custom_code is not None and (not isinstance(custom_code, str) or not validate_slug_path(custom_code)):
                return self.respond_text("Erro: 'code' inválido. Use letras/números/hífen por segmento (1–32), separados por '/'.", status=400)
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

# -------------------- Run --------------------
def run():
    ensure_schema()
    with ThreadingTCPServer((HOST, PORT), ShortenerHandler) as httpd:
        print(f"Servidor rodando em http://{HOST}:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
``
