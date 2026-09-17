"""
PipeLovers — Gerador do Painel de Engajamento & Saúde da Base
================================================================
Le os arquivos em /data, cruza tudo (contas, contratos, usuarios,
consumo, avaliacoes, downloads, biblioteca de conteudos e sessoes
Zoom) e gera um index.html autocontido (dados embutidos) na raiz
do repositorio.

Como atualizar os dados no dia a dia:
  1. Exporte as planilhas mais recentes do Waid / Hubla / base B2B.
  2. Sobrescreva os arquivos em /data mantendo EXATAMENTE os mesmos
     nomes de arquivo (veja a lista em ARQUIVOS abaixo).
  3. Rode `python gerar_dashboard.py` (ou deixe o GitHub Action
     rodar sozinho todo dia / a cada push em /data).

Arquivos esperados em /data:
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
  biblioteca_pai.xlsx    -> planilha PAI de biblioteca de conteudos
                            (muda pouco, so re-exporte se o
                            catalogo de aulas mudar)
  consumos_zoom.xlsx     -> export de sessoes ao vivo do Zoom (para
                            identificar quais aulas sao "Ao Vivo")
"""
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "index.html"

# Referencia de "hoje" e do ultimo mes fechado usadas nos calculos de
# dormencia / tendencia. Ajuste se quiser travar uma data especifica;
# por padrao usamos a data corrente do sistema que roda o script.
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
    contas = pd.read_csv(DATA / "contasb2b.csv")
    contratos = pd.read_csv(DATA / "contratos.csv")
    contratos["data_churn"] = pd.to_datetime(contratos["data_churn"], errors="coerce", utc=True)
    contratos["ativo"] = contratos["data_churn"].isna()

    active_ids = set(contratos.loc[contratos["ativo"], "conta_id"])
    contas["status_conta"] = contas["id"].apply(lambda x: "Ativa" if x in active_ids else "Inativa")

    conta_status_map = contas.set_index("id")["status_conta"].to_dict()
    conta_nome_map = contas.set_index("id")["nome"].to_dict()

    resumo = {
        "ativas": int((contas["status_conta"] == "Ativa").sum()),
        "inativas": int((contas["status_conta"] == "Inativa").sum()),
        "total": int(len(contas)),
    }
    return contas, contratos, conta_status_map, conta_nome_map, resumo


