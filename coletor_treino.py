"""
Coletor de TREINAMENTO — modo histórico sem filtros.

Roda com janela ampla (default: 7 dias), SEM whitelist, SEM IA, SEM classificação.
Gera 2 arquivos:
- data/treinamento-YYYY-MM-DD.json  → todas as notícias brutas dedupadas
- revisao-YYYY-MM-DD.html           → tela interativa pra Juliana marcar

Uso:
  python3 coletor_treino.py            (janela padrão de 7 dias)
  python3 coletor_treino.py 14         (14 dias)

Depois de rodar:
  open revisao-YYYY-MM-DD.html        (abre a tela no navegador)
"""

import os
import sys
import json
import html as html_lib
from datetime import datetime

# Reaproveita funções do coletor principal
from coletor import (
    KEYWORDS,
    buscar_google_news,
    deduplicar,
    parsear_rss,
    RSS_FEEDS_DIRETOS,
    buscar_rss_direto,
    dentro_da_janela,
)


def main():
    # Janela em dias (default 7, override via arg)
    dias = 7
    if len(sys.argv) > 1:
        try:
            dias = int(sys.argv[1])
        except ValueError:
            print(f"Erro: argumento '{sys.argv[1]}' não é número. Usando 7.")

    hoje = datetime.now().strftime("%Y-%m-%d")

    print(f"\n📰 COLETOR DE TREINAMENTO")
    print(f"📅 Data: {hoje}  |  Janela: últimos {dias} dias")
    print(f"⚠️  Modo bruto: SEM whitelist, SEM IA, SEM classificação")
    print(f"🎯 Objetivo: você revisa manualmente pra ensinar o sistema\n")

    todas = []

    # === Google News por keyword ===
    total_termos = sum(len(v) for v in KEYWORDS.values())
    print(f"━━━ GOOGLE NEWS ({total_termos} termos) ━━━")
    for categoria, termos in KEYWORDS.items():
        for termo in termos:
            items = buscar_google_news(termo, dias)
            for it in items:
                it["categoria_termo"] = categoria
            todas.extend(items)
        print(f"  {categoria}: cumulativo {len(todas)} resultados")

    # === RSS direto ===
    print(f"\n━━━ RSS DIRETO ({len(RSS_FEEDS_DIRETOS)} fontes) ━━━")
    for fonte_nome, url in RSS_FEEDS_DIRETOS:
        raw = buscar_rss_direto(fonte_nome, url)
        em_janela = [i for i in raw if dentro_da_janela(i, dias)]
        for it in em_janela:
            it["categoria_termo"] = "RSS_DIRETO"
        todas.extend(em_janela)
        print(f"  • {fonte_nome:<25} {len(em_janela)} items em janela")

    print(f"\n📊 Total bruto: {len(todas)}")
    unicas = deduplicar(todas)
    print(f"🧹 Após dedup:  {len(unicas)}")

    # Ordena por data desc (mais recente primeiro) e depois por fonte
    unicas.sort(key=lambda n: (n.get("pub_date") or "", n.get("source") or ""), reverse=True)

    # === Salva JSON ===
    os.makedirs("data", exist_ok=True)
    json_path = f"data/treinamento-{hoje}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "window_days": dias,
            "total": len(unicas),
            "news": unicas,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n✅ JSON salvo:  {json_path}")

    # === Gera HTML de revisão ===
    html_path = f"revisao-{hoje}.html"
    gerar_html_revisao(unicas, hoje, dias, html_path)
    print(f"✅ HTML salvo:  {html_path}")
    print(f"\n👉 Próximo passo: abra a tela de revisão com")
    print(f"   open {html_path}")
    print(f"   (ou duplo-clique no arquivo no Finder)\n")


