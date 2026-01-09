
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

INDEX_HTML = """

<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>EncCurtador • Painel</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 24px; }
  h1, h2, h3 { margin: 0 0 12px; }
  .card { border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .col { display: flex; flex-direction: column; gap: 6px; }
  label { font-weight: 600; }
  input[type="text"], input[type="number"], textarea, select { padding: 8px; border: 1px solid #ccc; border-radius: 6px; }
  textarea { min-height: 96px; }
  .small { color: #666; font-size: 12px; }
  .btn { padding: 8px 12px; border: 1px solid #555; background: #fff; border-radius: 6px; cursor: pointer; }
  .btn:hover { background: #f5f5f5; }
  .btn-danger { padding: 8px 12px; border: 1px solid #c33; background: #fff; border-radius: 6px; color: #c33; cursor: pointer; }
  .btn-danger:hover { background: #ffecec; }
  table { width: 100%; border-collapse: collapse; }
  th, td { border-bottom: 1px solid #eee; padding: 8px; vertical-align: top; }
  code { background: #f6f8fa; padding: 2px 4px; border-radius: 4px; }
  /* Modal edição */
  #editModal { position: fixed; inset: 0; display: none; align-items: center; justify-content: center; background: rgba(0,0,0,0.35); }
  #editModal .box { background: #fff; border-radius: 8px; padding: 16px; width: 640px; max-width: 95vw; }
</style>
</head>
<body>

<h1>EncCurtador • Painel</h1>

<div class="card">
  <h2>Criar link curto</h2>

  <div class="row">
    <div class="col" style="flex:1 1 260px">
      <label for="slugCode">Slug (opcional)</label>
      <input type="text" id="slugCode" placeholder="Ex.: PromocaoMercadoPago/Whats" />
      <div class="small">Apenas letras, números e hífen por segmento.</div>
    </div>

    <div class="col" style="flex:1 1 180px">
      <label for="destType">Tipo de destino</label>
      <select id="destType">
        <option value="web">Web (URL)</option>
        <option value="wa">WhatsApp (wa.me)</option>
      </select>
    </div>
  </div>

  <!-- Form de URLs (web) -->
  <div id="webForm" class="col" style="margin-top:12px">
    <label for="webUrls">URLs (uma por linha)</label>
    <textarea id="webUrls" placeholder="Todas devem começar com http:// ou https://"></textarea>

    <label for="webWeights">Pesos (opcional, uma por linha na mesma ordem)</label>
    <textarea id="webWeights" placeholder="Se vazio, peso = 1 para todos."></textarea>
  </div>

  <!-- Form de WhatsApp -->
  <div id="waForm" class="col" style="margin-top:12px; display:none">
    <div class="row">
      <div class="col" style="flex:0 0 120px">
        <label for="waDdi">DDI</label>
        <input type="text" id="waDdi" placeholder="Ex.: 55" />
      </div>
      <div class="col" style="flex:1 1 220px">
        <label for="waNumber">Número (somente dígitos)</label>
        <input type="text" id="waNumber" placeholder="Ex.: 41999998888" />
      </div>
      <div class="col" style="flex:1 1 220px">
        <label for="waWeight">Peso do destino</label>
        <input type="number" id="waWeight" value="1" min="0" step="1" />
        <div class="small">Peso 0 = nunca selecionado; valores maiores aumentam a chance.</div>
      </div>
    </div>

    <label for="waMsg">Mensagem</label>
    <textarea id="waMsg" placeholder="Texto que vai após ?text="></textarea>

    <div class="row">
      <button id="addWa" class="btn">Adicionar destino WhatsApp</button>
      <div class="small">Adicione quantos números quiser. Cada um tem seu próprio peso.</div>
    </div>

    <div id="waList" class="col" style="margin-top:8px"></div>
  </div>

  <div class="row" style="margin-top:12px">
    <button id="createBtn" class="btn">Criar link curto</button>
    <div id="createResult" class="small"></div>
  </div>
</div>

<div class="card">
  <h2>Links criados</h2>
  <table id="linksTable">
    <thead>
      <tr>
        <th style="width:160px">Código</th>
        <th>Destinos</th>
        <th style="width:120px">Hits</th>
        <th style="width:360px">Ações</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
  <div class="row" style="margin-top:8px">
    <button id="refreshList" class="btn">Atualizar lista</button>
  </div>
</div>

<!-- Modal de edição -->
<div id="editModal">
  <div class="box">
    <h3>Editar link</h3>
    <div class="col">
      <label for="editCode">Slug atual</label>
      <input id="editCode" type="text" readonly />
      <div class="small">Este é o código atual do link.</div>
    </div>

    <div class="col" style="margin-top:8px">
      <label for="editNewCode">Novo slug (opcional)</label>
      <input id="editNewCode" type="text" placeholder="Deixe em branco para manter o atual." />
    </div>

    <div class="col" style="margin-top:8px">
      <label for="editUrls">URLs (uma por linha)</label>
      <textarea id="editUrls" placeholder="Ex.: https://site.com ou https://wa.me/5511999..."></textarea>
    </div>

    <div class="col" style="margin-top:8px">
      <label for="editWeights">Pesos (uma por linha na mesma ordem das URLs)</label>
      <textarea id="editWeights" placeholder="Se vazio, peso = 1 para todos. Valores negativos viram 0."></textarea>
    </div>

    <div class="row" style="margin-top:8px">
      <button id="editCancel" class="btn">Cancelar</button>
      <button id="editSave" class="btn">Salvar alterações</button>
      <div id="editResult" class="small"></div>
    </div>
  </div>
</div>

<div class="small" style="margin-top:8px">
  Servidor local. Para compartilhar publicamente, faça deploy (Render/Railway/Heroku).
</div>

<script>
  // --- Referências (IDs que você confirmou) ---
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

  // --- Estado ---
  let waDestinos = []; // {url, phone, weight}

  // --- Toggle do tipo de destino (volta a funcionar como antes) ---
  destType.addEventListener('change', () => {
    const v = destType.value;
    webForm.style.display = (v === 'web') ? '' : 'none';
    waForm.style.display = (v === 'wa') ? '' : 'none';
  });

  // --- Adicionar destino WhatsApp ---
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

  // --- Lista visual dos destinos Whats (apenas número) ---
  function renderWaList() {
    waList.innerHTML = '';
    if (waDestinos.length === 0) {
      waList.innerHTML = '<div class="small">Nenhum destino WhatsApp adicionado.</div>';
      return;
    }
    waDestinos.forEach((d,i)=>{
      const div = document.createElement('div');
      div.className = 'item';
      // *** EXIBIÇÃO AJUSTADA: mostrar somente o número (DDI+número)
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

  // --- Criação via backend ---
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
          .split('\n').map(s=>s.trim()).filter(Boolean);
        const ws = (document.getElementById('webWeights').value || '')
          .split('\n').map(s=>s.trim()).filter(Boolean);
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
      // Mantém a mesma saída (sem alterar lógica além da exibição/persistência solicitada)
      createResult.innerHTML = `✅ Criado: ${short}${short}</a>`;
      if (destType.value === 'wa') { waDestinos = []; renderWaList(); waWeight.value='1'; }
      slugCode.value = '';
      await carregarLista();
    } catch (e) {
      createResult.textContent = 'Erro: ' + e.message;
    }
  });

  // --- Lista de links criados (exibição de destinos + hits por destino) ---
  async function carregarLista() {
    const resp = await fetch('/list');
    const text = await resp.text();
    const linhas = text.split('\n').filter(Boolean);
    linksTableBody.innerHTML = '';

    // helper: extrai dígitos de uma URL wa.me
    function waUrlToDigits(url) {
      const after = url.split('wa.me/')[1] || url;
      const pathOnly = after.split('?')[0];
      const digits = (pathOnly.match(/\d+/g) || []).join('');
      return digits || url;
    }

    // helper: monta HTML do destino + hits ao lado (se houver)
    function destinoComHitsHtml(url, hitsDest) {
      const isWa = url.includes('wa.me/');
      const display = isWa ? waUrlToDigits(url) : url;
      const hitsTag = Number.isFinite(hitsDest)
        ? ` <span class="small">· hits: ${hitsDest}</span>`
        : '';
      return `<code>${display}</code>${hitsTag}`;
    }

    for (const l of linhas) {
      const code = l.split(' -> ')[0].trim();

      // total de hits do link (mantém comportamento original)
      const hitsMatch = l.match(/\(hits:\s*(\d+)\)|\(total hits:\s*(\d+)\)/i);
      const hitsTotal = hitsMatch ? parseInt(hitsMatch[1] || hitsMatch[2], 10) : 0;

      // parte após "->"
      const depoisSeta = l.split(' -> ')[1] || '';
      let destinosHtml = '-';

      if (depoisSeta.startsWith('MULTI:')) {
        // remove sufixo "(total hits: N)" e pega itens "url [w=.. hits=..]"
        let parte = depoisSeta.slice('MULTI:'.length)
          .replace(/\s*\(total hits:\s*\d+\)\s*$/i, '')
          .trim();

        // divide por vírgula
        const itens = parte.split(/\s*,\s*/).filter(Boolean);

        const linhasDestinos = itens.map(item => {
          // captura URL e o número de hits dentro dos colchetes
          const m = item.match(/^(https?:\/\/[^\s]+)\s*\[(.*?)\]$/i);
          const url = m ? m[1] : item;
          let hitsDest = null;
          if (m && m[2]) {
            const h = m[2].match(/hits\s*=\s*(\d+)/i);
            if (h) hitsDest = parseInt(h[1], 10);
          }
          return destinoComHitsHtml(url, hitsDest);
        });

        destinosHtml = linhasDestinos.join('<br>');
      } else if (depoisSeta) {
        // SINGLE: "URL (hits: N)" — usamos N como hits do único destino
        const url = depoisSeta.replace(/\s*\(hits:\s*\d+\)\s*$/i, '').trim();
        destinosHtml = destinoComHitsHtml(url, hitsTotal);
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><code>${code}</code></td>
        <td>${destinosHtml}</td>   <!-- 'Destinos' com número/URL + hits por destino -->
        <td>${hitsTotal}</td>      <!-- 'Hits' total do link (inalterado) -->
        <td class="row">
          <button class="btn" onclick="copiar('${location.origin}/${code}')">Copiar</button>
          ${location.origin}/${code}Abrir</a>
          ${location.origin}/stats/${code}Stats</a>
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

  // Garante que na carga inicial a UI mostre o formulário correto (web/wa)
  window.addEventListener('load', () => {
    carregarLista();
    destType.dispatchEvent(new Event('change'));
  });

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
      editUrls.value = urls.join('\n');
      editWeights.value = weights.join('\n');
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
    const urls = (editUrls.value || '').split('\n').map(s=>s.trim()).filter(Boolean);
    const weights = (editWeights.value || '').split('\n').map(s=>s.trim()).filter(Boolean)
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
