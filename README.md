# PipeLovers — Painel de Engajamento & Saúde da Base

Dashboard estático (GitHub Pages) que cruza contas B2B, contratos, usuários,
consumo de aulas (Waid), avaliações, downloads offline e sessões ao vivo (Zoom)
num único `index.html`.

## Como funciona

```
data/                     ← você atualiza esses arquivos
  contasb2b.csv
  contratos.csv
  usuarios.csv
  classes_progress.xlsx
  reviews_contents.xlsx
  downloads.xlsx
  biblioteca_pai.xlsx     ← muda pouco, só re-exporte se o catálogo de aulas mudar
  consumos_zoom.xlsx      ← muda pouco, só re-exporte se tiver sessão ao vivo nova

gerar_dashboard.py        ← lê tudo em /data e escreve index.html
template.html             ← layout/CSS/JS do painel (o script injeta os dados aqui dentro)
index.html                ← gerado automaticamente, é o que o GitHub Pages publica
.github/workflows/atualizar.yml  ← roda o script sozinho a cada push em /data e 1x por dia
```

## Atualização diária (o que você precisa fazer)

1. Exporte do Waid / Hubla / base B2B os arquivos mais recentes.
2. Substitua os arquivos dentro de `data/` **mantendo exatamente os mesmos nomes**
   (veja a lista de colunas esperadas nos comentários no topo de `gerar_dashboard.py`).
3. Dê commit e push para o `main`.
4. O GitHub Action (`atualizar.yml`) roda sozinho, regenera o `index.html` e
   faz o commit de volta — em ~1-2 minutos o GitHub Pages já reflete os dados novos.

Se preferir, também roda sozinho todo dia às 09h (Brasília) mesmo sem push,
e você pode disparar manualmente pela aba **Actions → Atualizar Dashboard → Run workflow**.

## Rodando localmente (opcional, pra conferir antes de subir)

```bash
pip install -r requirements.txt
python gerar_dashboard.py
# abre o index.html gerado no navegador
```

## Ativando o GitHub Pages (só na primeira vez)

No repositório: **Settings → Pages → Source: Deploy from a branch → Branch: `main` / `(root)`**.
O link fica em `https://<seu-usuário>.github.io/<nome-do-repo>/`.

## O que está calculado

- Contas ativas/inativas (conta é ativa se tem contrato sem `data_churn`)
- Usuários ativos/inativos (herda o status da conta)
- Consumo por grupo (Pré-Vendas, Executivos, Gestão, Canais e Parcerias, Class,
  Programas Especiais) — Ao Vivo vs. Gravado, visão semanal e mensal
- Usuários únicos assistindo, por grupo/período/tipo
- Nota média das aulas por grupo
- MAU (mês a mês) e tendência
- Volume médio de aulas concluídas por mês
- Usuários dormentes (nunca consumiram / sem consumo há 6m / sem consumo há 3m)
- Downloads offline x consumo registrado
- Empresas com +50% de MAU (contas com 3+ usuários)

## Limitações conhecidas

- O cruzamento "grupo do conteúdo" depende do título da aula ser **idêntico** entre
  o export do Waid e a planilha `biblioteca_pai.xlsx`. Divergência de nomenclatura
  cai em "Sem Grupo Identificado" — o painel mostra o % disso em destaque.
- "Dormência" é calculada pela última conclusão de aula (não existe hoje um evento
  de "login" separado nos exports usados).
