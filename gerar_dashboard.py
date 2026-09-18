"""
PipeLovers — Gerador do Painel de Engajamento & Saúde da Base
================================================================
Cruza contas, contratos, usuarios, consumo, avaliacoes, downloads,
biblioteca de conteudos e sessoes Zoom, e gera um index.html
autocontido (dados embutidos) na raiz do repositorio.

Fonte dos dados (automatico vs manual):
  Contas, contratos, usuarios e consumo de aulas vem DIRETO do
  Supabase via API, desde que as variaveis de ambiente SUPABASE_URL
  e SUPABASE_SERVICE_ROLE_KEY estejam configuradas (no GitHub, isso
  fica em Settings -> Secrets and variables -> Actions). Com isso,
  o GitHub Action que roda todo dia as 09h ja pega o dado mais
  recente sozinho, sem precisar subir planilha nenhuma pra essas 4
  fontes -- o consumo, inclusive, ja chega em tempo real via
  webhook da Waid direto no Supabase.

  Biblioteca de conteudos, sessoes Zoom, avaliacoes (CSAT gravado
  e ao vivo) e downloads AINDA sao manuais -- continuam vindo dos
  arquivos em /data, ate que a gente crie uma fonte automatica pra
  eles tambem.

  Se as variaveis do Supabase nao estiverem configuradas (rodando
  local, por exemplo), o script cai automaticamente pros arquivos
  csv/xlsx locais em /data no lugar de contas/contratos/usuarios/
  consumo -- util pra testar sem precisar de credencial nenhuma.

Como atualizar os dados que ainda sao manuais:
  1. Exporte as planilhas mais recentes (biblioteca, zoom, reviews,
     downloads, csat).
  2. Sobrescreva os arquivos em /data mantendo EXATAMENTE os mesmos
     nomes de arquivo (veja a lista em ARQUIVOS abaixo).
  3. Rode `python gerar_dashboard.py` (ou deixe o GitHub Action
     rodar sozinho todo dia / a cada push em /data).

Arquivos esperados em /data (so usados quando USE_SUPABASE = False,
ou pras 4 fontes que ainda sao manuais mesmo com Supabase ligado):
  contasb2b.csv        -> nome, created_at, id
  contratos.csv         -> id, created_at, tipo_contrato, csm,
                            data_assinatura, data_churn, conta_id
  usuarios.csv           -> id, created_at, nome, email, whatsapp,
                            status, id_conta, cargo, data_criacao,
                            grupo_acesso
  classes_progress.xlsx  -> export "Relatorio" do Waid com o consumo
                            (Nome, Email, CPF, Conteudo, Modulo,
                            Nome da aula, Data de conclusao)
  reviews_contents.xlsx  -> export de avaliacoes (Codigo, Curso,
                            Aluno, CPF, Email, Data da Avaliacao,
                            Avaliacao, Mensagem)
  downloads.xlsx         -> export de downloads offline (Codigo,
                            Aluno, E-mail, Aula, Conteudo, Status,
                            Baixado em, Expira em)
  csat_ao_vivo.csv       -> export do Zoom da pesquisa de satisfacao
                            pos-aula ao vivo (survey report). Vem com
                            secoes extras antes da tabela de respostas;
                            o script acha o cabecalho de verdade sozinho.
  biblioteca_pai.xlsx    -> planilha PAI de biblioteca de conteudos
                            (muda pouco, so re-exporte se o
                            catalogo de aulas mudar)
  consumos_zoom.xlsx     -> export de sessoes ao vivo do Zoom (para
                            identificar quais aulas sao "Ao Vivo")
"""
import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "index.html"

# Referencia de "hoje" e do ultimo mes fechado usadas nos calculos de
# dormencia / tendencia. Ajuste se quiser travar uma data especifica;
# por padrao usamos a data corrente do sistema que roda o script.
# ---------------------------------------------------------------
# CONEXAO COM O SUPABASE (fonte automatica: contas, contratos,
# usuarios e consumo de aulas vem de la quando as credenciais
# estiverem configuradas; senao, cai pros arquivos manuais em /data)
# ---------------------------------------------------------------
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
USE_SUPABASE = bool(SUPABASE_URL and SUPABASE_KEY)


def _supabase_fetch(table, select="*", page_size=1000):
    """Busca todas as linhas de uma tabela/view do Supabase via API REST
    (PostgREST), paginando automaticamente (o Supabase limita a resposta
    por chamada, entao precisamos ir avancando ate acabar)."""
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
    rows = []
    offset = 0
    while True:
        params = {"select": select, "limit": page_size, "offset": offset}
        resp = requests.get(f"{SUPABASE_URL}/rest/v1/{table}", headers=headers, params=params, timeout=60)
        resp.raise_for_status()
        batch = resp.json()
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows)


TODAY = pd.Timestamp.today().normalize()
MES_ATUAL = TODAY.strftime("%Y-%m")
# ultimo mes fechado = mes anterior ao mes corrente
ULTIMO_MES_FECHADO = (TODAY.replace(day=1) - pd.Timedelta(days=1)).strftime("%Y-%m")

GRUPOS = [
    "Pré-Vendas", "Executivos", "Gestão", "Canais e Parcerias",
    "Class", "Programas Especiais", "Sem Grupo Identificado",
]