# ---------------------------------------------------------------
# 2. USUARIOS
# ---------------------------------------------------------------
def load_usuarios(conta_status_map, conta_nome_map):
    usuarios = pd.read_csv(DATA / "usuarios.csv")
    usuarios["email"] = usuarios["email"].astype(str).str.strip().str.lower()
    usuarios["data_criacao"] = pd.to_datetime(usuarios["data_criacao"], errors="coerce")
    usuarios["status_conta"] = usuarios["id_conta"].map(conta_status_map)
    usuarios["nome_conta"] = usuarios["id_conta"].map(conta_nome_map)
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

    return mapping


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
    cp = pd.read_excel(DATA / "classes_progress.xlsx")
    cp["Email"] = cp["Email"].astype(str).str.strip().str.lower()
    cp["Nome da aula"] = cp["Nome da aula"].astype(str).str.strip()
    cp["data"] = pd.to_datetime(cp["Data de conclusão"], format="%d/%m/%Y %H:%M", errors="coerce")
    cp = cp.dropna(subset=["data"])

    cp["grupo"] = cp["Nome da aula"].map(mapping).fillna("Sem Grupo Identificado")
    cp["tipo"] = np.where(cp["Nome da aula"].isin(aulas_ao_vivo), "Ao Vivo", "Gravado")
    cp["semana"] = cp["data"].dt.strftime("%G-W%V")
    cp["mes"] = cp["data"].dt.strftime("%Y-%m")

    email_status = usuarios.drop_duplicates(subset="email").set_index("email")[
        ["usuario_ativo", "status_conta", "nome_conta", "id_conta"]
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

    vol_mes = cp.groupby("mes").size().reset_index(name="total_aulas")
    media_aulas_mes = float(vol_mes["total_aulas"].mean())
    usuarios_por_mes = cp.groupby("mes")["Email"].nunique()
    media_por_usuario = float((vol_mes.set_index("mes")["total_aulas"] / usuarios_por_mes).mean())

    return (
        consumo_semana, consumo_mes, users_semana, users_mes, mau_mes,
        {"media_aulas_por_mes": media_aulas_mes, "media_aulas_por_usuario_mes": media_por_usuario},
    )


# ---------------------------------------------------------------
# 6. REVIEWS
# ---------------------------------------------------------------
def load_reviews(mapping):
    rv = pd.read_excel(DATA / "reviews_contents.xlsx")
    rv["Curso"] = rv["Curso"].astype(str).str.strip()
    rv["grupo"] = rv["Curso"].map(mapping).fillna("Sem Grupo Identificado")
    nota_grupo = rv.groupby("grupo")["Avaliação"].agg(["mean", "count"]).reset_index()
    nota_grupo.columns = ["grupo", "nota_media", "qtd_avaliacoes"]
    nota_grupo["nota_media"] = nota_grupo["nota_media"].round(2)
    return nota_grupo


# ---------------------------------------------------------------
# 7. DOWNLOADS x CONSUMO
# ---------------------------------------------------------------
def load_downloads(cp):
    dl = pd.read_excel(DATA / "downloads.xlsx")
    dl["E-mail"] = dl["E-mail"].astype(str).str.strip().str.lower()
    dl["Aula"] = dl["Aula"].astype(str).str.strip()
    watched_pairs = set(zip(cp["Email"], cp["Nome da aula"]))
    dl["assistiu_depois"] = dl.apply(lambda r: (r["E-mail"], r["Aula"]) in watched_pairs, axis=1)
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

    nunca = int(usuarios["nunca_consumiu"].sum())
    seis_meses = int(((~usuarios["nunca_consumiu"]) & (usuarios["dias_desde_ultimo"] > 182)).sum())
    tres_meses = int(((~usuarios["nunca_consumiu"]) & (usuarios["dias_desde_ultimo"] > 90)).sum())

    return usuarios, {
        "nunca_consumiram": nunca,
        "sem_consumo_6m": seis_meses,
        "sem_consumo_3m": tres_meses,
        "total_usuarios": int(len(usuarios)),
    }


# ---------------------------------------------------------------
# 9. EMPRESAS COM +50% MAU
# ---------------------------------------------------------------
def empresas_mau50(usuarios, cp):
    consumo_mes_email = cp[cp["mes"] == ULTIMO_MES_FECHADO].groupby("Email").size()
    usuarios = usuarios.copy()
    usuarios["ativo_mes_ref"] = usuarios["email"].isin(consumo_mes_email.index)

    por_conta = usuarios.groupby("nome_conta").agg(
        total_usuarios=("id", "count"),
        usuarios_ativos_mes=("ativo_mes_ref", "sum"),
        status_conta=("status_conta", "first"),
    ).reset_index()
    por_conta["mau_pct"] = (por_conta["usuarios_ativos_mes"] / por_conta["total_usuarios"] * 100).round(1)
    por_conta = por_conta[por_conta["total_usuarios"] >= 3]
    return por_conta[por_conta["mau_pct"] >= 50].sort_values("mau_pct", ascending=False)


# ---------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------
def main():
    print(f"Gerando dashboard | hoje={TODAY.date()} | ultimo mes fechado={ULTIMO_MES_FECHADO}")

    contas, contratos, conta_status_map, conta_nome_map, contas_resumo = load_contas_contratos()
    usuarios, usuarios_resumo = load_usuarios(conta_status_map, conta_nome_map)

    mapping = load_titulo_grupo_map()
    aulas_ao_vivo = load_aulas_ao_vivo()
    cp = load_consumo(mapping, aulas_ao_vivo, usuarios)

    sem_grupo_pct = (cp["grupo"] == "Sem Grupo Identificado").mean() * 100

    consumo_semana, consumo_mes, users_semana, users_mes, mau_mes, volume_medio = agregacoes_consumo(cp)
    nota_grupo = load_reviews(mapping)
    downloads_cross = load_downloads(cp)
    usuarios_dorm, dormentes_resumo = dormentes(usuarios, cp)
    empresas = empresas_mau50(usuarios, cp)

    master = {
        "contas": contas_resumo,
        "usuarios": usuarios_resumo,
        "consumo_semana": consumo_semana.to_dict("records"),
        "consumo_mes": consumo_mes.to_dict("records"),
        "users_semana": users_semana.to_dict("records"),
        "users_mes": users_mes.to_dict("records"),
        "mau_mes": mau_mes.to_dict("records"),
        "volume_medio": volume_medio,
        "nota_grupo": nota_grupo.to_dict("records"),
        "downloads_cross": downloads_cross,
        "dormentes": dormentes_resumo,
        "empresas_mau50": empresas.to_dict("records"),
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
