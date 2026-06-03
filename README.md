# IBBA Real Estate - Daily Clipping

Automatização da rotina diária de clipping de notícias do setor imobiliário brasileiro, feita para a equipe de Real Estate do Itaú BBA.

## Como funciona

1. **Coletor** (`coletor.py`) busca notícias no Google News + RSS direto do MetroQuadrado
2. **Filtros**: whitelist de fontes confiáveis + classificação por IA (Claude Haiku) com 3 níveis (LIKELY_KEEP / UNCERTAIN / LIKELY_DROP)
3. **Dedup semântica**: agrupa mesmo evento em fontes diferentes
4. **URLs canônicas**: converte links Google News pra URL real do site
5. **Dashboard** (`index.html`) mostra todas com sugestão da IA — você revisa e gera o clipping formatado pra WhatsApp/email
6. **Aprendizado**: suas decisões viram exemplos few-shot pra IA da próxima rodada

## Execução

### Manual (botão no dashboard)
Acesse o dashboard publicado em GitHub Pages e clique em "Atualizar agora".

### Automática (cron)
Roda todo dia útil às 5:30 (BRT) via GitHub Actions.

### Local (durante desenvolvimento)
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
python3 coletor.py
open dashboard.html
```

## Estrutura

```
.
├── coletor.py                  # Script principal
├── coletor_treino.py           # Modo histórico (7 dias, sem filtros) pra recalibração
├── dashboard_template.py       # Gera index.html / dashboard.html
├── requirements.txt            # Dependências Python
├── .github/workflows/
│   └── atualizar.yml           # Workflow de execução automática
├── index.html                  # Dashboard (gerado pelo coletor; servido pelo Pages)
├── dashboard.html              # Cópia local do dashboard
└── data/
    ├── clipping-YYYY-MM-DD.json     # Saída diária do coletor
    └── decisoes_historicas.json     # Acumulado das decisões (few-shot)
```

## Configurações importantes

- **Secret `ANTHROPIC_API_KEY`** precisa estar configurado em Settings > Secrets and variables > Actions
- Cron está em `.github/workflows/atualizar.yml` (modificar formato se quiser outro horário)

## Custos

- GitHub Pages + Actions: $0 (plano free)
- API Claude Haiku: ~$1-2/mês

---

Projeto pessoal — não distribuído.