def gerar_html_revisao(noticias: list, data_str: str, dias: int, output_path: str):
    """Gera HTML self-contained com as notícias embutidas e UI de revisão."""

    # Serializa só os campos que a UI precisa
    dados_ui = [
        {
            "i": idx,
            "t": n.get("title", ""),
            "u": n.get("link", ""),
            "s": n.get("source", "?"),
            "d": n.get("pub_date", ""),
            "k": n.get("search_term", ""),
        }
        for idx, n in enumerate(noticias)
    ]
    dados_json = json.dumps(dados_ui, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8" />
<title>Revisão de Treinamento - """ + data_str + """</title>
<style>
:root {
  --black: #000000;
  --orange: #FF6200;
  --green: #57B49A;
  --red: #cc2222;
  --gray: #BFBFBF;
  --gray-bg: #f5f5f5;
  --gray-light: #ededed;
  --yellow-soft: #fff7e0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Arial, Helvetica, sans-serif;
  background: var(--gray-bg);
  color: var(--black);
  padding: 20px;
  line-height: 1.4;
}
.container { max-width: 900px; margin: 0 auto; }

header {
  background: var(--black);
  color: white;
  padding: 18px 24px;
  border-radius: 10px;
  margin-bottom: 16px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
header h1 { font-size: 18px; }
header h1 .accent { color: var(--orange); }
.progress {
  background: var(--orange);
  padding: 8px 16px;
  border-radius: 8px;
  font-weight: 700;
  font-size: 13px;
}

.controls {
  background: white;
  border: 1px solid var(--gray-light);
  border-radius: 10px;
  padding: 14px 18px;
  margin-bottom: 14px;
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
  font-size: 13px;
}
.controls .label { color: #666; font-weight: 600; margin-right: 4px; }
.controls .key {
  display: inline-block;
  background: var(--gray-light);
  border: 1px solid var(--gray);
  border-radius: 4px;
  padding: 2px 8px;
  font-family: monospace;
  font-size: 12px;
  margin: 0 4px;
}
.btn {
  padding: 8px 16px;
  border-radius: 8px;
  border: none;
  font-family: inherit;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
}
.btn-primary { background: var(--orange); color: white; }
.btn-secondary { background: white; color: var(--black); border: 1px solid var(--gray); }
.btn-secondary:hover { background: var(--gray-light); }

.card {
  background: white;
  border: 1px solid var(--gray-light);
  border-radius: 10px;
  padding: 20px 22px;
  margin-bottom: 12px;
}
.card .meta {
  font-size: 12px;
  color: #666;
  margin-bottom: 8px;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.card .meta .source { font-weight: 700; color: var(--black); }
.card .meta .term { background: var(--yellow-soft); padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.card h3 {
  font-size: 16px;
  font-weight: 700;
  line-height: 1.4;
  margin-bottom: 10px;
}
.card a {
  font-size: 12px;
  color: var(--orange);
  word-break: break-all;
  text-decoration: none;
}
.card a:hover { text-decoration: underline; }
.actions {
  display: flex;
  gap: 8px;
  margin-top: 14px;
}
.actions button {
  flex: 1;
  padding: 12px;
  font-size: 14px;
  font-weight: 700;
  border: 2px solid;
  border-radius: 8px;
  cursor: pointer;
  font-family: inherit;
  background: white;
  transition: all 0.15s;
}
.actions .keep { border-color: var(--green); color: var(--green); }
.actions .keep:hover, .actions .keep.selected { background: var(--green); color: white; }
.actions .drop { border-color: var(--red); color: var(--red); }
.actions .drop:hover, .actions .drop.selected { background: var(--red); color: white; }
.actions .skip { border-color: var(--gray); color: #666; }
.actions .skip:hover, .actions .skip.selected { background: var(--gray); color: white; }

.card.kept { border-left: 4px solid var(--green); }
.card.dropped { border-left: 4px solid var(--red); opacity: 0.6; }
.card.skipped { border-left: 4px solid var(--gray); }

.shortcuts-hint {
  text-align: center;
  font-size: 12px;
  color: #666;
  margin-top: 20px;
  padding: 10px;
  background: white;
  border-radius: 8px;
}

.done-banner {
  display: none;
  background: var(--green);
  color: white;
  padding: 18px;
  border-radius: 10px;
  text-align: center;
  font-weight: 700;
  margin-bottom: 14px;
}
.done-banner.show { display: block; }
.done-banner button {
  margin-left: 12px;
  background: white;
  color: var(--green);
  border: none;
  padding: 8px 16px;
  border-radius: 6px;
  font-weight: 700;
  cursor: pointer;
  font-family: inherit;
}

@media (max-width: 600px) {
  body { padding: 10px; }
  .card { padding: 14px 16px; }
  header h1 { font-size: 16px; }
  .actions button { font-size: 13px; padding: 10px; }
}
</style>
</head>
<body>
<div class="container">

  <header>
    <div>
      <h1>Revisão de Treinamento <span class="accent">·</span> """ + data_str + """</h1>
      <div style="font-size: 12px; opacity: 0.8; margin-top: 4px">Últimos """ + str(dias) + """ dias · sem filtros</div>
    </div>
    <div class="progress" id="progress">0 / 0</div>
  </header>

  <div class="done-banner" id="doneBanner">
    Revisão completa.
    <button onclick="exportar()">Exportar JSON</button>
  </div>

  <div class="controls">
    <span class="label">Atalhos:</span>
    <span class="key">1</span> Entra
    <span class="key">2</span> Descarta
    <span class="key">3</span> Pular
    <span class="key">↑</span> Voltar
    <button class="btn btn-primary" onclick="exportar()" style="margin-left: auto">Exportar progresso</button>
    <button class="btn btn-secondary" onclick="limpar()">Reiniciar</button>
  </div>

  <div id="lista"></div>

  <div class="shortcuts-hint">
    Suas decisões ficam salvas automaticamente neste navegador.<br>
    Pode fechar e retomar depois. Quando terminar, clique em "Exportar".
  </div>

</div>

<script>
const NOTICIAS = """ + dados_json + """;
const STORAGE_KEY = 'revisao_""" + data_str + """';

function carregar() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}'); }
  catch(e) { return {}; }
}
function salvar(dec) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(dec));
}

let decisoes = carregar();
let cursor = parseInt(localStorage.getItem(STORAGE_KEY + '_cursor') || '0');

function decidir(idx, valor) {
  decisoes[idx] = valor;
  salvar(decisoes);
  const card = document.querySelector(`.card[data-i="${idx}"]`);
  if (card) {
    card.className = 'card ' + (valor === 'keep' ? 'kept' : valor === 'drop' ? 'dropped' : 'skipped');
    card.querySelectorAll('.actions button').forEach(b => b.classList.remove('selected'));
    card.querySelector(`.actions .${valor}`).classList.add('selected');
  }
  atualizarProgresso();
  if (idx === cursor) {
    cursor++;
    localStorage.setItem(STORAGE_KEY + '_cursor', String(cursor));
    focarProximo();
  }
}

function atualizarProgresso() {
  const decididas = Object.keys(decisoes).length;
  document.getElementById('progress').textContent = `${decididas} / ${NOTICIAS.length}`;
  if (decididas >= NOTICIAS.length) {
    document.getElementById('doneBanner').classList.add('show');
  }
}

function focarProximo() {
  // Encontra primeira sem decisão a partir de cursor
  let idx = cursor;
  while (idx < NOTICIAS.length && decisoes[idx]) idx++;
  if (idx >= NOTICIAS.length) {
    window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
    return;
  }
  const card = document.querySelector(`.card[data-i="${idx}"]`);
  if (card) card.scrollIntoView({behavior: 'smooth', block: 'center'});
  cursor = idx;
  localStorage.setItem(STORAGE_KEY + '_cursor', String(cursor));
}

function voltar() {
  if (cursor > 0) cursor--;
  localStorage.setItem(STORAGE_KEY + '_cursor', String(cursor));
  const card = document.querySelector(`.card[data-i="${cursor}"]`);
  if (card) card.scrollIntoView({behavior: 'smooth', block: 'center'});
}

function exportar() {
  const out = {
    data: '""" + data_str + """',
    total: NOTICIAS.length,
    decisoes_count: Object.keys(decisoes).length,
    decisoes: NOTICIAS.map(n => ({
      title: n.t,
      source: n.s,
      link: n.u,
      search_term: n.k,
      decisao: decisoes[n.i] || 'pending',
    })),
  };
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'revisao-""" + data_str + """-decisoes.json';
  a.click();
}

function limpar() {
  if (!confirm('Apagar todas as decisões deste dia? Não dá pra desfazer.')) return;
  localStorage.removeItem(STORAGE_KEY);
  localStorage.removeItem(STORAGE_KEY + '_cursor');
  location.reload();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

function renderizar() {
  const lista = document.getElementById('lista');
  NOTICIAS.forEach(n => {
    const dec = decisoes[n.i];
    const card = document.createElement('div');
    card.className = 'card' + (dec === 'keep' ? ' kept' : dec === 'drop' ? ' dropped' : dec === 'skip' ? ' skipped' : '');
    card.dataset.i = n.i;
    card.innerHTML = `
      <div class="meta">
        <span class="source">${escapeHtml(n.s)}</span>
        ${n.d ? '<span>' + escapeHtml(n.d.substring(0,10)) + '</span>' : ''}
        ${n.k ? '<span class="term">' + escapeHtml(n.k) + '</span>' : ''}
      </div>
      <h3>${escapeHtml(n.t)}</h3>
      <a href="${escapeHtml(n.u)}" target="_blank">${escapeHtml(n.u)}</a>
      <div class="actions">
        <button class="keep ${dec === 'keep' ? 'selected' : ''}" onclick="decidir(${n.i}, 'keep')">Entra (1)</button>
        <button class="drop ${dec === 'drop' ? 'selected' : ''}" onclick="decidir(${n.i}, 'drop')">Descarta (2)</button>
        <button class="skip ${dec === 'skip' ? 'selected' : ''}" onclick="decidir(${n.i}, 'skip')">Pular (3)</button>
      </div>
    `;
    lista.appendChild(card);
  });
  atualizarProgresso();
  setTimeout(focarProximo, 200);
}

document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (cursor >= NOTICIAS.length) return;
  if (e.key === '1') { decidir(cursor, 'keep'); e.preventDefault(); }
  else if (e.key === '2') { decidir(cursor, 'drop'); e.preventDefault(); }
  else if (e.key === '3') { decidir(cursor, 'skip'); e.preventDefault(); }
  else if (e.key === 'ArrowUp') { voltar(); e.preventDefault(); }
});

renderizar();
</script>
</body>
</html>
"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