SHEETS_MAP = {
    "SESSÕES | PRÉ VENDAS": "Pré-Vendas",
    "SESSÕES | GESTÃO": "Gestão",
    "SESSÕES | EXECUTIVOS": "Executivos",
    " SESSÕES | CANAIS": "Canais e Parcerias",
    "SESSÕES | CLASS": "Class",
    "CERTIFICAÇÃO EM IA": "Programas Especiais",
    "PROGRAMA DE GESTÃO": "Gestão",
    "CERTIFICAÇÃO DE VENDEDORES B2B": "Programas Especiais",
    "ESPECIALIZAÇÃO DE VENDEDORES": "Programas Especiais",
}
def clean_json(obj):
    """Troca NaN/Inf por None recursivamente para gerar JSON valido."""
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_json(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj


# ---------------------------------------------------------------
# 1. CONTAS / CONTRATOS
# ---------------------------------------------------------------
def load_contas_contratos():
    if USE_SUPABASE:
        contas = _supabase_fetch("contasb2b")
        contratos = _supabase_fetch("contratos")
        print(f"  (via Supabase: {len(contas)} contas, {len(contratos)} contratos)")
    else:
        contas = pd.read_csv(DATA / "contasb2b.csv")
        contratos = pd.read_csv(DATA / "contratos.csv")
    contratos["data_churn"] = pd.to_datetime(contratos["data_churn"], errors="coerce", utc=True)
    contratos["ativo"] = contratos["data_churn"].isna()

    active_ids = set(contratos.loc[contratos["ativo"], "conta_id"])
    contas["status_conta"] = contas["id"].apply(lambda x: "Ativa" if x in active_ids else "Inativa")

    conta_status_map = contas.set_index("id")["status_conta"].to_dict()
    conta_nome_map = contas.set_index("id")["nome"].to_dict()

    # CSM: prioriza o contrato ativo; se nao houver, pega o CSM do contrato mais recente
    contratos_sorted = contratos.sort_values(["ativo", "created_at"], ascending=[False, False])
    conta_csm_map = contratos_sorted.drop_duplicates(subset="conta_id").set_index("conta_id")["csm"].to_dict()

    resumo = {
        "ativas": int((contas["status_conta"] == "Ativa").sum()),
        "inativas": int((contas["status_conta"] == "Inativa").sum()),
        "total": int(len(contas)),
    }
    return contas, contratos, conta_status_map, conta_nome_map, conta_csm_map, resumo


# ---------------------------------------------------------------
# 2. USUARIOS
# ---------------------------------------------------------------
def load_usuarios(conta_status_map, conta_nome_map, conta_csm_map):
    if USE_SUPABASE:
        usuarios = _supabase_fetch("usuarios")
        print(f"  (via Supabase: {len(usuarios)} usuarios)")
    else:
        usuarios = pd.read_csv(DATA / "usuarios.csv")
    usuarios["email"] = usuarios["email"].astype(str).str.strip().str.lower()
    usuarios["data_criacao"] = pd.to_datetime(usuarios["data_criacao"], errors="coerce")
    usuarios["status_conta"] = usuarios["id_conta"].map(conta_status_map)
    usuarios["nome_conta"] = usuarios["id_conta"].map(conta_nome_map)
    usuarios["csm"] = usuarios["id_conta"].map(conta_csm_map)
    usuarios["usuario_ativo"] = usuarios["status_conta"] == "Ativa"

    resumo = {
        "ativos": int(usuarios["usuario_ativo"].sum()),
        "inativos": int((~usuarios["usuario_ativo"]).sum()),
        "total": int(len(usuarios)),
    }
    return usuarios, resumo


# ---------------------------------------------------------------
# 3. BIBLIOTECA PAI -> mapa titulo -> grupo
# ---------------------------------------------------------------
NORM_GRUPO = {
 "Canais e Parcerias": "Canais e Parcerias",
 "Pré-Vendas (Prospecção)": "Pré-Vendas",
 "Líderes de Vendas": "Gestão",
 "Executivos de Vendas": "Executivos",
 "PipeLovers Class": "Class",
 "Planejamento Comercial": "Gestão",
 "ESPECIALIZAÇÃO ": "Programas Especiais",
 "CERTIFICAÇÃO EM IA": "Programas Especiais",
 "Programa de gestão": "Gestão",
 "Certificação": "Programas Especiais",
 "Class": "Class",
 "Pré-Vendas": "Pré-Vendas",
 "Gestão Comercial": "Gestão",
 "Executivos": "Executivos",
 "SDRs": "Pré-Vendas",
}

# Correções manuais indicadas pelo time de produto: titulos que a Biblioteca PAI
# nao tem, tem errado, ou so nao bate por causa de maiusculas/minusculas.
# Aplicadas por cima do que vier da planilha (tem prioridade).
MANUAL_OVERRIDES = {
    "Programa Especialização em Prospecção": "Pré-Vendas",
    "Módulo 1: Certificação de Vendedores B2B - Prospecção Inteligente e Geração de Demanda": "Pré-Vendas",
    "Programa de Gestão de Vendas B2B": "Gestão",
    "Inicie sua jornada de Pré Vendas por aqui": "Pré-Vendas",
    "Módulo 2: Certificação de Vendedores B2B - Pitch, Storytelling e Oratória Comercial": "Executivos",
    "Inicie sua jornada de Gestão por aqui": "Gestão",
    "Módulo 3: Certificação de Vendedores B2B - Negociação e Fechamento": "Executivos",
    "Inicie sua jornada para Executivos por aqui": "Executivos",
    "Certificação de Vendedores B2B": "Executivos",
    "Módulo 1: Programa de Gestão - Estratégia de Posicionamento e Planejamento Comercial": "Gestão",
    "Módulo 4: Certificação de Vendedores B2B - Gestão de Carteira e Expansão de Receita": "Executivos",
    "Módulo 1: Especialização em Prospecção - Geração de demanda": "Pré-Vendas",
    "ACELERANDO NEGOCIAÇÕES: HACKS PRÁTICOS PARA FECHAMENTO": "Executivos",
    "Módulo 5: Certificação de Vendedores B2B - Habilidades Essenciais e Rotina do Vendedor": "Executivos",
    "Mensagens de prospecção que convertem": "Pré-Vendas",
    "Módulo 3: Especialização em Prospecção - Produtividade, cadência e consistência": "Pré-Vendas",
    "COMO CONSTRUIR PERCEPÇÃO DE VALOR A PARTIR DO CONHECIMENTO DE NEGÓCIO": "Class",
    "Módulo 2: Programa de Gestão - Recrutamento, Remuneração e Desenvolvimento de Equipes": "Gestão",
    "Módulo 2: Especialização em Prospecção - Engenharia da atenção": "Pré-Vendas",
    "Os Segredos dos Melhores Pré-vendedores do Brasil": "Pré-Vendas",
    "Módulo 4: Especialização em Prospecção - Mensagens que geram conversão (WhatsApp, LinkedIn e e-mail)": "Pré-Vendas",
    "Módulo 3 : Programa de Gestão - Canais de Aquisição e Geração de Demanda B2B": "Gestão",
    "Módulo 3: Programa de Gestão - Canais de Aquisição e Geração de Demanda B2B": "Gestão",
    "Certificação em Inteligência Artificial em Vendas B2B": "Programas Especiais",
    "Módulo 6: Especialização em Prospecção - Mapa de Poder & Multi-Threading": "Pré-Vendas",
    "Módulo 4: Programa de Gestão - Expansão de receita na base de clientes retenção, upsell": "Gestão",
    "Abordagens e Prospecção que Funcionam no Começo do Ano": "Pré-Vendas",
    "Módulo 5: Programa de Gestão - Rotinas de gestão comercial rituais, processos e ferramentas": "Gestão",
    "Módulo 5: Especialização em Prospecção - Gestão de objeções": "Pré-Vendas",
    "COMO GERAR MAIS OPORTUNIDADES UTILIZANDO UM FLUXO DE CADÊNCIAS NA PROSPECÇÃO": "Pré-Vendas",
    "COMO MOTIVAR UMA EQUPE DE VENDAS DESMOTIVADA": "Gestão",
    "Como Motivar uma Equipe de Vendas Desmotivada": "Gestão",
    "Tráfego pago com vendas sem bláh bláh bláh": "Class",
    "Fundamentos e preparação da negociação B2B": "Executivos",
    "COMO AUTOCONHECIMENTO E INTELIGÊNCIA EMOCIONAL TE AJUDAM A VENDER MAIS ?": "Pré-Vendas",
    "COMO AUTOCONHECIMENTO E INTELIGÊNCIA EMOCIONAL TE AJUDAM A VENDER MAIS?": "Pré-Vendas",
    "Como se preparar para uma reunião de forecast?": "Executivos",
    "Inicie sua jornada de Canais por aqui": "Canais e Parcerias",
    "COMO INTELIGÊNCIA ARTIFICIAL VAI TRANSFORMAR O MARKETING B2B?": "Gestão",
    "Arquitetura de concessões: ceder sem destruir margem": "Executivos",
    "COMO MAPEAR A CULTURA DE SEU TIME DE VENDAS": "Gestão",
    "Rituais de Gestão de Vendas B2B": "Gestão",
    "A ROTA DO VENDEDOR AO GERENTE DE VENDAS": "Gestão",
    "Construindo uma Carreira Internacional em Vendas B2B": "Pré-Vendas",
}


def load_titulo_grupo_map():
    path = DATA / "biblioteca_pai.xlsx"
    mapping = {}
    for sheet, default_grupo in SHEETS_MAP.items():
        try:
            df = pd.read_excel(path, sheet_name=sheet, header=7)
        except Exception:
            continue
        if "Nome do conteúdo" not in df.columns:
            continue
        df = df.dropna(subset=["Nome do conteúdo"])
        if "Grupo" in df.columns:
            for titulo, grupo in zip(df["Nome do conteúdo"], df["Grupo"]):
                if pd.isna(titulo):
                    continue
                g = grupo if (isinstance(grupo, str) and grupo.strip()) else default_grupo
                mapping[titulo.strip()] = NORM_GRUPO.get(g, g)
        else:
            for titulo in df["Nome do conteúdo"]:
                if pd.isna(titulo):
                    continue
                mapping[titulo.strip()] = NORM_GRUPO.get(default_grupo, default_grupo)

    try:
        notas = pd.read_excel(path, sheet_name="NOTAS")
        notas.columns = ["titulo", "x", "grupo"][: len(notas.columns)]
        for titulo, grupo in zip(notas["titulo"], notas["grupo"]):
            if pd.isna(titulo):
                continue
            t = titulo.strip()
            if t not in mapping:
                g = grupo if isinstance(grupo, str) else "Sem Grupo Identificado"
                mapping[t] = NORM_GRUPO.get(g, g)
    except Exception:
        pass

    # correções manuais tem prioridade sobre a planilha
    mapping.update(MANUAL_OVERRIDES)

    return mapping


def _norm_ws(s):
    """Colapsa espacos duplos/multiplos em um so, e tira espacos nas pontas."""
    return re.sub(r"\s+", " ", s).strip()


def _map_case_insensitive(series, mapping):
    """Cruza uma coluna de titulos com o mapa titulo->grupo, tentando: match
    exato, depois ignorando maiusculas/minusculas, depois normalizando espacos
    duplicados (varios titulos da Biblioteca PAI e do Waid vem com espacamento
    ou capitalizacao diferentes e nao batiam no match exato)."""
    mapping_lower = {k.lower(): v for k, v in mapping.items()}
    mapping_norm = {_norm_ws(k).lower(): v for k, v in mapping.items()}

    exato = series.map(mapping)
    faltando = exato.isna()
    if faltando.any():
        via_lower = series[faltando].str.lower().map(mapping_lower)
        exato.loc[faltando] = via_lower
    faltando = exato.isna()
    if faltando.any():
        via_norm = series[faltando].apply(_norm_ws).str.lower().map(mapping_norm)
        exato.loc[faltando] = via_norm
    return exato


def load_aulas_ao_vivo():
    try:
        zoom = pd.read_excel(DATA / "consumos_zoom.xlsx", sheet_name="Dados")
        return set(zoom["Nome da Aula"].dropna().str.strip().unique())
    except Exception:
        return set()


# ---------------------------------------------------------------
# 4. CONSUMO (classes_progress)
# ---------------------------------------------------------------
def load_consumo(mapping, aulas_ao_vivo, usuarios):
    if USE_SUPABASE:
        empty_cols = ["Email", "Conteúdo", "data"]
        live = _supabase_fetch("consumo_aulas")
        historico = _supabase_fetch("consumo_aulas_historico")

        if len(live):
            # so contam como consumo os registros que a Waid marcou com 100%
            # (na pratica e o unico valor confiavel que ela envia hoje - ver
            # nota no README sobre o bug de progresso parcial reportado a Waid)
            live = live[live["progress"] == 100].copy()
        if len(live):
            live["Email"] = live["member_email"].astype(str).str.strip().str.lower()
            live["Conteúdo"] = live["content_title"].astype(str).str.strip()
            data_bruta = live["completed_at"].fillna(live["last_event_at"])
            live["data"] = pd.to_datetime(data_bruta, utc=True, errors="coerce").dt.tz_convert(None)
            live = live[empty_cols]
        else:
            live = pd.DataFrame(columns=empty_cols)

        if len(historico):
            historico["Email"] = historico["member_email"].astype(str).str.strip().str.lower()
            historico["Conteúdo"] = historico["content_title"].astype(str).str.strip()
            historico["data"] = pd.to_datetime(historico["completed_at"], utc=True, errors="coerce").dt.tz_convert(None)
            historico = historico[empty_cols]
        else:
            historico = pd.DataFrame(columns=empty_cols)

        print(f"  (via Supabase: {len(live)} eventos em tempo real + {len(historico)} do historico)")
        cp = pd.concat([historico, live], ignore_index=True)
        cp = cp.dropna(subset=["data"])
    else:
        cp = pd.read_excel(DATA / "classes_progress.xlsx")
        cp["Email"] = cp["Email"].astype(str).str.strip().str.lower()
        cp["Nome da aula"] = cp["Nome da aula"].astype(str).str.strip()
        cp["Conteúdo"] = cp["Conteúdo"].astype(str).str.strip()
        cp["data"] = pd.to_datetime(cp["Data de conclusão"], format="%d/%m/%Y %H:%M", errors="coerce")
        cp = cp.dropna(subset=["data"])

    # "Conteúdo" bate com a Biblioteca PAI com taxa de match bem maior que "Nome da aula"
    # (que às vezes vem com prefixo de módulo/expert concatenado). Usamos Conteúdo como
    # chave de cruzamento de grupo e de identificação de aula ao vivo.
    cp["grupo"] = _map_case_insensitive(cp["Conteúdo"], mapping).fillna("Sem Grupo Identificado")
    aulas_ao_vivo_lower = {a.lower() for a in aulas_ao_vivo}
    cp["tipo"] = np.where(
        cp["Conteúdo"].isin(aulas_ao_vivo) | cp["Conteúdo"].str.lower().isin(aulas_ao_vivo_lower),
        "Ao Vivo", "Gravado"
    )
    cp["semana"] = cp["data"].dt.strftime("%G-W%V")
    cp["mes"] = cp["data"].dt.strftime("%Y-%m")

    email_status = usuarios.drop_duplicates(subset="email").set_index("email")[
        ["usuario_ativo", "status_conta", "nome_conta", "id_conta", "csm"]
    ]
    cp = cp.join(email_status, on="Email")
    return cp


# ---------------------------------------------------------------
# 5. AGREGACOES DE CONSUMO
# ---------------------------------------------------------------
def agregacoes_consumo(cp):
    def agg_consumo(freq_col):
        return (
            cp.groupby([freq_col, "grupo", "tipo"]).size().reset_index(name="aulas_assistidas").sort_values(freq_col)
        )


    def agg_users(freq_col):
        return (
            cp.dropna(subset=["Email"])
            .groupby([freq_col, "grupo", "tipo"])["Email"]
            .nunique()
            .reset_index(name="usuarios_unicos")
            .sort_values(freq_col)
        )

    consumo_semana = agg_consumo("semana")
    consumo_mes = agg_consumo("mes")
    users_semana = agg_users("semana")
    users_mes = agg_users("mes")

    mau_mes = cp.dropna(subset=["Email"]).groupby("mes")["Email"].nunique().reset_index(name="mau").sort_values("mes")
    mau_mes["variacao_pct"] = mau_mes["mau"].pct_change() * 100

    # Volume medio considera so os ultimos 12 meses FECHADOS (exclui o mes parcial e os
    # meses iniciais de baixíssimo volume, que distorciam a media historica pra baixo).
    vol_mes = cp.groupby("mes").size().reset_index(name="total_aulas").sort_values("mes")
    usuarios_por_mes = cp.groupby("mes")["Email"].nunique()
    meses_fechados = [m for m in vol_mes["mes"] if m != MES_ATUAL]
    ultimos_12 = meses_fechados[-12:]
    vol_ultimos_12 = vol_mes[vol_mes["mes"].isin(ultimos_12)]
    media_aulas_mes = float(vol_ultimos_12["total_aulas"].mean()) if len(vol_ultimos_12) else None
    media_por_usuario = float(
        (vol_ultimos_12.set_index("mes")["total_aulas"] / usuarios_por_mes.reindex(ultimos_12)).mean()
    ) if len(vol_ultimos_12) else None

    return (
        consumo_semana, consumo_mes, users_semana, users_mes, mau_mes,
        {
            "media_aulas_por_mes": media_aulas_mes,
            "media_aulas_por_usuario_mes": media_por_usuario,
            "periodo": f"{ultimos_12[0]} a {ultimos_12[-1]}" if ultimos_12 else "—",
        },
    )


# ---------------------------------------------------------------
# 5b. MAU POR GRUPO DE CONTEUDO (aprofundamento da aba Usuarios & MAU)
# ---------------------------------------------------------------
def mau_por_grupo(cp):
    """MAU mensal por grupo de conteudo: usuarios unicos que consumiram
    QUALQUER aula daquele grupo naquele mes (ao vivo + gravado somados sem
    duplicar usuario)."""
    mau_grupo = (
        cp.dropna(subset=["Email"])
        .groupby(["mes", "grupo"])["Email"]
        .nunique()
        .reset_index(name="mau")
        .sort_values("mes")
    )
    return mau_grupo


# ---------------------------------------------------------------
# 6. REVIEWS
# ---------------------------------------------------------------
def load_reviews(mapping):
    rv = pd.read_excel(DATA / "reviews_contents.xlsx")
    rv["Curso"] = rv["Curso"].astype(str).str.strip()
    rv["grupo"] = _map_case_insensitive(rv["Curso"], mapping).fillna("Sem Grupo Identificado")
    nota_grupo = rv.groupby("grupo")["Avaliação"].agg(["mean", "count"]).reset_index()
    nota_grupo.columns = ["grupo", "nota_media", "qtd_avaliacoes"]
    nota_grupo["nota_media"] = nota_grupo["nota_media"].round(2)
    nota_grupo["tipo"] = "Gravado"

    # detalhe individual, para a tabela de nota + grupo + comentario qualitativo
    detalhe = rv[["Aluno", "Curso", "grupo", "Avaliação", "Mensagem", "Data da Avaliação"]].copy()
    detalhe.columns = ["aluno", "curso", "grupo", "nota", "comentario", "data"]
    detalhe["comentario"] = detalhe["comentario"].fillna("")
    detalhe["tipo"] = "Gravado"
    detalhe = detalhe.sort_values("nota", ascending=True)

    return nota_grupo, detalhe


def _classificar_topico_ao_vivo(topico):
    """Classifica o topico da reuniao Zoom no grupo de conteudo, usando os
    prefixos de marca que a PipeLovers usa nos titulos das sessoes ao vivo
    (ex: 'PipeLovers💜 Canais & Parcerias:', 'PipeLovers🧡 Executivos de
    Vendas:', 'PipeLovers💚SDRs:' etc) -- mais robusto que tentar casar o
    titulo inteiro com a Biblioteca PAI, que nem sempre tem a sessao ao vivo
    cadastrada com o mesmo texto."""
    if not isinstance(topico, str) or not topico.strip() or topico.strip() == "N/D":
        return "Sem Grupo Identificado"
    t = topico.lower()
    if "canais" in t or "parcerias" in t:
        return "Canais e Parcerias"
    if "gestão comercial" in t or "gestao comercial" in t or "programa de gestão" in t or "programa de gestao" in t:
        return "Gestão"
    if "executivos de vendas" in t:
        return "Executivos"
    if "certificação de vendedores b2b" in t or "certificacao de vendedores b2b" in t:
        return "Executivos"
    if "sdrs" in t or "pré-vendas" in t or "pre-vendas" in t or "especialização em prospecção" in t or "especializacao em prospeccao" in t:
        return "Pré-Vendas"
    if "certificação em inteligência artificial" in t or "certificacao em inteligencia artificial" in t or "bench" in t:
        return "Programas Especiais"
    if "class" in t:
        return "Class"
    return "Sem Grupo Identificado"


def load_csat_ao_vivo():
    """Le o export de CSAT das sessoes ao vivo (pesquisa pos-aula do Zoom).
    O arquivo vem com secoes extras antes da tabela de respostas de verdade
    e com colunas 'fantasma' de padding no final -- pulamos ate a linha do
    cabecalho real e limitamos as 15 colunas que interessam."""
    path = DATA / "csat_ao_vivo.csv"
    if not path.exists():
        return pd.DataFrame(columns=["grupo", "nota_media", "qtd_avaliacoes", "tipo"]), pd.DataFrame(
            columns=["aluno", "curso", "grupo", "nota", "comentario", "data", "tipo"]
        )

    cols = [
        "idx", "id_usuario", "nome_usuario", "email", "data_envio", "coletado_de", "topico",
        "id_reuniao", "nome_resposta", "nota_geral", "nota_aplicacao", "nota_conhecimento",
        "nota_comunicacao", "comentario_critica", "comentario_sugestao",
    ]
    # acha a linha do cabecalho de verdade procurando pela coluna conhecida
    with open(path, encoding="utf-8-sig") as f:
        linhas = f.readlines()
    header_idx = next(
        (i for i, l in enumerate(linhas) if l.startswith("#,ID do usuário") or l.startswith("#,\"ID do usuário")),
        None,
    )
    if header_idx is None:
        return pd.DataFrame(columns=["grupo", "nota_media", "qtd_avaliacoes", "tipo"]), pd.DataFrame(
            columns=["aluno", "curso", "grupo", "nota", "comentario", "data", "tipo"]
        )

    rv = pd.read_csv(path, skiprows=header_idx + 1, header=None, usecols=range(15), names=cols, encoding="utf-8-sig")
    rv["grupo"] = rv["topico"].apply(_classificar_topico_ao_vivo)

    nota_grupo_vivo = rv.groupby("grupo")["nota_geral"].agg(["mean", "count"]).reset_index()
    nota_grupo_vivo.columns = ["grupo", "nota_media", "qtd_avaliacoes"]
    nota_grupo_vivo["nota_media"] = nota_grupo_vivo["nota_media"].round(2)
    nota_grupo_vivo["tipo"] = "Ao Vivo"

    detalhe_vivo = rv[["nome_resposta", "topico", "grupo", "nota_geral", "comentario_critica", "data_envio"]].copy()
    detalhe_vivo.columns = ["aluno", "curso", "grupo", "nota", "comentario", "data"]
    detalhe_vivo["comentario"] = detalhe_vivo["comentario"].fillna("")
    detalhe_vivo["aluno"] = detalhe_vivo["aluno"].fillna("—")
    detalhe_vivo["tipo"] = "Ao Vivo"
    detalhe_vivo = detalhe_vivo.dropna(subset=["nota"]).sort_values("nota", ascending=True)

    return nota_grupo_vivo, detalhe_vivo


# ---------------------------------------------------------------
# 6b. FAIXAS DE MAU POR CONTA (saude da carteira)
# ---------------------------------------------------------------
def mau_buckets(empresas_df):
    total = len(empresas_df)
    if total == 0:
        return {"acima_50": {"n": 0, "pct": 0}, "entre_25_49": {"n": 0, "pct": 0}, "abaixo_25": {"n": 0, "pct": 0}, "total": 0}
    acima_50 = int((empresas_df["mau_pct"] >= 50).sum())
    entre_25_49 = int(((empresas_df["mau_pct"] >= 25) & (empresas_df["mau_pct"] < 50)).sum())
    abaixo_25 = int((empresas_df["mau_pct"] < 25).sum())
    return {
        "acima_50": {"n": acima_50, "pct": round(acima_50 / total * 100, 1)},
        "entre_25_49": {"n": entre_25_49, "pct": round(entre_25_49 / total * 100, 1)},
        "abaixo_25": {"n": abaixo_25, "pct": round(abaixo_25 / total * 100, 1)},
        "total": total,
    }


# ---------------------------------------------------------------
# 7. DOWNLOADS x CONSUMO
# ---------------------------------------------------------------
def load_downloads(cp):
    dl = pd.read_excel(DATA / "downloads.xlsx")
    dl["E-mail"] = dl["E-mail"].astype(str).str.strip().str.lower()
    dl["Conteúdo"] = dl["Conteúdo"].astype(str).str.strip()
    watched_pairs = set(zip(cp["Email"], cp["Conteúdo"]))
    dl["assistiu_depois"] = dl.apply(lambda r: (r["E-mail"], r["Conteúdo"]) in watched_pairs, axis=1)
    return {
        "baixou_e_assistiu": int(dl["assistiu_depois"].sum()),
        "baixou_nao_assistiu": int((~dl["assistiu_depois"]).sum()),
        "total_downloads": int(len(dl)),
    }


# ---------------------------------------------------------------
# 8. DORMENTES
# ---------------------------------------------------------------
def dormentes(usuarios, cp):
    last_consumo = cp.groupby("Email")["data"].max()
    usuarios = usuarios.copy()
    usuarios["ultimo_consumo"] = usuarios["email"].map(last_consumo)
    usuarios["nunca_consumiu"] = usuarios["ultimo_consumo"].isna()
    usuarios["dias_desde_ultimo"] = (TODAY - usuarios["ultimo_consumo"]).dt.days

    ativos = usuarios[usuarios["usuario_ativo"]].copy()
    nunca_mask = ativos["nunca_consumiu"]
    seis_mask = (~nunca_mask) & (ativos["dias_desde_ultimo"] > 182)
    tres_mask = (~nunca_mask) & (ativos["dias_desde_ultimo"] > 90)

    def to_lista(mask):
        cols = ["nome", "email", "nome_conta", "csm", "dias_desde_ultimo"]
        d = ativos.loc[mask, cols].copy()
        d["dias_desde_ultimo"] = d["dias_desde_ultimo"].apply(lambda x: None if pd.isna(x) else int(x))
        d = d.rename(columns={"nome": "nome_usuario"})
        d = d.fillna({"nome_conta": "—", "csm": "—"})
        return d.sort_values("dias_desde_ultimo", ascending=False, na_position="first").to_dict("records")

    resumo = {
        "nunca_consumiram": int(nunca_mask.sum()),
        "sem_consumo_6m": int(seis_mask.sum()),
        "sem_consumo_3m": int(tres_mask.sum()),
        "total_usuarios_ativos": int(len(ativos)),
        "total_usuarios": int(len(usuarios)),
    }
    listas = {
        "nunca": to_lista(nunca_mask),
        "seis_meses": to_lista(seis_mask),
        "tres_meses": to_lista(tres_mask),
    }
    return usuarios, resumo, listas


# ---------------------------------------------------------------
# 9. EMPRESAS ATIVAS (MAU% por conta)
# ---------------------------------------------------------------
def empresas_ativas(usuarios, cp, contas, conta_csm_map):
    consumo_mes_email = cp[cp["mes"] == ULTIMO_MES_FECHADO].groupby("Email").size()
    usuarios = usuarios.copy()
    usuarios["ativo_mes_ref"] = usuarios["email"].isin(consumo_mes_email.index)

    por_conta = usuarios.groupby("id_conta").agg(
        total_usuarios=("id", "count"),
        usuarios_ativos_mes=("ativo_mes_ref", "sum"),
    ).reset_index()

    # parte de TODAS as contas ativas (nao so das que tem usuario cadastrado em /usuarios),
    # pra nao perder contas sem nenhum usuario na contagem total.
    ativas_base = contas[contas["status_conta"] == "Ativa"][["id", "nome"]].rename(
        columns={"id": "id_conta", "nome": "nome_conta"}
    )
    ativas = ativas_base.merge(por_conta, on="id_conta", how="left")
    ativas["total_usuarios"] = ativas["total_usuarios"].fillna(0).astype(int)
    ativas["usuarios_ativos_mes"] = ativas["usuarios_ativos_mes"].fillna(0).astype(int)
    ativas["csm"] = ativas["id_conta"].map(conta_csm_map).fillna("—")
    ativas["mau_pct"] = np.where(
        ativas["total_usuarios"] > 0,
        (ativas["usuarios_ativos_mes"] / ativas["total_usuarios"] * 100).round(1),
        0.0,
    )
    return ativas.sort_values("mau_pct", ascending=False)


# ---------------------------------------------------------------
# 10. TIME TO FIRST VALUE (TTFV)
# ---------------------------------------------------------------
def ttfv(usuarios, cp):
    primeiro_consumo = cp.groupby("Email")["data"].min()
    u = usuarios.copy()
    u["primeiro_consumo"] = u["email"].map(primeiro_consumo)
    valid = u.dropna(subset=["primeiro_consumo", "data_criacao"])
    dias = (valid["primeiro_consumo"].dt.tz_localize(None) - valid["data_criacao"]).dt.days
    dias = dias[(dias >= 0) & (dias <= 365)]
    if len(dias) == 0:
        return {"media_dias": None, "mediana_dias": None, "amostra": 0}
    return {
        "media_dias": round(float(dias.mean()), 1),
        "mediana_dias": float(dias.median()),
        "amostra": int(len(dias)),
    }


# ---------------------------------------------------------------
# 11. RETENCAO POR COORTE (ativacao)
# ---------------------------------------------------------------
def cohort_retencao(cp, inicio="2026-01"):
    primeiro_consumo = cp.groupby("Email")["data"].min()
    ativos_por_mes = cp.groupby("mes")["Email"].apply(set)
    cohort_mes = primeiro_consumo.dt.strftime("%Y-%m")

    meses_ordenados = sorted(ativos_por_mes.index)
    if len(meses_ordenados) < 2:
        return []
    # cohorts desde o inicio do ano corrente ate o mes mais recente com dado (inclui o mes parcial)
    cohorts_recentes = [m for m in meses_ordenados if m >= inicio]

    resultado = []
    for i, cm in enumerate(cohorts_recentes):
        usuarios_cohort = set(cohort_mes[cohort_mes == cm].index)
        if not usuarios_cohort:
            continue
        linha = {"cohort": cm, "tamanho": len(usuarios_cohort)}
        idx_cm = meses_ordenados.index(cm)
        for offset in range(0, 10):
            idx = idx_cm + offset
            if idx >= len(meses_ordenados):
                linha[f"m{offset}"] = None
                continue
            mes_alvo = meses_ordenados[idx]
            ativos_no_mes = ativos_por_mes.get(mes_alvo, set())
            retidos = len(usuarios_cohort & ativos_no_mes)
            linha[f"m{offset}"] = round(retidos / len(usuarios_cohort) * 100, 1)
        resultado.append(linha)
    return resultado


# ---------------------------------------------------------------
# 12. LEAD TIME ENTRE FIM DO CONSUMO E CHURN
# ---------------------------------------------------------------
def lead_time_churn(contratos, usuarios, cp):
    churned = contratos[~contratos["ativo"]].dropna(subset=["data_churn"])
    last_consumo_conta = cp.dropna(subset=["id_conta"]).groupby("id_conta")["data"].max()
    dias_list = []
    for _, row in churned.iterrows():
        ultimo = last_consumo_conta.get(row["conta_id"])
        if ultimo is None or pd.isna(ultimo):
            continue
        churn_date = row["data_churn"].tz_localize(None) if row["data_churn"].tzinfo else row["data_churn"]
        d = (churn_date - ultimo).days
        if -30 <= d <= 730:  # remove outliers grosseiros
            dias_list.append(d)
    if not dias_list:
        return {"media_dias": None, "mediana_dias": None, "amostra": 0}
    s = pd.Series(dias_list)
    return {"media_dias": round(float(s.mean()), 1), "mediana_dias": float(s.median()), "amostra": int(len(s))}


# ---------------------------------------------------------------
# 13. AULAS SEM GRUPO IDENTIFICADO
# ---------------------------------------------------------------
def aulas_sem_grupo(cp):
    sem = cp[cp["grupo"] == "Sem Grupo Identificado"]
    contagem = sem.groupby("Conteúdo").size().reset_index(name="ocorrencias").sort_values(
        "ocorrencias", ascending=False
    )
    return contagem


# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------
def main():
    print(f"Gerando dashboard | hoje={TODAY.date()} | ultimo mes fechado={ULTIMO_MES_FECHADO}")

    contas, contratos, conta_status_map, conta_nome_map, conta_csm_map, contas_resumo = load_contas_contratos()
    usuarios, usuarios_resumo = load_usuarios(conta_status_map, conta_nome_map, conta_csm_map)

    mapping = load_titulo_grupo_map()
    aulas_ao_vivo = load_aulas_ao_vivo()
    cp = load_consumo(mapping, aulas_ao_vivo, usuarios)

    sem_grupo_pct = (cp["grupo"] == "Sem Grupo Identificado").mean() * 100

    consumo_semana, consumo_mes, users_semana, users_mes, mau_mes, volume_medio = agregacoes_consumo(cp)
    mau_grupo_mes = mau_por_grupo(cp)
    nota_grupo, reviews_detalhe = load_reviews(mapping)
    nota_grupo_vivo, reviews_detalhe_vivo = load_csat_ao_vivo()
    nota_grupo_combinado = pd.concat([nota_grupo, nota_grupo_vivo], ignore_index=True)
    downloads_cross = load_downloads(cp)
    usuarios_dorm, dormentes_resumo, dormentes_listas = dormentes(usuarios, cp)
    empresas = empresas_ativas(usuarios, cp, contas, conta_csm_map)
    mau_buckets_resumo = mau_buckets(empresas)
    ttfv_resumo = ttfv(usuarios, cp)
    cohort = cohort_retencao(cp)
    lead_time = lead_time_churn(contratos, usuarios, cp)
    sem_grupo_tabela = aulas_sem_grupo(cp)

    # MAU como % da base de usuarios ativos
    mau_mes_list = mau_mes.to_dict("records")
    for r in mau_mes_list:
        r["mau_pct_da_base_ativa"] = (
            round(r["mau"] / usuarios_resumo["ativos"] * 100, 1) if usuarios_resumo["ativos"] else None
        )
    fechados = [r for r in mau_mes_list if r["mes"] != MES_ATUAL]
    mau_resumo = {
        "mes_atual_pct": fechados[-1]["mau_pct_da_base_ativa"] if fechados else None,
        "mes_atual_mes": fechados[-1]["mes"] if fechados else None,
        "media_pct_12m": (
            round(sum(r["mau_pct_da_base_ativa"] for r in fechados[-12:]) / len(fechados[-12:]), 1)
            if fechados else None
        ),
    }

    master = {
        "contas": contas_resumo,
        "usuarios": usuarios_resumo,
        "consumo_semana": consumo_semana.to_dict("records"),
        "consumo_mes": consumo_mes.to_dict("records"),
        "users_semana": users_semana.to_dict("records"),
        "users_mes": users_mes.to_dict("records"),
        "mau_mes": mau_mes_list,
        "mau_grupo_mes": mau_grupo_mes.to_dict("records"),
        "mau_resumo": mau_resumo,
        "volume_medio": volume_medio,
        "nota_grupo": nota_grupo.to_dict("records"),
        "nota_grupo_vivo": nota_grupo_vivo.to_dict("records"),
        "nota_grupo_combinado": nota_grupo_combinado.to_dict("records"),
        "reviews_detalhe": reviews_detalhe.to_dict("records"),
        "reviews_detalhe_vivo": reviews_detalhe_vivo.to_dict("records"),
        "downloads_cross": downloads_cross,
        "dormentes": dormentes_resumo,
        "dormentes_listas": dormentes_listas,
        "empresas_ativas": empresas.to_dict("records"),
        "mau_buckets": mau_buckets_resumo,
        "ttfv": ttfv_resumo,
        "cohort_retencao": cohort,
        "lead_time_churn": lead_time,
        "sem_grupo_tabela": sem_grupo_tabela.to_dict("records"),
        "meta": {
            "gerado_em": TODAY.strftime("%Y-%m-%d"),
            "aviso_grupo": (
                f"{sem_grupo_pct:.0f}% dos registros de consumo nao tiveram o titulo da aula "
                "encontrado exatamente na Biblioteca PAI (Sem Grupo Identificado). Recomenda-se "
                "padronizar os titulos entre Waid e a planilha PAI."
            ),
            "mes_parcial": MES_ATUAL,
        },
    }
    master = clean_json(master)

    template = TEMPLATE.read_text(encoding="utf-8")
    html = template.replace("__DATA_PLACEHOLDER__", json.dumps(master, ensure_ascii=False))
    html = html.replace(
        "17 de setembro de 2026", TODAY.strftime("%d/%m/%Y")
    )
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"OK -> {OUTPUT} ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
