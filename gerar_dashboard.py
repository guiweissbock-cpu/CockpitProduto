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
def empresas_ativas(usuarios, cp):
    consumo_mes_email = cp[cp["mes"] == ULTIMO_MES_FECHADO].groupby("Email").size()
    usuarios = usuarios.copy()
    usuarios["ativo_mes_ref"] = usuarios["email"].isin(consumo_mes_email.index)

    por_conta = usuarios.groupby("id_conta").agg(
        nome_conta=("nome_conta", "first"),
        total_usuarios=("id", "count"),
        usuarios_ativos_mes=("ativo_mes_ref", "sum"),
        status_conta=("status_conta", "first"),
        csm=("csm", "first"),
    ).reset_index()
    por_conta["mau_pct"] = (por_conta["usuarios_ativos_mes"] / por_conta["total_usuarios"] * 100).round(1)
    ativas = por_conta[por_conta["status_conta"] == "Ativa"].sort_values("mau_pct", ascending=False)
    return ativas.fillna({"csm": "—"})


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
def cohort_retencao(cp):
    primeiro_consumo = cp.groupby("Email")["data"].min()
    ativos_por_mes = cp.groupby("mes")["Email"].apply(set)
    cohort_mes = primeiro_consumo.dt.strftime("%Y-%m")

    meses_ordenados = sorted(ativos_por_mes.index)
    if len(meses_ordenados) < 2:
        return []
    cohorts_recentes = meses_ordenados[-7:-1]  # ultimos 6 cohorts fechados (exclui mes parcial)

    resultado = []
    for i, cm in enumerate(cohorts_recentes):
        usuarios_cohort = set(cohort_mes[cohort_mes == cm].index)
        if not usuarios_cohort:
            continue
        linha = {"cohort": cm, "tamanho": len(usuarios_cohort)}
        idx_cm = meses_ordenados.index(cm)
        for offset in range(0, 4):
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
    contagem = sem.groupby("Nome da aula").size().reset_index(name="ocorrencias").sort_values(
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
    nota_grupo = load_reviews(mapping)
    downloads_cross = load_downloads(cp)
    usuarios_dorm, dormentes_resumo, dormentes_listas = dormentes(usuarios, cp)
    empresas = empresas_ativas(usuarios, cp)
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

    master = {
        "contas": contas_resumo,
        "usuarios": usuarios_resumo,
        "consumo_semana": consumo_semana.to_dict("records"),
        "consumo_mes": consumo_mes.to_dict("records"),
        "users_semana": users_semana.to_dict("records"),
        "users_mes": users_mes.to_dict("records"),
        "mau_mes": mau_mes_list,
        "volume_medio": volume_medio,
        "nota_grupo": nota_grupo.to_dict("records"),
        "downloads_cross": downloads_cross,
        "dormentes": dormentes_resumo,
        "dormentes_listas": dormentes_listas,
        "empresas_ativas": empresas.to_dict("records"),
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
