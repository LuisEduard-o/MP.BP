
# shortner.py
import http.server
import socketserver
import urllib.parse
import json
import os
import time
import threading
import random
import re

HOST = "0.0.0.0"  # escuta todas as interfaces
PORT = int(os.getenv("PORT", 8000))
DB_FILE = "db.json"

ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
RESERVED = {"new", "list", "stats", "help", "index.html"}

def base62_encode(n: int) -> str:
    if n == 0:
        return ALPHABET[0]
    s = []
    base = len(ALPHABET)
    while n > 0:
        n, r = divmod(n, base)
        s.append(ALPHABET[r])
    return "".join(reversed(s))

def load_db():
    if not os.path.exists(DB_FILE):
        return {"counter": 1000, "urls": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

DB_LOCK = threading.Lock()

def is_http_url(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")

def build_short_base(handler: http.server.BaseHTTPRequestHandler) -> str:
    host_hdr = handler.headers.get("Host")
    if host_hdr:
        return f"http://{host_hdr}"
    return f"http://{HOST}:{PORT}"

def validate_slug_path(slug: str) -> bool:
    """
    Valida slug com múltiplos segmentos separados por '/'.
    Cada segmento: [A-Za-z0-9-], 1..32 chars.
    Ex.: 'PromocaoMercadoPago/Whats'
    """
    if not isinstance(slug, str):
        return False
    if len(slug) < 1 or len(slug) > 128:
        return False
    # Sem barras duplicadas, sem barras no início/fim
    if slug.startswith("/") or slug.endswith("/") or "//" in slug:
        return False
    segments = slug.split("/")
    for seg in segments:
        if seg in RESERVED:
            return False
        if not re.fullmatch(r"[A-Za-z0-9-]{1,32}", seg):
            return False
    return True

INDEX_HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>EncCurtador • Painel</title>
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
        <div class="small">Apenas letras, números e hífen por segmento. Use '/' para separar. Ex.: <code>PromocaoMercadoPago/Whats</code>.</div>
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
      <div style="flex:1 1 300px"><code>${d.url}</code></div>
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
</script>
</html>
"""

class ShortenerHandler(http.server.SimpleHTTPRequestHandler):
    # ---------- GET ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")
        params = urllib.parse.parse_qs(parsed.query)

        # UI
        if path == "" or path == "index.html":
            return self.respond_html(INDEX_HTML)

        # Ajuda em texto
        if path == "help":
            return self.respond_text(
                "EncCurtador ativo!\n"
                "Uso via API:\n"
                "  POST /new  (JSON: { urls: [...], weights: [...], code?: 'slug/optional' })\n"
                "  GET  /list\n"
                "  GET  /stats/<code>\n"
                "  GET  /<code>\n"
            )

        # Lista
        if path == "list":
            with DB_LOCK:
                db = load_db()
                lines = []
                for code, entry in db["urls"].items():
                    if entry.get("type") == "single":
                        lines.append(f"{code} -> {entry['url']} (hits: {entry['hits']})")
                    else:
                        parts = []
                        for t in entry["targets"]:
                            parts.append(f"{t['url']} [w={t.get('weight',1)} hits={t['hits']}]")
                        lines.append(f"{code} -> MULTI: {', '.join(parts)} (total hits: {entry['hits']})")
                return self.respond_text("\n".join(lines) if lines else "Sem links ainda.")

        # Stats
        if path.startswith("stats/"):
            code = path.split("/", 1)[1] if "/" in path else ""
            if not code:
                return self.respond_text("Uso: /stats/<code>", status=400)
            with DB_LOCK:
                db = load_db()
                entry = db["urls"].get(code)
            if not entry:
                return self.respond_text("Código não encontrado.", status=404)

            if entry.get("type") == "single":
                text = (
                    f"Código: {code}\n"
                    f"Tipo: SINGLE\n"
                    f"URL: {entry['url']}\n"
                    f"Hits: {entry['hits']}\n"
                    f"Criado em: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['created_at']))}\n"
                )
                return self.respond_text(text)
            else:
                lines = [
                    f"Código: {code}",
                    "Tipo: MULTI",
                    f"Criado em: " + time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(entry['created_at'])),
                    f"Total hits: {entry['hits']}",
                    "Destinos:"
                ]
                total = entry["hits"] if entry["hits"] > 0 else 1
                for t in entry["targets"]:
                    pct = (t["hits"] / total) * 100.0
                    lines.append(f"  - {t['url']} | w={t.get('weight',1)} | hits={t['hits']} ({pct:.2f}%)")
                return self.respond_text("\n".join(lines))

        # Redirecionamento
        with DB_LOCK:
            db = load_db()
            entry = db["urls"].get(path)

        if entry:
            if entry.get("type") == "single":
                target_url = entry["url"]
                with DB_LOCK:
                    db["urls"][path]["hits"] += 1
                    save_db(db)
                self.send_response(301)
                self.send_header("Location", target_url)
                self.end_headers()
                return
            else:
                targets = entry.get("targets", [])
                if not targets:
                    return self.respond_text("Configuração inválida para MULTI (sem targets).", status=500)
                weights = [t.get("weight", 1.0) for t in targets]
                if sum(weights) == 0:
                    weights = [1.0] * len(targets)
                idx = random.choices(range(len(targets)), weights=weights, k=1)[0]
                target = targets[idx]["url"]
                with DB_LOCK:
                    db["urls"][path]["hits"] += 1
                    db["urls"][path]["targets"][idx]["hits"] += 1
                    save_db(db)
                self.send_response(301)
                self.send_header("Location", target)
                self.end_headers()
                return
        else:
            self.respond_text("Código não encontrado.", status=404)

    # ---------- POST ----------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.lstrip("/")

        if path == "new":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                payload = json.loads(raw)
            except Exception as e:
                return self.respond_text(f"Erro ao ler JSON: {e}", status=400)

            urls = payload.get("urls", [])
            weights = payload.get("weights", [])
            custom_code = payload.get("code", None)

            print("[POST /new] urls:", urls)
            print("[POST /new] weights:", weights)
            print("[POST /new] code:", custom_code)

            # valida slug opcional (múltiplos segmentos)
            if custom_code is not None:
                if not isinstance(custom_code, str) or not validate_slug_path(custom_code):
                    return self.respond_text(
                        "Erro: 'code' inválido. Use apenas letras, números e hífen por segmento, separado por '/', 1–32 chars cada. "
                        "Ex.: PromocaoMercadoPago/Whats",
                        status=400
                    )

            # valida URLs/pesos
            if not urls or not isinstance(urls, list):
                return self.respond_text("Erro: 'urls' deve ser lista com ao menos 1 item.", status=400)

            urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
            if not urls:
                return self.respond_text("Erro: nenhuma URL válida em 'urls'.", status=400)

            if not all(is_http_url(u) for u in urls):
                return self.respond_text("Erro: todas as URLs devem começar com http:// ou https://", status=400)

            if weights and (not isinstance(weights, list) or len(weights) != len(urls)):
                return self.respond_text("Erro: 'weights' deve ter o mesmo tamanho de 'urls'.", status=400)
            try:
                wtmp = [float(w) for w in weights] if weights else [1.0] * len(urls)
                weights = [ (0.0 if (isinstance(w, float) and w < 0) else (w if isinstance(w, float) else 1.0)) for w in wtmp ]
            except Exception:
                return self.respond_text("Erro: 'weights' deve conter números.", status=400)

            with DB_LOCK:
                db = load_db()

                # Se veio custom_code, checar reserva/duplicidade
                if custom_code:
                    if custom_code in RESERVED:
                        return self.respond_text("Erro: slug reservado. Escolha outro nome.", status=400)
                    if custom_code in db["urls"]:
                        return self.respond_text("Erro: slug já está em uso. Escolha outro.", status=409)
                    code = custom_code
                else:
                    # gerar base62 e ainda reaproveitar se já existe igual
                    for c, entry in db["urls"].items():
                        if entry.get("type") == "multi":
                            ex_urls = [t["url"] for t in entry.get("targets", [])]
                            ex_weights = [t.get("weight", 1.0) for t in entry.get("targets", [])]
                            if ex_urls == urls and ex_weights == weights:
                                short = f"{build_short_base(self)}/{c}"
                                return self.respond_text(short)
                        elif entry.get("type") == "single":
                            if len(urls) == 1 and entry["url"] == urls[0]:
                                short = f"{build_short_base(self)}/{c}"
                                return self.respond_text(short)
                    db["counter"] += 1
                    code = base62_encode(db["counter"])

                # criar
                if len(urls) == 1:
                    db["urls"][code] = {
                        "type": "single",
                        "url": urls[0],
                        "created_at": time.time(),
                        "hits": 0
                    }
                else:
                    targets = [{"url": u, "weight": w, "hits": 0} for u, w in zip(urls, weights)]
                    db["urls"][code] = {
                        "type": "multi",
                        "targets": targets,
                        "created_at": time.time(),
                        "hits": 0
                    }

                save_db(db)
                short = f"{build_short_base(self)}/{code}"

            return self.respond_text(short)

        return self.respond_text("Endpoint POST não encontrado.", status=404)

    # ---------- helpers ----------
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
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

def run():
    with socketserver.TCPServer((HOST, PORT), ShortenerHandler) as httpd:
        print(f"Servidor rodando em http://{HOST}:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()
