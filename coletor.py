"""
Coletor de notícias - Real Estate Brasil (Fase 1)
Busca notícias no Google News RSS para uma lista de palavras-chave,
deduplica, classifica por categoria e salva em JSON.

Uso: python3 coletor.py
Saída: data/clipping-YYYY-MM-DD.json
"""

import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
import json
import re
import os
import sys
import time
import warnings
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

# Suprime warnings visuais do urllib3 sobre LibreSSL no macOS — não afeta funcionamento
warnings.filterwarnings("ignore", message=".*OpenSSL.*")
warnings.filterwarnings("ignore", category=Warning, module="urllib3")

# =============================================================
# CONFIGURAÇÃO — palavras-chave por categoria
# =============================================================
# Cada empresa é buscada por: (a) nome qualificado e (b) ticker quando aplicável.
# Termos genéricos foram qualificados pra reduzir ruído (ex: "MRV" → "MRV Engenharia").

KEYWORDS = {
    "HOMEBUILDERS": [
        # Empresas (nome qualificado + ticker quando aplicável)
        "Cyrela", "CYRE3",
        "EZ Tec", "Eztec", "EZTC3",
        "Even construtora", "EVEN3",
        "Helbor", "HBOR3",
        "Mitre Realty", "MTRE3",
        "Moura Dubeux", "MDNE3",
        "Melnick", "MELK3",
        "Lavvi", "LAVV3",
        "LPS Brasil", "Lopes corretora", "LPSB3",
        "Trisul", "TRIS3",
        "Cury construtora", "CURY3",
        "Direcional Engenharia", "DIRR3",
        "MRV Engenharia", "MRVE3",
        "Plano&Plano", "PLPL3",
        "Construtora Tenda", "TEND3",
        # Temas
        "imóveis residenciais",
        "mercado imobiliário residencial",
        "FGTS habitação",
        "Minha Casa Minha Vida",
        "construtora Brasil",
        "incorporadora imobiliária",
        "lançamentos imobiliários",
        "VSO vendas sobre oferta",
        "INCC",
        # "Casa Verde Amarela" removida — 0 keeps em 27 (programa legado)
        "Caixa habitacional",
        "crédito imobiliário",
        "SFH sistema financeiro habitação",
        # "LCI letra de crédito imobiliário" removido — 0/14 keeps
        "LIG letra imobiliária garantida",
        "poupança SBPE",
    ],
    "MALLS": [
        # Empresas
        # "Iguatemi shopping" removido (0/46 keeps — trazia varejo/marcas).
        # "Allos shopping" removido (0/12 keeps).
        # Mantemos só os tickers + nome corporativo qualificado.
        "Iguatemi Empresa", "IGTI11",
        "Allos shoppings resultados", "ALOS3",
        "Multiplan", "MULT3",
        "JHSF", "JHSF3",
        "LOG Commercial Properties", "LOGG3",
        "BR Properties", "BRPR3",
        "São Carlos Empreendimentos", "SCAR3",
        "GLP logística Brasil",
        "Bresco logística", "BRCO11",
        "BRC logística",
        "Prologis Brasil",
        "Cyrela Commercial Properties", "CCPR3",
        "JFL Living",
        "Vitacon",
        "General Shopping", "GSHP3",
        "Hines real estate",
        "HSI gestora imobiliária",
        # Temas
        "shopping center Brasil",
        "galpão logístico",
        "lajes corporativas",
        "escritórios São Paulo",
        "fundo imobiliário shopping",
        "vacância escritórios",
        "ABL área bruta locável",
        # "condomínio logístico" removido — 0/15 keeps
        "cap rate imóveis",
        "NOI shopping",
    ],
    # REAL_ESTATE removida — foco em HOMEBUILDERS e MALLS apenas.
    # Notícias macro relevantes (MCMV, FGTS, INCC, crédito imobiliário, Caixa habitacional)
    # ficam dentro de HOMEBUILDERS porque impactam direto as construtoras.
}

# Janela de tempo: 1 dia normal, 3 dias na segunda-feira (cobre fim de semana)
def janela_dias():
    return 3 if datetime.today().weekday() == 0 else 1


# RSS feeds diretos — só MetroQuadrado (única que validou na revisão da Juliana: 80% keep).
# Os outros 6 feeds (Folha, InfoMoney, Money Times, Estadão E-Investidor, Valor)
# trouxeram 100+ notícias gerais e ~0 relevantes — não compensam.
RSS_FEEDS_DIRETOS = [
    ("MetroQuadrado", "https://metroquadrado.com/feed/"),
]

# Keywords pra filtrar título dos feeds gerais — usa todas as keywords de busca em minúsculas
def _keywords_para_match_titulo():
    todas = set()
    for cat_keys in KEYWORDS.values():
        for k in cat_keys:
            todas.add(k.lower())
    # Adiciona hints específicos do setor
    todas.update([
        "imóvel", "imóveis", "imobiliária", "imobiliário",
        "construção civil", "habitacional", "moradia",
        "fundo imobiliário", "fii", "real estate",
        "incorporação", "lançamento residencial",
    ])
    return todas

# Whitelist de fontes — dividida em 2 conjuntos para evitar matches incorretos.

