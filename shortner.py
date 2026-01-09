
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

INDEX_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>EncCurtador • Painel</</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root { --bg:#0f172a; --card:#111827; --txt:#e5e7eb; --muted:#a1a1aa; --accent:#22c55e; --danger:#ef4444; }
*{box-sizing:border-box} body{margin:0;background:#0b1022;color:var(--txt);font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu}
.container{max-width:1024px;margin:32px auto;padding:0 16px}
h1{font-size:1.6rem;margin:0 0 16px}
.card{background:var(--card);padding:16px;border-radius:12px;border:1px solid #1f2937}
.grid{display:grid;gap:12px}
.grid-2{grid-template-columns:1fr 1fr}
label{display:block;font-size:.9rem;margin-bottom:6px;color:#cbd5e1}
input,select,textarea{width:100%;padding:10px;border-radius:8px;border:1px solid #334155;background:#0b1220;color:var(--txt)}
input[type="number"]{width:100%}
textarea{min-height:80px}
button{padding:10px 14px;border:none;border-radius:8px;cursor:pointer}
.btn{background:#334155;color:#fff}
.btn-primary{background:var(--accent);color:#00150c;font-weight:600}
.btn-danger{background:var(--danger);color:#fff}
.small{font-size:.85rem;color:var(--muted)}
.table{width:100%;border-collapse:collapse;margin-top:12px}
.table th,.table td{border-bottom:1px solid #1f2937;padding:8px;text-align:left;font-size:.92rem}
.row{display:flex;gap:8px;align-items:center}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.8rem;background:#1f2937;color:#cbd5e1}
code{background:#0b1220;padding:2px 6px;border-radius:6px;border:1px solid #1f2937}
footer{margin-top:24px;color:#94a3b8}
hr{border:none;border-top:1px solid #1f2937;margin:16px 0}
.list{display:flex;flex-direction:column;gap:8px}
.item{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#0b1220;padding:8px;border-radius:8px;border:1px solid #1f2937}
.item code{max-width:100%;overflow:auto}
.weight{max-width:120px}
.remove{background:#ef4444}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center}
.modal-content{background:var(--card);padding:16px;border-radius:12px;max-width:900px;width:95%;border:1px solid #1f2937}
.modal-title{font-size:1.2rem;margin:0 0 10px}
.modal-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:12px}
</style>
</head>
<body>
<div class="container">
  <h1>EncCurtador • Painel</h1>

  <div class="card">
    <h2 style="margin-top:0">Criar link curto</h2>
    <div class="grid grid-2">
      <div>
        <label>Slug (opcional)</label>
        <input id="slugCode" placeholder="ex.: PromocaoMercadoPago/Whats" />
        <div class="small">Apenas letras, números e hífen por segmento. Ex.: <code>PromocaoMercadoPago/Whats</code>.</div>
      </div>
      <div class="row" style="align-items:flex-end">
        <label class="badge">Tipo de destino</label>
        <select id="destType">
          <option value="web">Web (URL)</option>
          <option value="wa">WhatsApp (wa.me)</option>
        </select>
      </div>
    </div>

    <!-- WEB FORM -->
    <div id="webForm" class="grid" style="margin-top:10px">
      <div>
        <label>URLs (uma por linha)</label>
        <textarea id="webUrls" placeholder="https://site1.com\nhttps://site2.com"></textarea>
        <div class="small">Todas devem começar com <code>http://</code> ou <code>https://</code>.</div>
      </div>
      <div>
        <label>Pesos (opcional, uma por linha na mesma ordem)</label>
        <textarea id="webWeights" placeholder="50\n30\n20"></textarea>
        <div class="small">Se vazio, peso = 1 para todos.</div>
      </div>
    </div>

    <!-- WHATSAPP FORM -->
    <div id="waForm" class="grid" style="display:none;margin-top:10px">
      <div class="grid grid-2">
        <div>
          <label>DDI</label>
          <input id="waDdi" value="55" />
        </div>
        <div>
          <label>Número (somente dígitos)</label>
          <input id="waNumber" placeholder="41999998888" />
        </div>
      </div>
      <div class="grid grid-2">
        <div>
          <label>Mensagem</label>
          <textarea id="waMsg" placeholder="Olá! Quero aproveitar a promoção."></textarea>
        </div>
        <div>
          <label>Peso do destino</label>
          <input id="waWeight" type="number" min="0" step="1" value="1" />
          <div class="small">Peso 0 = nunca selecionado; valores maiores aumentam a chance.</div>
        </div>
      </div>
      <div class="row">
        <button class="btn" id="addWa">Adicionar destino WhatsApp</button>
        <span class="small">Adicione quantos números quiser. Cada um tem seu próprio peso.</span>
      </div>
      <div id="waList" class="list" style="margin-top:10px"></div>
    </div>

    <div class="row" style="margin-top:12px">
      <button class="btn-primary" id="createBtn">Criar link curto</button>
      <span id="createResult" class="small"></span>
    </div>
  </div>

  <div class="card" style="margin-top:16px">
    <h2 style="margin-top:0">Links criados</h2>
    <table class="table" id="linksTable">
      <thead>
        <tr><th>Código</th><th>Destinos</th><th>Hits</th><th>Ações</th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <div class="row">
      <button class="btn" id="refreshList">Atualizar lista</button>
    </div>
  </div>

  <!-- MODAL DE EDIÇÃO -->
  <div class="modal" id="editModal">
    <div class="modal-content">
      <h3 class="modal-title">Editar link</h3>
      <div class="grid grid-2">
        <div>
          <label>Slug atual</label>
          <input id="editCode" readonly />
          <div class="small">Este é o código atual do link.</div>
        </div>
        <div>
          <label>Novo slug (opcional)</label>
          <input id="editNewCode" placeholder="ex.: Suporte/Whats" />
          <div class="small">Deixe em branco para manter o atual.</div>
        </div>
      </div>
      <div class="grid">
        <div>
          <label>URLs (uma por linha)</label>
          <textarea id="editUrls"></textarea>
          <div class="small">Ex.: <code>https://wa.me/5541999998888?text=...</code> ou <code>https://site.com</code></div>
        </div>
        <div>
          <label>Pesos (uma por linha na mesma ordem das URLs)</label>
          <textarea id="editWeights"></textarea>
          <div class="small">Se vazio, peso = 1 para todos. Valores negativos viram 0.</div>
        </div>
      </div>
      <div class="modal-actions">
        <button class="btn" id="editCancel">Cancelar</button>
        <button class="btn-primary" id="editSave">Salvar alterações</button>
      </div>
      <div class="small" id="editResult"></div>
    </div>
  </div>

  <footer>
    <div>Servidor local. Para compartilhar publicamente, faça deploy (Render/Railway/Heroku).</div>
  </footer>
</div>

<script>
const destType = document.getElementById('destType');
const webForm = document.getElementById('webForm');
const waForm = document.getElementById('waForm');
const waDdi = document.getElementById('waDdi');
const waNumber = document.getElementById('waNumber');
const waMsg = document.getElementById('waMsg');
const waWeight = document.getElementById('waWeight');
const waList = document.getElementById('waList');
const addWa = document.getElementById('addWa');
const createBtn = document.getElementById('createBtn');
const createResult = document.getElementById('createResult');
const linksTableBody = document.querySelector('#linksTable tbody');
const refreshListBtn = document.getElementById('refreshList');
const slugCode = document.getElementById('slugCode');

// Modal edição
const editModal = document.getElementById('editModal');
const editCode = document.getElementById('editCode');
const editNewCode = document.getElementById('editNewCode');
const editUrls = document.getElementById('editUrls');
const editWeights = document.getElementById('editWeights');
const editCancel = document.getElementById('editCancel');
const editSave = document.getElementById('editSave');
const editResult = document.getElementById('editResult');

let waDestinos = []; // {url, phone, weight}

destType.addEventListener('change', () => {
  const v = destType.value;
  webForm.style.display = (v === 'web') ? '' : 'none';
  waForm.style.display = (v === 'wa') ? '' : 'none';
});

addWa.addEventListener('click', () => {
  const ddiClean = (waDdi.value || '').trim().replace(/[^0-9]/g,'');   // só dígitos
  const num = (waNumber.value || '').trim().replace(/[^0-9]/g,'');
  const msgText = (waMsg.value || '').trim();
  let w = parseFloat(waWeight.value);

  if (!ddiClean || !num) {
    alert('Informe DDI e número (apenas dígitos).');
    return;
  }
  if (Number.isNaN(w) || w < 0) w = 1;

  // wa.me exige número internacional sem '+', sem espaços/traços
  const phone = `${ddiClean}${num}`;
  const url = `https://wa.me/${phone}?text=${encodeURIComponent(msgText)}`;

  waDestinos.push({url, phone, weight: w});
  renderWaList();
  waNumber.value = '';
});

function renderWaList() {
  waList.innerHTML = '';
  if (waDestinos.length === 0) {
    waList.innerHTML = '<div class="small">Nenhum destino WhatsApp adicionado.</div>';
    return;
  }
  waDestinos.forEach((d,i)=>{
    const div = document.createElement('div');
    div.className = 'item';
    div.innerHTML = `
      <div style="flex:1 1 300px"><code>${d.phone}</code></div>
      <div class="row">
        <label class="small">Peso</label>
        <input class="weight" type="number" min="0" step="1" value="${d.weight}" />
        <button class="btn-danger remove">Remover</button>
      </div>
    `;
    const weightInput = div.querySelector('.weight');
    weightInput.addEventListener('change', () => {
      let v = parseFloat(weightInput.value);
      if (Number.isNaN(v) || v < 0) v = 0;
      waDestinos[i].weight = v;
    });
    const removeBtn = div.querySelector('.remove');
    removeBtn.addEventListener('click', () => {
      waDestinos.splice(i,1);
      renderWaList();
    });
    waList.appendChild(div);
  });
}

async function criarLinkCurto(urls, weights, code) {
  const payload = { urls, weights };
  if (code && code.trim()) payload.code = code.trim();
  const resp = await fetch('/new', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (!resp.ok) throw new Error(await resp.text());
  return await resp.text();
}

createBtn.addEventListener('click', async () => {
  createResult.textContent = 'Criando...';
  try {
    let urls = [], weights = [];
    const code = slugCode.value || '';

    if (destType.value === 'web') {
      urls = (document.getElementById('webUrls').value || '')
        .split('\\n').map(s=>s.trim()).filter(Boolean);
      const ws = (document.getElementById('webWeights').value || '')
        .split('\\n').map(s=>s.trim()).filter(Boolean);
      weights = ws.map(x => {
        const n = parseFloat(x);
        return Number.isNaN(n) || n < 0 ? 1 : n;
      });
    } else {
      if (!waDestinos.length) throw new Error('Adicione ao menos um número de WhatsApp.');
      urls = waDestinos.map(d => d.url);
      weights = waDestinos.map(d => (Number.isFinite(d.weight) && d.weight >= 0) ? d.weight : 1);
    }

    const short = await criarLinkCurto(urls, weights, code);
    createResult.innerHTML = `✅ Criado: ${short}${short}</a>`;
    if (destType.value === 'wa') { waDestinos = []; renderWaList(); waWeight.value='1'; }
    slugCode.value = '';
    await carregarLista();
  } catch (e) {
    createResult.textContent = 'Erro: ' + e.message;
  }
});

async function carregarLista() {
  const resp = await fetch('/list');
  const text = await resp.text();
  const linhas = text.split('\\n').filter(Boolean);
  linksTableBody.innerHTML = '';
  for (const l of linhas) {
    const code = l.split(' -> ')[0].trim();
    const hitsMatch = l.match(/\\(hits: (\\d+)\\)|\\(total hits: (\\d+)\\)/);
    const hits = hitsMatch ? (hitsMatch[1] || hitsMatch[2]) : '0';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><code>${code}</code></td>
      <td>${l.replace(code + ' -> ', '')}</td>
      <td>${hits}</td>
      <td class="row">
        <button class="btn" onclick="copiar('${location.origin}/${code}')">Copiar</button>
        /${code}Abrir</a>
        /stats/${code}Stats</a>
        <button class="btn" onclick="abrirEdicao('${code}')">Editar</button>
        <button class="btn-danger" onclick="excluirLink('${code}')">Excluir</button>
      </td>
    `;
    linksTableBody.appendChild(tr);
  }
}

function copiar(txt) {
  navigator.clipboard.writeText(txt).then(()=>alert('Link copiado: ' + txt));
}

refreshListBtn.addEventListener('click', carregarLista);
window.addEventListener('load', carregarLista);

// ------- EDIÇÃO -------
async function abrirEdicao(code) {
  editResult.textContent = '';
  editCode.value = code;
  editNewCode.value = '';
  editUrls.value = '';
  editWeights.value = '';
  try {
    const resp = await fetch(`/get/${code}`);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    const urls = (data.type === 'single') ? [data.url] : (data.targets.map(t => t.url));
    const weights = (data.type === 'single') ? [1] : (data.targets.map(t => t.weight || 1));
    editUrls.value = urls.join('\\n');
    editWeights.value = weights.join('\\n');
    editModal.style.display = 'flex';
  } catch (e) {
    alert('Erro ao abrir edição: ' + e.message);
  }
}
editCancel.addEventListener('click', () => {
  editModal.style.display = 'none';
});
editSave.addEventListener('click', async () => {
  editResult.textContent = 'Salvando...';
  const code = editCode.value.trim();
  const newCode = editNewCode.value.trim();
  const urls = (editUrls.value || '').split('\\n').map(s=>s.trim()).filter(Boolean);
  const weights = (editWeights.value || '').split('\\n').map(s=>s.trim()).filter(Boolean)
                    .map(x => { const n = parseFloat(x); return (Number.isNaN(n) || n < 0) ? 1 : n; });
  try {
    const payload = { code, urls, weights };
    if (newCode) payload.new_code = newCode;
    const resp = await fetch('/update', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    const txt = await resp.text();
    if (!resp.ok) throw new Error(txt);
    editResult.textContent = '✅ Alterações salvas: ' + txt;
    await carregarLista();
    setTimeout(()=>{ editModal.style.display='none'; }, 600);
  } catch (e) {
    editResult.textContent = 'Erro: ' + e.message;
  }
});

// ------- EXCLUSÃO -------
async function excluirLink(code) {
  if (!confirm(`Excluir o link '${code}'? Esta ação não pode ser desfeita.`)) return;
  try {
    const resp = await fetch('/delete', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ code })
    });
    const txt = await resp.text();
    if (!resp.ok) throw new Error(txt);
    alert('✅ Excluído: ' + code);
    await carregarLista();
  } catch (e) {
    alert('Erro ao excluir: ' + e.message);
  }
}
</script>
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
