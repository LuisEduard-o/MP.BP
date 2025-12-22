
# shortner.py (psycopg 3 + UI completa restaurada)
import http.server
from socketserver import ThreadingTCPServer
import urllib.parse
import os
import time
import random
import re
import json

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

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
        return f"http://{host_hdr}"
    return f"http://{HOST}:{PORT}"

# -------------------- HTML UI completa --------------------
INDEX_HTML = r"""
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8"/>
<title>EncCurtador • Painel</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
:root{
  --bg:#0f172a; --panel:#111827; --muted:#6b7280; --border:#1f2937;
  --accent:#22c55e; --accent2:#3b82f6; --danger:#ef4444; --txt:#e5e7eb;
}
*{box-sizing:border-box}
body{font-family:system-ui,Segoe UI,Roboto,Arial,sans-serif;background:var(--bg);color:var(--txt);margin:0}
.container{max-width:1100px;margin:32px auto;padding:0 16px}
h1{margin:0 0 12px;font-size:28px}
small{color:var(--muted)}
.panel{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px;margin:16px 0}
.panel h2{margin:0 0 12px;font-size:20px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
label{display:block;font-size:14px;color:#cbd5e1;margin:8px 0 4px}
input,textarea,select,button{width:100%;font-size:15px;padding:10px;border-radius:8px;border:1px solid var(--border);background:#0b1020;color:var(--txt)}
textarea{min-height:90px}
button{cursor:pointer;border:0}
.btn{background:var(--accent);color:#021308;font-weight:600}
.btn.secondary{background:var(--accent2);color:#04121f}
.btn.danger{background:var(--danger);color:#2b0b0b}
.btn.muted{background:#1f2937;color:#cbd5e1}
.row{display:flex;gap:10px;align-items:center}
.flex-1{flex:1}
code,pre{background:#0b1020;border:1px solid var(--border);padding:10px;border-radius:8px;color:#93c5fd;overflow:auto}
table{width:100%;border-collapse:collapse;margin-top:8px}
th,td{border:1px solid var(--border);padding:8px;text-align:left}
th{background:#0b1020}
.tag{display:inline-block;background:#0b3b20;color:#93f2ae;border:1px solid #135c35;padding:2px 8px;border-radius:999px;font-size:12px}
.badge{display:inline-block;background:#0b223b;color:#8ec7ff;border:1px solid #114a7b;padding:2px 8px;border-radius:999px;font-size:12px}
.copy{display:inline-flex;gap:8px;align-items:center;margin-top:8px}
hr{border:0;border-top:1px solid var(--border);margin:16px 0}
.footer{color:var(--muted);font-size:13px;text-align:center;margin:24px 0}
a{color:#93c5fd}
.hidden{display:none}
</style>
</head>
<body>
<div class="container">
  <h1>EncCurtador • Painel</h1>
  <small>Crie links curtos, distribua entre múltiplos destinos (com pesos) e veja estatísticas.</small>

  <!-- Criar link curto -->
  <div class="panel">
    <h2>Criar link curto</h2>
    <div class="grid">
      <div>
        <label>Slug (opcional)</label>
        <input id="new-code" placeholder="Ex.: PromocaoMercadoPago/Whats"/>
        <small class="muted">Use letras, números e hífen por segmento (1–32), separados por '/'.</small>
        <hr>
        <label>Tipo de destino</label>
        <div class="row">
          <label class="row"><input type="radio" name="tipo" value="web" checked> <span>&nbsp;Web (URL)</span></label>
          <label class="row"><input type="radio" name="tipo" value="wa"> <span>&nbsp;WhatsApp (wa.me)</span></label>
        </div>
        <div id="web-box">
          <label>URLs (uma por linha)</label>
          <textarea id="web-urls" placeholder="https://site.com&#10;https://outro.com"></textarea>
          <label>Pesos (uma por linha na mesma ordem)</label>
          <textarea id="web-weights" placeholder="Se vazio, peso = 1 para todos."></textarea>
        </div>
        <div id="wa-box" class="hidden">
          <div class="row">
            <div class="flex-1">
              <label>DDI</label>
              <input id="wa-ddi" value="55"/>
            </div>
            <div class="flex-1">
              <label>Número (somente dígitos)</label>
              <input id="wa-number" placeholder="41999998888"/>
            </div>
          </div>
          <label>Mensagem</label>
          <textarea id="wa-message" placeholder="Digite a mensagem que será enviada..."></textarea>
          <label>Peso</label>
          <input id="wa-weight" type="number" step="0.1" value="1"/>
          <div class="row">
            <button class="btn secondary" id="wa-add">Adicionar destino WhatsApp</button>
            <button class="btn muted" id="wa-clear">Limpar lista</button>
          </div>
          <table id="wa-table" class="hidden">
            <thead><tr><th>Destino (wa.me)</th><th>Peso</th><th>Ações</th></tr></thead>
            <tbody></tbody>
          </table>
          <small class="muted">Adicione quantos números quiser. Cada um tem seu próprio peso.</small>
        </div>
        <hr>
        <button class="btn" id="create-btn">Criar link curto</button>
        <div id="create-result" class="hidden">
          <div class="copy">
            <code id="create-url"></code>
            <button class="btn secondary" id="copy-btn">Copiar</button>
            <a id="open-btn" class="badge" target="_blank">Abrir</a>
          </div>
          <small class="muted">Compartilhe este link com seus clientes.</small>
        </div>
      </div>

      <!-- Ajuda / instruções -->
      <div>
        <pre>
API:
POST /new      { urls:[...], weights:[...], code?: 'slug/opcional' }
POST /update   { code, new_code?, urls:[...], weights:[...] }
POST /delete   { code }
GET  /list
GET  /get/{code}
GET  /stats/{code}
GET  /{code}   (redireciona)
        </pre>
        <div class="tag">Dica</div>
        <small>Para WhatsApp, o destino é: <code>https://wa.me/DDINUMERO?text=MENSAGEM</code>. O painel monta isso para você.</small>
      </div>
    </div>
  </div>

  <!-- Links criados -->
  <div class="panel">
    <h2>Links criados</h2>
    <div class="row">
      <button class="btn secondary" id="refresh-list">Atualizar lista</button>
      <button class="btn muted" id="clear-list">Limpar</button>
    </div>
    <pre id="list-box" class="hidden"></pre>
  </div>

  <!-- Editar link -->
  <div class="panel">
    <h2>Editar link</h2>
    <div class="grid">
      <div>
        <label>Slug atual</label>
        <input id="edit-code" placeholder="Ex.: G9 ou Promocao/Whats"/>
        <label>Novo slug (opcional)</label>
        <input id="edit-new-code" placeholder="Deixe em branco para manter."/>
        <label>URLs (uma por linha)</label>
        <textarea id="edit-urls" placeholder="https://wa.me/5541999998888?text=...&#10;https://site.com"></textarea>
        <label>Pesos (uma por linha na mesma ordem das URLs)</label>
        <textarea id="edit-weights" placeholder="Se vazio, peso = 1 para todos. Valores negativos viram 0."></textarea>
        <div class="row">
          <button class="btn secondary" id="edit-save">Salvar alterações</button>
          <button class="btn muted" id="edit-cancel">Cancelar</button>
        </div>
      </div>
      <div>
        <div class="row">
          <div class="flex-1">
            <label>Excluir por slug</label>
            <input id="del-code" placeholder="Ex.: G9"/>
          </div>
        </div>
        <button class="btn danger" id="del-btn">Excluir</button>
        <hr>
        <label>Stats de um código</label>
        <div class="row">
          <input id="stats-code" class="flex-1" placeholder="Ex.: G9"/>
          <button class="btn secondary" id="stats-btn">Ver stats</button>
        </div>
        <pre id="stats-box" class="hidden"></pre>
        <hr>
        <label>Get JSON de um código</label>
        <div class="row">
          <input id="get-code" class="flex-1" placeholder="Ex.: G9"/>
          <button class="btn secondary" id="get-btn">Ver JSON</button>
        </div>
        <pre id="get-box" class="hidden"></pre>
      </div>
    </div>
  </div>

  <div class="footer">
    Servidor local. Para compartilhar publicamente, faça deploy (Render).  
    Respostas de redirecionamento enviam <code>Cache-Control: no-store</code>.
  </div>
</div>

<script>
(function(){
  const q = (sel)=>document.querySelector(sel);
  const qa = (sel)=>Array.from(document.querySelectorAll(sel));
  const show = (el)=>el.classList.remove('hidden');
  const hide = (el)=>el.classList.add('hidden');

  // Alterna caixas Web/WhatsApp
  qa('input[name="tipo"]').forEach(r=>{
    r.addEventListener('change', ()=>{
      const isWeb = q('input[name="tipo"]:checked').value === 'web';
      (isWeb?show:hide)(q('#web-box'));
      (!isWeb?show:hide)(q('#wa-box'));
    });
  });

  // Lista de destinos WhatsApp (em memória no navegador)
  const waList = [];
  function renderWaTable(){
    const tbl = q('#wa-table');
    const tbody = tbl.querySelector('tbody');
    tbody.innerHTML = '';
    if (waList.length === 0){ hide(tbl); return; }
    show(tbl);
    waList.forEach((item, idx)=>{
      const tr = document.createElement('tr');
      const url = `https://wa.me/${item.ddi}${item.number}?text=${encodeURIComponent(item.message)}`;
      tr.innerHTML = `
        <td><code>${url}</code></td>
        <td>${item.weight}</td>
        <td><button class="btn danger" data-idx="${idx}">Remover</button></td>
      `;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll('button[data-idx]').forEach(btn=>{
      btn.addEventListener('click', ()=>{
        const i = parseInt(btn.getAttribute('data-idx'));
        waList.splice(i,1);
        renderWaTable();
      });
    });
  }
  q('#wa-add').addEventListener('click', ()=>{
    const ddi = q('#wa-ddi').value.trim();
    const number = q('#wa-number').value.trim();
    const message = q('#wa-message').value.trim();
    let weight = parseFloat(q('#wa-weight').value || '1');
    if (!ddi || !number || !message){ alert('Preencha DDI, número e mensagem.'); return; }
    if (!/^\d+$/.test(ddi) || !/^\d+$/.test(number)){ alert('DDI e número devem conter apenas dígitos.'); return; }
    if (!(weight>=0)){ weight = 1; }
    waList.push({ddi, number, message, weight});
    renderWaTable();
  });
  q('#wa-clear').addEventListener('click', ()=>{
    waList.length = 0; renderWaTable();
  });

  // Criar link
  q('#create-btn').addEventListener('click', async ()=>{
    const code = q('#new-code').value.trim() || null;
    const tipo = q('input[name="tipo"]:checked').value;
    let urls = [], weights = [];
    if (tipo === 'web'){
      urls = q('#web-urls').value.split('\n').map(s=>s.trim()).filter(Boolean);
      weights = q('#web-weights').value.split('\n').map(s=>s.trim()).filter(Boolean).map(parseFloat);
      if (weights.length === 0) weights = Array(urls.length).fill(1.0);
    } else {
      if (waList.length === 0){ alert('Adicione ao menos um destino WhatsApp.'); return; }
      urls = waList.map(item => `https://wa.me/${item.ddi}${item.number}?text=${encodeURIComponent(item.message)}`);
      weights = waList.map(item => parseFloat(item.weight||1));
    }
    if (urls.length === 0){ alert('Informe ao menos uma URL.'); return; }
    if (weights.length !== urls.length){ alert('Pesos devem ter o mesmo número de linhas das URLs.'); return; }

    try{
      const res = await fetch('/new', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ urls, weights, code })
      });
      const txt = await res.text();
      if (!res.ok){ alert(txt); return; }
      q('#create-url').textContent = txt;
      q('#open-btn').href = txt;
      show(q('#create-result'));
    }catch(e){ alert('Erro ao criar link: '+e.message); }
  });

  // Copiar link
  q('#copy-btn').addEventListener('click', async ()=>{
    const v = q('#create-url').textContent;
    try{ await navigator.clipboard.writeText(v); alert('Copiado!'); }
    catch{ alert('Não foi possível copiar.'); }
  });

  // Lista
  q('#refresh-list').addEventListener('click', async ()=>{
    try{
      const res = await fetch('/list');
      const txt = await res.text();
      q('#list-box').textContent = txt || 'Sem links ainda.';
      show(q('#list-box'));
    }catch(e){ alert('Erro ao listar: '+e.message); }
  });
  q('#clear-list').addEventListener('click', ()=>{
    hide(q('#list-box')); q('#list-box').textContent='';
  });

  // Editar
  q('#edit-save').addEventListener('click', async ()=>{
    const code = q('#edit-code').value.trim();
    const new_code = q('#edit-new-code').value.trim() || null;
    let urls = q('#edit-urls').value.split('\n').map(s=>s.trim()).filter(Boolean);
    let weights = q('#edit-weights').value.split('\n').map(s=>s.trim()).filter(Boolean).map(parseFloat);
    if (!code){ alert("Informe o slug atual."); return; }
    if (urls.length === 0){ alert("Informe ao menos uma URL."); return; }
    if (weights.length === 0) weights = Array(urls.length).fill(1.0);
    if (weights.length !== urls.length){ alert("Pesos devem ter o mesmo número de linhas das URLs."); return; }
    try{
      const res = await fetch('/update', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ code, new_code, urls, weights })
      });
      const txt = await res.text();
      if (!res.ok){ alert(txt); return; }
      alert('Atualizado! Novo link: ' + txt);
    }catch(e){ alert('Erro ao atualizar: '+e.message); }
  });
  q('#edit-cancel').addEventListener('click', ()=>{
    q('#edit-code').value=''; q('#edit-new-code').value='';
    q('#edit-urls').value=''; q('#edit-weights').value='';
  });

  // Excluir
  q('#del-btn').addEventListener('click', async ()=>{
    const code = q('#del-code').value.trim();
    if (!code){ alert('Informe o slug para excluir.'); return; }
    if (!confirm(`Excluir '${code}'?`)) return;
    try{
      const res = await fetch('/delete', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ code })
      });
      const txt = await res.text();
      if (!res.ok){ alert(txt); return; }
      alert(txt);
    }catch(e){ alert('Erro ao excluir: '+e.message); }
  });

  // Stats
  q('#stats-btn').addEventListener('click', async ()=>{
    const code = q('#stats-code').value.trim();
    if (!code){ alert('Informe o código.'); return; }
    try{
      const res = await fetch('/stats/' + encodeURIComponent(code));
      const txt = await res.text();
      q('#stats-box').textContent = txt;
      show(q('#stats-box'));
    }catch(e){ alert('Erro ao obter stats: '+e.message); }
  });

  // Get JSON
  q('#get-btn').addEventListener('click', async ()=>{
    const code = q('#get-code').value.trim();
    if (!code){ alert('Informe o código.'); return; }
    try{
      const res = await fetch('/get/' + encodeURIComponent(code));
      const txt = await res.text();
      q('#get-box').textContent = txt;
      show(q('#get-box'));
    }catch(e){ alert('Erro ao obter JSON: '+e.message); }
  });
})();
</script>
</body>
</html>
"""