# Fontes com nomes ambíguos: comparação EXATA (case-insensitive).
# Ex: "folha" sozinho pegaria "Folha Extra" e "brasilemfolhas.com.br".
FONTES_EXATAS = {
    "folha de s.paulo", "folha de são paulo", "folha de sp", "folha online",
    "valor econômico", "valor investe", "valor",
    "estadão", "estadao",
    "o estado de s. paulo", "o estado de s.paulo",
    "o globo", "globo", "blog do moreira",
    "veja", "veja são paulo", "veja sao paulo",
    "g1",
    "cnn brasil",  # voltou: 3 keeps na revisão da Juliana (2026-06-03)
    # Adições validadas na revisão da Juliana (taxa keep ≥30%):
    "portas.com.br",
    "advfn", "advfn brasil",
    "financenews.com.br",
    "capital aberto",
    "guia do investidor",
    "secovi-sp", "secovi sp",
    "gazeta mercantil",
    "spacemoney",
    "ipotex", "fdr.com.br",
    # Removidas após revisão:
    # - "estadão e-investidor": 0/8 keeps na revisão
    # - Estadão E-Investidor vinha mais de outras notícias de varejo/IR
}

# Fontes com nomes únicos: comparação por SUBSTRING (case-insensitive).
# Volume expandido — IA filtra ruído depois.
FONTES_SUBSTRING = {
    # Financeiros institucionais
    "infomoney", "info money", "exame", "neofeed", "brazil journal",
    "money times", "moneytimes", "broadcast", "pipeline",
    "bloomberg línea", "bloomberg linea", "reuters",
    "investnews",
    "valor investe", "valor pro",
    # Removidas após revisão (zero keep):
    # - "e-investidor"/"einvestidor": rejeitada (vinha de Estadão E-Investidor com 0/8 keeps)
    # - Suno: maioria FIIs específicos fora do escopo
    # - Investing.com: vaza REITs americanos
    # - Funds Explorer: foca em FIIs específicos
    "empiricus",
    # Especializados em real estate
    "metroquadrado", "metro quadrado", "siila", "buildings",
    "construção mercado", "construcao mercado",
    "aecweb",
    # Adicionados após revisão da Juliana (taxa keep ≥25%):
    "mercado&consumo", "mercado e consumo",
    "forbes brasil",
    "nord investimentos",
    "pipelinevalor", "pipeline valor",
    # Outros relevantes
    "uol economia", "uol mercado",
}

# =============================================================
# FETCH RSS
# =============================================================
def buscar_google_news(termo: str, dias: int = 1) -> list:
    """Busca o termo no Google News RSS e retorna lista de items parseados.
    Inclui delay pequeno e User-Agent realista pra evitar rate limit."""
    query = urllib.parse.quote(f"{termo} when:{dias}d")
    url = f"https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_content = response.read()
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"  ⚠ Rate limit em '{termo}', aguardando 5s...", file=sys.stderr)
            time.sleep(5)
        else:
            print(f"  ⚠ HTTP {e.code} buscando '{termo}'", file=sys.stderr)
        return []
    except Exception as e:
        print(f"  ⚠ Erro buscando '{termo}': {e}", file=sys.stderr)
        return []
    finally:
        time.sleep(0.4)  # Pequeno delay pra ser educado com a API

    return parsear_rss(xml_content, termo)