# -------------------- DB Pool & Schema (psycopg 3) --------------------
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL não definido nas variáveis de ambiente.")

DB_POOL = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=20)

def ensure_schema():
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS urls (
                  code        TEXT PRIMARY KEY,
                  type        TEXT NOT NULL CHECK (type IN ('single','multi')),
                  url         TEXT,
                  created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            cur.execute("""
                CREATE TABLE IF NOT EXISTS counters (
                  name   TEXT PRIMARY KEY,
                  value  BIGINT NOT NULL
                );
            """)
            cur.execute("""
                INSERT INTO counters(name, value)
                VALUES ('short_counter', 1000)
                ON CONFLICT (name) DO NOTHING;
            """)
        conn.commit()

def next_code() -> str:
    with DB_POOL.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE counters SET value = value + 1 WHERE name = 'short_counter' RETURNING value;")
            val = cur.fetchone()[0]
        conn.commit()
    return base62_encode(val)

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
    """Seleciona destino e incrementa hits."""
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
                "EncCurtador (psycopg 3 / PostgreSQL)\n"
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
            return self.send_json(raw)
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

    # helpers de resposta
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

# -------------------- Run --------------------
def run():
    ensure_schema()
    with ThreadingTCPServer((HOST, PORT), ShortenerHandler) as httpd:
        print(f"Servidor rodando em http://{HOST}:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