def buscar_rss_direto(fonte_nome: str, url: str) -> list:
    """Busca um feed RSS direto e retorna items parseados com a fonte forçada."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            xml_content = response.read()
    except Exception as e:
        print(f"  ⚠ Erro buscando RSS '{fonte_nome}': {e}", file=sys.stderr)
        return []

    items = parsear_rss(xml_content, f"[RSS direto] {fonte_nome}")
    # Força a fonte (esses feeds não retornam <source>)
    for item in items:
        if not item["source"]:
            item["source"] = fonte_nome
    return items


def filtrar_por_keyword_no_titulo(items: list, keywords_lower: set) -> list:
    """Retorna só items cujo título contém alguma keyword (case-insensitive)."""
    result = []
    for item in items:
        title_lower = item["title"].lower()
        if any(k in title_lower for k in keywords_lower):
            result.append(item)
    return result


def parsear_rss(xml_content: bytes, termo_busca: str) -> list:
    """Parse RSS do Google News, retorna lista de dicts com title, link, source, pub_date."""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        print(f"  ⚠ Erro parseando XML para '{termo_busca}': {e}", file=sys.stderr)
        return []

    items = []
    for item in root.iter("item"):
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")
        pubdate_el = item.find("pubDate")

        if title_el is None or link_el is None:
            continue

        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        # Google News põe a fonte no final do título como "Title - Source"
        # Mas também tem o elemento <source>, que é mais confiável
        source = (source_el.text or "").strip() if source_el is not None else ""
        if not source and " - " in title:
            # Fallback: extrair fonte do final do título
            parts = title.rsplit(" - ", 1)
            if len(parts) == 2:
                title, source = parts[0], parts[1]

        # Parse data de publicação (formato RFC 822)
        pub_date_iso = ""
        if pubdate_el is not None and pubdate_el.text:
            try:
                dt = parsedate_to_datetime(pubdate_el.text)
                pub_date_iso = dt.isoformat()
            except Exception:
                pass

        items.append({
            "title": title,
            "link": link,
            "source": source,
            "pub_date": pub_date_iso,
            "search_term": termo_busca,
        })
    return items


# =============================================================
# DEDUPLICAÇÃO
# =============================================================
def normalizar_titulo(t: str) -> str:
    """Normaliza título para comparação — minúsculas, sem pontuação, sem espaços extras."""
    t = t.lower()
    t = re.sub(r"[^\w\s]", "", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def deduplicar(items: list) -> list:
    """Remove duplicatas baseado em link e em título normalizado."""
    seen_links = set()
    seen_titles = set()
    result = []
    for item in items:
        link = item["link"]
        title_norm = normalizar_titulo(item["title"])
        if link in seen_links or title_norm in seen_titles:
            continue
        seen_links.add(link)
        seen_titles.add(title_norm)
        result.append(item)
    return result


# =============================================================
# CLASSIFICAÇÃO (heurística simples baseada em keywords)
# =============================================================
HOMEBUILDER_HINTS = [
    # Empresas
    "cyrela", "eztec", "ez tec", "even", "helbor", "mitre", "moura dubeux",
    "melnick", "lavvi", "lps brasil", "lopes", "trisul", "cury", "direcional",
    "mrv", "plano&plano", "plano e plano", "tenda", "gafisa", "tecnisa", "tegra",
    # Tickers
    "cyre3", "eztc3", "even3", "hbor3", "mtre3", "mdne3", "melk3", "lavv3",
    "lpsb3", "tris3", "cury3", "dirr3", "mrve3", "plpl3", "tend3",
    # Temas
    "incorporadora", "construtora", "minha casa minha vida", "mcmv",
    "fgts habitação", "incc", "lançamento imobiliário", "lançamentos imobiliários",
    "vso", "casa verde amarela", "caixa habitacional",
    "crédito imobiliário", "sfh", "lci", "lig", "sbpe",
]
MALLS_HINTS = [
    # Empresas
    "iguatemi", "allos", "multiplan", "jhsf", "log commercial",
    "br properties", "são carlos empreendimentos", "glp logística",
    "bresco", "brc logística", "prologis", "cyrela commercial",
    "jfl living", "vitacon", "general shopping", "hines", "hsi gestora",
    # Tickers
    "igti11", "alos3", "mult3", "jhsf3", "logg3", "brpr3", "scar3",
    "brco11", "ccpr3", "gshp3",
    # Temas
    "shopping", "galpão", "logístico", "logística", "lajes corporativas",
    "escritório corporativo", "fundo imobiliário shopping",
    "vacância escritório", "vacância shopping", "abl", "área bruta locável",
    "condomínio logístico", "cap rate", "noi",
]

def classificar(item: dict) -> str:
    """Retorna HOMEBUILDERS / MALLS / DESCARTAR baseado em hints no título + search_term."""
    blob = (item["title"] + " " + item["search_term"]).lower()
    hb = sum(1 for h in HOMEBUILDER_HINTS if h in blob)
    mp = sum(1 for h in MALLS_HINTS if h in blob)

    if hb == 0 and mp == 0:
        return "DESCARTAR"  # nada bateu — provavelmente ruído
    return "HOMEBUILDERS" if hb >= mp else "MALLS"


def fonte_aprovada(item: dict) -> bool:
    """Retorna True se a fonte está aprovada (exata OU substring única)."""
    source_norm = item["source"].lower().strip()
    if source_norm in FONTES_EXATAS:
        return True
    return any(f in source_norm for f in FONTES_SUBSTRING)


# Fontes prioritárias (top tier) — ficam no topo da lista final do JSON.
# Ordem importa: mais prioritária primeiro.
FONTES_PRIORITARIAS = [
    "valor econômico", "valor",
    "folha de s.paulo", "folha de são paulo",
    "estadão", "estadao",
    "o globo",
    "metroquadrado", "metro quadrado",
    "brazil journal",
    "exame",
    "siila",
]


def prioridade_fonte(item: dict) -> int:
    """Retorna a posição (0 = mais prioritária). Não-prioritárias retornam 999."""
    source_norm = item["source"].lower().strip()
    for i, fonte in enumerate(FONTES_PRIORITARIAS):
        if fonte in source_norm:
            return i
    return 999


# =============================================================
# RESOLVER URLs (Google News -> URL canônica do site original)
# =============================================================
def resolver_urls_canonicas(noticias: list):
    """Substitui links Google News pela URL real do site da fonte.
    Modifica items in-place. Guarda o link original em 'link_google_news'.
    """
    if not noticias:
        return
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        print("  ⚠ Lib 'googlenewsdecoder' não instalada. Mantendo links Google News.")
        print("    Pra ativar: pip3 install googlenewsdecoder")
        return

    sucessos = 0
    falhas = 0
    for n in noticias:
        link = n.get("link") or ""
        if "news.google.com" not in link:
            continue
        try:
            result = gnewsdecoder(link, interval=1)
            if result.get("status") and result.get("decoded_url"):
                n["link_google_news"] = link  # guarda original pra debug
                n["link"] = result["decoded_url"]
                sucessos += 1
            else:
                falhas += 1
        except Exception:
            falhas += 1
    print(f"  ✓ {sucessos} URLs resolvidas, {falhas} falharam (mantidas no link Google News)")


def _chamar_anthropic(prompt: str, api_key: str, max_tokens: int = 4000):
    """Helper: chama API da Anthropic e retorna o texto da resposta. Lança em erro."""
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        data = json.loads(response.read())
    return data["content"][0]["text"]


def deduplicar_semanticamente(noticias: list, api_key: str) -> list:
    """Agrupa notícias semanticamente similares (mesmo evento, fontes diferentes)
    e mantém apenas a de fonte de maior prioridade em cada cluster."""
    if len(noticias) <= 1:
        return noticias

    items_texto = "\n".join(
        f"{i+1}. [{n['source']}] {n['title']}"
        for i, n in enumerate(noticias)
    )

    prompt = f"""Você é um curador de clipping. Algumas das notícias abaixo cobrem o MESMO EVENTO ou MOVIMENTO de mercado em veículos diferentes. Identifique esses clusters.

REGRA: notícias contam como o "mesmo evento" se descrevem o mesmo fato (mesma empresa + mesma ação + mesma data aproximada).

Exemplos do mesmo evento:
- "Multiplan inaugura BH Shopping com R$ 30 mi" (ADVFN)
- "Multiplan amplia BH Shopping e reforça geração de valor" (Guia do Investidor)
- "Multiplan inaugura 6ª expansão do BH Shopping" (financenews)
→ Tudo isso é 1 evento. Mantenha SÓ 1.

NÃO são o mesmo evento:
- "Multiplan inaugura BH Shopping" vs "Multiplan aprova R$ 120 mi JCP" → 2 eventos distintos
- "Pátria consolida FIIs" vs "Pátria compra prédio em SP" → 2 eventos

Para cada cluster, retorne o id da notícia QUE DEVE PERMANECER. Critério de escolha:
1ª prioridade: Valor, Folha, Estadão, O Globo, MetroQuadrado, Brazil Journal, Exame, SiiLA
2ª prioridade: InfoMoney, Money Times, NeoFeed, Pipeline
3ª prioridade: outras

Retorne SOMENTE um JSON neste formato (sem texto antes/depois, sem markdown):
{{"manter": [1, 3, 5, ...]}}
onde os números são os ids das notícias a MANTER (uma por cluster, mais todas as únicas).

NOTÍCIAS:
{items_texto}"""

    try:
        raw = _chamar_anthropic(prompt, api_key, max_tokens=1000)
    except Exception as e:
        print(f"  ⚠ Erro na dedup semântica: {e}", file=sys.stderr)
        return noticias

    # Parse
    try:
        import re as _re
        match = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if not match:
            print(f"  ⚠ Resposta da IA sem JSON na dedup semântica", file=sys.stderr)
            return noticias
        result = json.loads(match.group())
        manter_ids = set(int(x) for x in result.get("manter", []))
    except Exception as e:
        print(f"  ⚠ Falha parseando dedup semântica: {e}", file=sys.stderr)
        return noticias

    filtrada = [n for i, n in enumerate(noticias) if (i + 1) in manter_ids]
    return filtrada


# =============================================================
# CARREGAR DECISÕES HISTÓRICAS (pra few-shot na IA)
# =============================================================
DECISOES_HISTORICAS_PATH = "data/decisoes_historicas.json"

def carregar_decisoes_historicas(n_exemplos: int = 25) -> list:
    """Lê as últimas N decisões da Juliana pra usar como few-shot no prompt.
    Retorna lista de dicts {title, source, decisao} ordenada por data desc.
    Retorna lista vazia se arquivo não existe ainda.
    """
    if not os.path.exists(DECISOES_HISTORICAS_PATH):
        return []
    try:
        with open(DECISOES_HISTORICAS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Esperamos formato: {"decisoes": [{title, source, decisao, data}, ...]}
        todas = data.get("decisoes", [])
        # Ordena por data desc e pega últimas N
        todas.sort(key=lambda x: x.get("data", ""), reverse=True)
        return todas[:n_exemplos]
    except Exception as e:
        print(f"  ⚠ Erro lendo decisões históricas: {e}", file=sys.stderr)
        return []


def formatar_exemplos_few_shot(exemplos: list) -> str:
    """Formata decisões anteriores como bloco de exemplos pro prompt."""
    if not exemplos:
        return ""

    keeps = [e for e in exemplos if e.get("decisao") == "keep"]
    drops = [e for e in exemplos if e.get("decisao") == "drop"]

    bloco = "\n═══════════════════════════════════════════════════════════\n"
    bloco += f"EXEMPLOS REAIS DA SUA CURADORIA RECENTE (use como referência):\n"
    bloco += "═══════════════════════════════════════════════════════════\n\n"

    if keeps:
        bloco += f"VOCÊ MANTEVE estas notícias em curadoria recente:\n"
        for e in keeps[:15]:
            bloco += f"  ✓ [{e.get('source','?')}] {e.get('title','')[:120]}\n"
        bloco += "\n"

    if drops:
        bloco += f"VOCÊ DESCARTOU estas notícias em curadoria recente:\n"
        for e in drops[:15]:
            bloco += f"  ✗ [{e.get('source','?')}] {e.get('title','')[:120]}\n"
        bloco += "\n"

    return bloco


# =============================================================
# CAMADA DE IA (Claude Haiku) — modo "ranqueador" (não-filtro)
# Atribui sugestão LIKELY_KEEP / UNCERTAIN / LIKELY_DROP pra cada notícia
# Custo: ~$0.001 por notícia analisada (~$0.50-1/mês em uso real)
# =============================================================
def classificar_com_ia(noticias: list, api_key: str) -> list:
    """Usa Claude Haiku para sugerir relevância (NÃO descarta nada).
    Adiciona campo 'sugestao_ia' = LIKELY_KEEP / UNCERTAIN / LIKELY_DROP
    e atribui 'category' = HOMEBUILDERS ou MALLS quando aplicável.
    """
    if not noticias:
        return noticias

    # Carrega decisões históricas pra few-shot
    exemplos_historicos = carregar_decisoes_historicas(25)
    bloco_few_shot = formatar_exemplos_few_shot(exemplos_historicos)
    if exemplos_historicos:
        print(f"  📚 Few-shot ativo: {len(exemplos_historicos)} decisões históricas suas no prompt")

    items_texto = "\n".join(
        f"{i+1}. [{n['source']}] {n['title']}  [pub: {(n.get('pub_date') or '?')[:10]}]"
        for i, n in enumerate(noticias)
    )

    hoje_str = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""Você é analista de research do setor imobiliário do Itaú BBA. Está sugerindo a relevância de cada notícia para um clipping institucional. Hoje é {hoje_str}.

IMPORTANTE: você NÃO descarta nada. Você apenas SUGERE uma classificação. A analista revisa depois.

Para cada notícia, atribua:
- sugestao: "LIKELY_KEEP" (provavelmente vai entrar) OU "UNCERTAIN" (em dúvida) OU "LIKELY_DROP" (provavelmente não entra)
- categoria: "HOMEBUILDERS" ou "MALLS" (mesmo nas LIKELY_DROP, atribua a melhor categoria)

Use UNCERTAIN quando tiver dúvida real (ex: incorporadora regional citada, FII relevante mas pequeno, transação sem valor explícito). Não use UNCERTAIN como "fuga" — só quando legítimo.

═══════════════════════════════════════════════════════════
REGRA DE PRIORIDADE DE FONTE (a mais importante):
═══════════════════════════════════════════════════════════

TOP 8 FONTES PREMIUM (sempre cobertura institucional):
  Valor, Folha de S.Paulo, Estadão, O Globo, MetroQuadrado, Brazil Journal, Exame, SiiLA

Regras:
- Notícia de fonte PREMIUM + tema relevante + recente → LIKELY_KEEP
- Notícia de fonte PREMIUM mas velha (referindo trimestre antigo, ano antigo, ex: "1T25" quando estamos em junho/2026) → LIKELY_DROP
- Notícia de fonte FORA das top 8 → LIKELY_DROP por padrão
  EXCEÇÕES (aprovar mesmo fora das top 8):
    a) Notícia EXCLUSIVA/ESPECÍFICA não coberta nas premium (ex: fato relevante de Moura Dubeux saiu primeiro em jornal nordestino; movimento corporativo de empresa pequena em portal especializado tipo ADVFN)
    b) Anúncio corporativo de empresa-âncora (Multiplan, Iguatemi, JHSF, MRV, Cyrela, etc) mesmo em fonte não-premium
    c) Movimento de acionista relevante (>5% participação, mudança de controle) mesmo em fonte não-premium

ATENÇÃO À DATA DE PUBLICAÇÃO (campo [pub: YYYY-MM-DD]):
- Se a data for de mais de 7 dias atrás → suspeite que pode ser notícia velha → marque LIKELY_DROP a menos que seja um movimento estrutural sem prazo
- Se o título mencionar "1T25", "2T25", "4T24", "novembro de 2024" e hoje estamos em 2026 → LIKELY_DROP (notícia antiga)
- Se mencionar "1T26", "1Q26", "abril/2026", "maio/2026" → recente, OK

═══════════════════════════════════════════════════════════
{bloco_few_shot}

═══════════════════════════════════════════════════════════
APROVAR (relevante=true) — 6 tipos identificados na curadoria histórica:
═══════════════════════════════════════════════════════════

TIPO 1 — Empresa listada do setor + ação corporativa
Sempre aprovar quando o título cita uma das empresas E menciona dividendos, JCP, recompra, debêntures, emissão, follow-on, CVM, fato relevante, M&A, mudança no controle.
Empresas-alvo:
  HOMEBUILDERS: Cyrela (CYRE3), Eztec/EZ Tec (EZTC3), Even (EVEN3), Helbor (HBOR3), Mitre (MTRE3), Moura Dubeux (MDNE3), Melnick (MELK3), Lavvi (LAVV3), Trisul (TRIS3), Cury (CURY3), Direcional (DIRR3), MRV (MRVE3), Plano&Plano (PLPL3), Tenda (TEND3), Tecnisa (TCSA3), Gafisa
  MALLS: Iguatemi (IGTI11), Allos (ALOS3), Multiplan (MULT3), JHSF (JHSF3), LOG CP (LOGG3), BR Properties (BRPR3), São Carlos (SCAR3), General Shopping (GSHP3)
Exemplos reais aprovados: "Tenda (TEND3) fará resgate antecipado de debêntures"; "Direcional distribui R$ 104,2 mi em dividendos"; "Multiplan aprova R$ 120 mi em JCP"; "JHSF renova programa de recompra"; "Família Zarzur (Eztec) no alvo da CVM"; "Even vende 9,8 milhões de ações"; "MRV acelera produção e repasses em maio"; "Tecnisa conclui venda fatia no Jardim das Perdizes para BTG".

TIPO 2 — Resultados / Prévia operacional
Vendas, lançamentos, repasses, VSO, produção mensal/trimestral, balanço, guidance, prejuízo/lucro.
Exemplos: "MRV eleva produção para 3.665 unidades em maio"; "Direcional anuncia volume de lançamentos do 1T"; "EzTec tem queda de 40% nos lançamentos vs 4T".

TIPO 3 — Transação imobiliária relevante (R$ ≥ 50 mi OU player institucional)
M&A, venda de torre, aquisição de portfólio, JV entre incorporadoras, **criação ou estruturação de novo FII institucional (gestoras grandes: Kinea, Brookfield, Pátria, BTG, XP, Bradesco Asset, Itaú Asset, JP Morgan AM)**, compra/venda de shopping ou galpão.
**ATENÇÃO ESPECIAL — JV entre gestoras grandes para lançar FII = SEMPRE APROVAR (mesmo se o título começar com "Fundo imobiliário..." ou "FII...")**
Exemplos APROVADOS:
- "TRXF11 compra 8 galpões por R$ 135 mi"
- "Kinea se une à Brookfield para lançar fundo imobiliário residencial de quase R$ 2 bilhões"  ← APROVAR (JV de gestoras grandes lançando FII institucional)
- "Kinea e Brookfield criam FII de R$ 2 bi no multifamily"  ← APROVAR
- "Kinea busca R$ 1,9 bilhão para investir em residenciais com a Brookfield"  ← APROVAR
- "Setin e Trisul prédio R$ 600 mi no Paraíso"
- "FII XPML11 compra participações da JHSF em shoppings"
- "CPPIB vira sócio HSI nos Hilton de Copacabana e Morumbi"
- "Galpão em Diadema vendido por R$ 93 mi a FII"
- "Tecnisa conclui venda fatia no Jardim das Perdizes para BTG"
- "Pátria consolida fundos imobiliários" (movimento estratégico de gestora grande)

TIPO 4 — Macro/regulatório imobiliário (com NÚMERO ou dado de mercado)
INCC, FipeZAP, crédito imobiliário (volume R$), mudanças regulatórias (CMN, Caixa, FGTS), poupança SBPE, leis MCMV.
Exemplos: "INCC-M desacelera a 0,77%"; "Inflação da construção cai para 1,16%"; "Crédito imobiliário ultrapassa R$ 16 bi"; "Caixa muda regras do financiamento"; "FGTS: novo modelo amplia uso do saldo"; "Financiamento por poupança cresce 35%"; "MCMV: faixa máxima de renda ampliada".

TIPO 5 — MCMV regional COM dado de mercado
Notícias sobre MCMV em qualquer cidade entram SE trouxerem dado quantitativo ("R$ X bi", "X% das vendas", "Y unidades contratadas").
Exemplos aprovados: "MCMV responde por 70% das vendas em SP"; "Paraná soma 142 mil residências contratadas"; "Mercado de Fortaleza R$ 2,7 bi recorde"; "MCMV: mais de 50% das vendas em Fortaleza 1T".
REJEITAR quando for só burocrático: "Inscrições MCMV em Manaus encerram em junho"; "Equipes fazem visitas em Uberlândia" (sem dado de mercado).

TIPO 6 — Movimento de mercado relevante (premium, multifamily, escritórios, luxo, FII institucional)
Tendências macro, mudanças estruturais, expansões grandes, novos players estrangeiros.
Exemplos: "JHSF inaugura CJ Boa Vista Village"; "Multiplan amplia BH Shopping"; "ASA (Alberto Safra) avança pelo interior de SP"; "Estúdios no Rio conquistam investidores estrangeiros"; "Família Zarzur EzTec CVM"; "Boom de lançamentos em Santana zona norte SP"; "Porto Maravilha imóveis acima de R$ 800 mil".

═══════════════════════════════════════════════════════════
DESCARTAR (relevante=false) — 8 padrões identificados:
═══════════════════════════════════════════════════════════

DESCARTE 1 — Listas genéricas de ações ou day trade
"10 ações para comprar em X", "Dividendos em agosto", "Day trade: compre X venda Y", "Agenda de empresas hoje", "Ibovespa hoje", "Top ações para acompanhar" — DESCARTAR MESMO se citarem empresas do setor de passagem.
Ex rejeitados: "Raízen, Axia, Brava, Yduqs, Multiplan e mais ações para acompanhar"; "Day trade: B3SA3 e ALLOS3"; "Nubank, Minerva, MRV e outros destaques"; "Dividendos: Petrobras, Bradesco e mais 24 empresas".

DESCARTE 2 — Análise/research de competidores sell-side
Cobertura de Citi, UBS, BTG, XP, Bradesco BBI, Genial, Goldman, JPMorgan, Santander, Ágora sobre empresas do setor — DESCARTAR por padrão. Só aprovar se for movimento institucional grande (upgrade/downgrade severo de empresa-âncora com impacto sistêmico).
Ex rejeitados: "Iguatemi pode valorizar 52%, diz Citi"; "UBS-BB corta MRV após piora nos juros"; "Genial inicia Iguatemi com compra"; "Goldman eleva Usiminas".
Exceção (aprovar): "Itaú BBA destaca prévia MRV"; análises explicitamente da equipe Itaú BBA.

DESCARTE 3 — Empresas estrangeiras (mesmo do setor RE)
Taylor Morrison, AvalonBay, Equity Residential, Floor Decor, Carrier, Urban Edge, Gaming and Leisure Properties, China Properties, British American Tobacco, AkzoNobel, Sherwin-Williams, etc. DESCARTAR.
Ex rejeitados: "Berkshire compra Taylor Morrison por US$ 6,8 bi"; "AvalonBay e Equity Residential anunciam fusão"; "Incorporadora China Properties deixa de pagar US$ 226 mi".

DESCARTE 4 — FIIs individuais (XXXX11) — performance/proventos
"FII X paga R$ Y por cota", "DY de Z%", "FII recompra cotas", "lista FIIs com pior desempenho" — DESCARTAR.
Exceção (aprovar): M&A entre FIIs, compra/venda de ativo grande, criação de novo FII institucional, mudança regulatória do segmento.
Ex rejeitados: "KNRI11 mantém R$ 1,10 por cota"; "Os 10 FIIs com pior desempenho"; "Qual nível de dívida confortável para FII".
Ex aprovados: "TRXF11 compra 8 galpões R$ 135 mi"; "Pátria consolida fundos imobiliários".

DESCARTE 5 — Macro/política/setores não imobiliários
Política partidária, Pix/PEC do BC, energia (gasolina, petróleo, Petrobras), Saab/caça, tecnologia (chip quântico, IA, criptomoedas), banco digital, cigarro, agronegócio, mineração, telecom, varejo (a não ser que dentro de shopping com tema institucional), futebol, celebridades, etc.

DESCARTE 6 — Conteúdo educativo pra pessoa física
"4 dicas para X", "Como declarar imóvel financiado no IR", "Guia: como financiar", "Saiba como inscrever-se" — DESCARTAR.
Exceção: aprovar se trouxer dado novo de mercado (ex: "Como vai funcionar o novo teto de financiamento do MCMV").

DESCARTE 7 — Incorporadoras regionais não-listadas
LaVentana, AF Incorporações, Dreamis, Casa Concreto e similares (incorporadoras pequenas, não-listadas, nicho local) — DESCARTAR.

DESCARTE 8 — Conteúdo genérico/clickbait
"Tudo sobre X", "Notícias Ao Vivo", "Onde morar em [estado]", "Confira", "Notícia ao vivo" — DESCARTAR.

═══════════════════════════════════════════════════════════

Retorne SOMENTE um array JSON puro, sem texto antes/depois, sem markdown:
[{{"id": 1, "sugestao": "LIKELY_KEEP", "categoria": "HOMEBUILDERS"}}, {{"id": 2, "sugestao": "LIKELY_DROP", "categoria": "MALLS"}}, {{"id": 3, "sugestao": "UNCERTAIN", "categoria": "HOMEBUILDERS"}}, ...]

NOTÍCIAS:
{items_texto}"""

    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read())
    except Exception as e:
        print(f"  ⚠ Erro na API Anthropic: {e}", file=sys.stderr)
        return noticias

    raw_text = data["content"][0]["text"].strip()

    # Tenta parsear o JSON (com fallback pra extrair de markdown se vier embrulhado)
    try:
        decisions = json.loads(raw_text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if not match:
            print(f"  ⚠ Resposta da IA não tinha JSON parseável: {raw_text[:200]}", file=sys.stderr)
            return noticias
        try:
            decisions = json.loads(match.group())
        except json.JSONDecodeError as e:
            print(f"  ⚠ JSON inválido da IA: {e}", file=sys.stderr)
            return noticias

    # Aplica decisões — modo RANQUEADOR: não descarta nada, só marca sugestão
    for d in decisions:
        idx = d.get("id", 0) - 1
        if 0 <= idx < len(noticias):
            sug = d.get("sugestao", "UNCERTAIN")
            if sug not in ("LIKELY_KEEP", "UNCERTAIN", "LIKELY_DROP"):
                sug = "UNCERTAIN"
            noticias[idx]["sugestao_ia"] = sug
            cat = d.get("categoria")
            if cat in ("HOMEBUILDERS", "MALLS"):
                noticias[idx]["category"] = cat

    # Garante que todas têm o campo (caso a IA tenha pulado alguma)
    for n in noticias:
        n.setdefault("sugestao_ia", "UNCERTAIN")

    return noticias


# =============================================================
# MAIN
# =============================================================
def dentro_da_janela(item: dict, dias: int) -> bool:
    """Filtra items dentro da janela de tempo (em dias). Sem pub_date, deixa passar."""
    from datetime import timezone
    if not item.get("pub_date"):
        return True
    try:
        dt = datetime.fromisoformat(item["pub_date"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        limite = datetime.now(timezone.utc) - timedelta(days=dias)
        return dt >= limite
    except Exception:
        return True


def main():
    dias = janela_dias()
    hoje = datetime.now().strftime("%Y-%m-%d")
    is_segunda = datetime.today().weekday() == 0

    print(f"\n📰 Coletor de Notícias — Real Estate")
    print(f"📅 Data: {hoje}  |  Janela: últimas {dias * 24}h" + (" (segunda-feira)" if is_segunda else ""))
    print(f"🔍 Buscando {sum(len(v) for v in KEYWORDS.values())} palavras-chave + {len(RSS_FEEDS_DIRETOS)} RSS diretos\n")

    todas_noticias = []
    stats_por_keyword = {}

    # === FONTE 1: Google News por keyword ===
    for categoria, termos in KEYWORDS.items():
        print(f"━━━ {categoria} ({len(termos)} termos) — Google News ━━━")
        for termo in termos:
            items = buscar_google_news(termo, dias)
            stats_por_keyword[termo] = len(items)
            print(f"  • {termo!r:<35} → {len(items)} resultado(s)")
            todas_noticias.extend(items)

    print(f"\n📊 Total Google News: {len(todas_noticias)}")

    # === FONTE 2: RSS direto ===
    print(f"\n━━━ RSS DIRETO ({len(RSS_FEEDS_DIRETOS)} fontes) ━━━")
    keywords_match = _keywords_para_match_titulo()
    total_rss = 0
    for fonte_nome, url in RSS_FEEDS_DIRETOS:
        raw = buscar_rss_direto(fonte_nome, url)
        # Filtra por janela de tempo
        em_janela = [i for i in raw if dentro_da_janela(i, dias)]
        # Filtra por keyword no título
        relevantes = filtrar_por_keyword_no_titulo(em_janela, keywords_match)
        print(f"  • {fonte_nome:<25} {len(raw)} items / {len(em_janela)} em janela / {len(relevantes)} com keyword")
        todas_noticias.extend(relevantes)
        total_rss += len(relevantes)
    print(f"\n📊 Total RSS direto: {total_rss}")

    print(f"\n📊 Total bruto:    {len(todas_noticias)} resultados")
    noticias_unicas = deduplicar(todas_noticias)
    print(f"🧹 Após dedup:     {len(noticias_unicas)} únicas")

    # FILTRO por whitelist de fontes — descarta tudo que não está na lista
    noticias_filtradas = [n for n in noticias_unicas if fonte_aprovada(n)]
    descartadas = len(noticias_unicas) - len(noticias_filtradas)
    print(f"🔒 Após whitelist: {len(noticias_filtradas)} aprovadas ({descartadas} descartadas por fonte)")

    # Classificar (heurística por keywords)
    for n in noticias_filtradas:
        n["category"] = classificar(n)

    # Separa o que sobrou após classificação
    descartadas_classif = [n for n in noticias_filtradas if n["category"] == "DESCARTAR"]
    aprovadas_final = [n for n in noticias_filtradas if n["category"] != "DESCARTAR"]

    # === CAMADA DE IA — MODO RANQUEADOR (não filtra, só sugere) ===
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key and aprovadas_final:
        print(f"\n🤖 IA sugerindo relevância em {len(aprovadas_final)} notícias...")
        aprovadas_final = classificar_com_ia(aprovadas_final, api_key)
        # Conta as sugestões para feedback
        from collections import Counter
        cnt = Counter(n.get("sugestao_ia", "UNCERTAIN") for n in aprovadas_final)
        print(f"   LIKELY_KEEP: {cnt.get('LIKELY_KEEP', 0)}  UNCERTAIN: {cnt.get('UNCERTAIN', 0)}  LIKELY_DROP: {cnt.get('LIKELY_DROP', 0)}")
    elif not api_key:
        print(f"\n💡 IA desativada (defina ANTHROPIC_API_KEY para ativar sugestões)")
        for n in aprovadas_final:
            n.setdefault("sugestao_ia", "UNCERTAIN")
    descartadas_ia = []  # legado, mantido vazio

    # === DEDUP SEMÂNTICA (opcional) ===
    # Agrupa notícias do mesmo evento em fontes diferentes e mantém só a de fonte premium.
    if api_key and len(aprovadas_final) > 1:
        print(f"\n🔍 Dedup semântica em {len(aprovadas_final)} notícias...")
        antes_dedup = len(aprovadas_final)
        aprovadas_final = deduplicar_semanticamente(aprovadas_final, api_key)
        print(f"🔍 Dedup removeu {antes_dedup - len(aprovadas_final)} duplicatas (mantida a fonte premium em cada cluster)")

    # === RESOLVER URLs (Google News -> site original) ===
    if aprovadas_final:
        print(f"\n🔗 Resolvendo URLs canônicas (Google News -> site original)...")
        resolver_urls_canonicas(aprovadas_final)

    # Ordena por prioridade de fonte (top 8 primeiro, depois outras dentro de cada categoria)
    # Ordenação: sugestão IA (KEEP > UNCERTAIN > DROP) → categoria → prioridade da fonte
    SUG_ORDER = {"LIKELY_KEEP": 0, "UNCERTAIN": 1, "LIKELY_DROP": 2}
    aprovadas_final.sort(key=lambda n: (
        SUG_ORDER.get(n.get("sugestao_ia", "UNCERTAIN"), 1),
        n["category"],
        prioridade_fonte(n),
    ))

    # Marca cada notícia como prioritária ou não (útil pro dashboard)
    for n in aprovadas_final:
        n["fonte_prioritaria"] = prioridade_fonte(n) < 999

    # Estatísticas
    por_categoria = {}
    por_fonte = {}
    for n in aprovadas_final:
        por_categoria[n["category"]] = por_categoria.get(n["category"], 0) + 1
        por_fonte[n["source"]] = por_fonte.get(n["source"], 0) + 1

    print(f"🎯 Após classificação: {len(aprovadas_final)} relevantes ({len(descartadas_classif)} descartadas por não bater HB ou MALLS)")

    print(f"\n📂 Por categoria:")
    for cat in ["HOMEBUILDERS", "MALLS"]:
        print(f"   {cat:<15} {por_categoria.get(cat, 0)}")

    print(f"\n📰 Top 10 fontes que entraram:")
    for fonte, count in sorted(por_fonte.items(), key=lambda x: -x[1])[:10]:
        print(f"   {count:3d}  {fonte}")

    # Salvar JSON
    os.makedirs("data", exist_ok=True)
    output_path = f"data/clipping-{hoje}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.now().isoformat(),
            "window_hours": dias * 24,
            "total_raw": len(todas_noticias),
            "total_unique": len(noticias_unicas),
            "total_aprovadas_fonte": len(noticias_filtradas),
            "total_aprovadas_final": len(aprovadas_final),
            "by_category": por_categoria,
            "by_source": por_fonte,
            "stats_per_keyword": stats_por_keyword,
            "ia_ativa": bool(api_key),
            "news": aprovadas_final,
            "descartadas_por_classif": descartadas_classif,  # pra debug
            "descartadas_por_ia": descartadas_ia,  # pra debug
        }, f, ensure_ascii=False, indent=2)

    print(f"\n✅ JSON salvo em: {output_path}")
    print(f"   ({os.path.getsize(output_path) / 1024:.1f} KB)")

    # Gera dashboard HTML com dados embutidos.
    # Salvamos como index.html (padrão do GitHub Pages) E dashboard.html (compat local).
    try:
        from dashboard_template import gerar_dashboard_html
        gerar_dashboard_html(aprovadas_final, hoje, "index.html")
        gerar_dashboard_html(aprovadas_final, hoje, "dashboard.html")
        print(f"✅ Dashboard salvo em: index.html (e dashboard.html)")
        print(f"\n👉 Local: open index.html  |  GitHub Pages: usa index.html automaticamente")
    except ImportError:
        print(f"⚠ dashboard_template.py não encontrado — pulando geração do HTML")
    except Exception as e:
        print(f"⚠ Erro gerando dashboard: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
