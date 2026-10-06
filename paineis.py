"""
PipeLovers — montagem das abas do Cockpit (faróis, alertas e insights)
======================================================================
Chamado pelo gerar_dashboard.py depois que todos os dados já foram
calculados. Recebe o dicionário `master` (o mesmo que antes ia inteiro
para o HTML) e alguns DataFrames, e devolve os pedaços de HTML/JSON
que são injetados no template.html.

Regras de status (mesma linguagem em todas as abas):
  ▲ Bom (verde) · ● Atenção (amarelo) · ▼ Crítico (vermelho) · ◆ Monitorar (cinza)
"""
import html
import json
import re

import numpy as np
import pandas as pd

MESES = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
ST = {"ok": ("▲", "Bom"), "warn": ("●", "Atenção"), "bad": ("▼", "Crítico"), "info": ("◆", "Monitorar")}
GRUPOS_CONTEUDO = ["Pré-Vendas", "Executivos", "Gestão", "Canais e Parcerias", "Class", "Programas Especiais"]


# ----------------------------------------------------------------- helpers
def br(x, n=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    return f"{x:,.{n}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def ni(x):
    return "—" if x is None else f"{int(round(x)):,}".replace(",", ".")


def ml(m):
    return f"{MESES[int(m[5:]) - 1]}/{m[2:4]}"


def esc(s):
    return html.escape(str(s if s is not None else ""))


def sinal(v, n=1, suf=""):
    if v is None:
        return "—"
    return ("+" if v >= 0 else "−") + br(abs(v), n) + suf


def spark(vals, w=120, h=32, col="var(--blue)"):
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1
    pts = [(i * (w - 6) / (len(vals) - 1) + 3, h - 4 - ((x if x is not None else lo) - lo) / rng * (h - 8)) for i, x in enumerate(vals)]
    p = " ".join(f"{a:.1f},{b:.1f}" for a, b in pts)
    lx, ly = pts[-1]
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" aria-hidden="true"><polyline points="{p}" fill="none" '
            f'stroke="{col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3" fill="{col}"/></svg>')


def chip(st, txt=None):
    return f'<span class="chip {st}">{ST[st][0]} {txt or ST[st][1]}</span>'


def farol(st, label, valor, sub, trend="", spk="", acao=""):
    return (f'<div class="farol {st}"><div class="f-top"><span class="f-label">{label}</span>{chip(st)}</div>'
            f'<div class="f-val">{valor}</div><div class="f-trend">{trend}</div>{spk}<div class="f-sub">{sub}</div>'
            + (f'<div class="f-acao">→ {acao}</div>' if acao else '') + '</div>')


def item(st, txt):
    return f'<li class="{st}"><span class="dot">{ST[st][0]}</span><span>{txt}</span></li>'


def seta(v, n=1, suf=" p.p."):
    if v is None:
        return '<span class="flat">—</span>'
    if abs(v) < 0.05:
        return f'<span class="flat">● {sinal(v, n)}{suf}</span>'
    return f'<span class="{"up" if v > 0 else "down"}">{"▲" if v > 0 else "▼"} {sinal(v, n)}{suf}</span>'


def pior(sts):
    for s in ("bad", "warn", "ok", "info"):
        if s in sts:
            return s
    return "info"


def linechart(meses, series, lo=None, hi=None, fmt=lambda v: br(v, 1) + "%", parcial=None, w=900, h=260):
    """series: lista de (nome, cor_css, [valores]). Desenha SVG simples, um eixo só."""
    L, R, T, B = 48, 140, 16, 30
    vals = [v for _, _, s in series for v in s if v is not None]
    if not vals:
        return '<div class="empty">Sem dados.</div>'
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    pad = (hi - lo) * 0.1 or 1
    lo, hi = max(0, lo - pad), hi + pad
    y = lambda v: T + (hi - v) / (hi - lo) * (h - T - B)
    x = lambda i: L + i * (w - L - R) / max(len(meses) - 1, 1)
    s = f'<svg viewBox="0 0 {w} {h}" class="linechart" role="img">'
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        s += f'<line x1="{L}" x2="{w - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--line)"/><text class="ax" x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{fmt(v)}</text>'
    for i, m in enumerate(meses):
        s += f'<text class="ax" x="{x(i):.1f}" y="{h - 8}" text-anchor="middle">{MESES[int(m[5:]) - 1]}{"*" if m == parcial else ""}</text>'
    for k, (nome, cor, vs) in enumerate(series):
        pts = [(x(i), y(v), v, meses[i]) for i, v in enumerate(vs) if v is not None]
        if not pts:
            continue
        seg = " ".join(f"{a:.1f},{b:.1f}" for a, b, _, _ in pts)
        s += f'<polyline points="{seg}" fill="none" stroke="{cor}" stroke-width="2.5" stroke-linejoin="round"/>'
        for a, b, v, m in pts:
            s += f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{5 if m == parcial else 4}" fill="{"var(--panel)" if m == parcial else cor}" stroke="{cor}" stroke-width="2"><title>{esc(nome)} {ml(m)}: {fmt(v)}</title></circle>'
        a, b, v, _ = pts[-1]
        s += f'<text class="lbl" x="{a + 8:.1f}" y="{b + (4 if k == 0 else -6):.1f}">{esc(nome)} {fmt(v)}</text>'
    return s + '</svg>'


# ------------------------------------------------------------ cálculos extras
def calcular_extras(cp, usuarios, contas, hoje, mes_atual, ult_fechado):
    """Métricas novas que dependem do consumo aula a aula."""
    ex = {}
    c = cp.dropna(subset=["Email"]).copy()
    c["dia"] = c["data"].dt.normalize()
    meses_f = sorted(m for m in c["mes"].unique() if m < mes_atual)
    ult = ult_fechado
    ant = meses_f[-2] if len(meses_f) >= 2 else None
    seis = meses_f[-6:]

    # --- frequência: dias distintos com aula por usuário que consumiu no mês
    freq = c.groupby(["mes", "Email"])["dia"].nunique().groupby("mes").mean()
    ex["frequencia"] = {m: round(float(freq.get(m, np.nan)), 2) for m in seis}

    # --- conteúdo novo: % das conclusões do mês vindas de aulas que apareceram
    #     pela 1ª vez no consumo nos últimos 90 dias (proxy da data de lançamento)
    c["tit"] = c["Conteúdo"].astype(str).str.strip().str.lower()
    primeira = c.groupby("tit")["data"].min()
    c["estreia"] = c["tit"].map(primeira)
    novo = {}
    for m in seis:
        ini = pd.Timestamp(m + "-01")
        cm = c[c["mes"] == m]
        if len(cm):
            novo[m] = round(float((cm["estreia"] >= ini - pd.Timedelta(days=90)).mean() * 100), 1)
    ex["conteudo_novo"] = novo

    # --- top aulas por grupo no último mês fechado (com variação vs. mês anterior)
    top = {}
    for g in GRUPOS_CONTEUDO:
        a = c[(c["grupo"] == g) & (c["mes"] == ult)].groupby("Conteúdo")["Email"].nunique()
        b = c[(c["grupo"] == g) & (c["mes"] == ant)].groupby("Conteúdo")["Email"].nunique() if ant else pd.Series(dtype=float)
        top[g] = [dict(aula=t, usuarios=int(v), ant=int(b.get(t, 0))) for t, v in a.sort_values(ascending=False).head(5).items()]
    ex["top_aulas"] = top
    a = c[c["mes"] == ult].groupby(["Conteúdo", "grupo"])["Email"].nunique()
    b = c[c["mes"] == ant].groupby(["Conteúdo", "grupo"])["Email"].nunique() if ant else pd.Series(dtype=float)
    dif = pd.concat([a.rename("a"), b.rename("b")], axis=1).fillna(0)
    dif["d"] = dif["a"] - dif["b"]
    dif = dif[(dif["a"] + dif["b"]) >= 15]
    ex["alta"] = [dict(aula=i[0], grupo=i[1], a=int(r.a), b=int(r.b)) for i, r in dif.sort_values("d", ascending=False).head(6).iterrows() if r.d > 0]
    ex["queda"] = [dict(aula=i[0], grupo=i[1], a=int(r.a), b=int(r.b)) for i, r in dif.sort_values("d").head(6).iterrows() if r.d < 0]

    # --- ativação em 30 dias: usuários criados no mês que concluíram a 1ª aula em até 30 dias
    u = usuarios.drop_duplicates("email").copy()
    prim = c.groupby("Email")["data"].min()
    u["prim"] = u["email"].map(prim)
    u["dc"] = pd.to_datetime(u["data_criacao"], errors="coerce")
    u["mes_c"] = u["dc"].dt.strftime("%Y-%m")
    at = {}
    for m in meses_f[-7:]:
        fim = pd.Timestamp(m + "-01") + pd.offsets.MonthEnd(0)
        if fim + pd.Timedelta(days=30) > hoje:
            continue
        coorte = u[u["mes_c"] == m]
        if len(coorte) < 10:
            continue
        ok = ((coorte["prim"] - coorte["dc"]).dt.days.between(-1, 30)).sum()
        at[m] = dict(n=int(len(coorte)), pct=round(float(ok / len(coorte) * 100), 1))
    ex["ativacao"] = at

    # --- MAU por conta (dois critérios) e saúde da conta
    ativas = set(contas.loc[contas["status_conta"] == "Ativa", "id"].astype(str))
    nome = contas.assign(id=contas["id"].astype(str)).set_index("id")["nome"].to_dict()
    uu = usuarios.copy()
    uu["conta"] = uu["id_conta"].astype(str).str.replace(r"\.0$", "", regex=True)
    uu = uu[uu["conta"].isin(ativas)]
    csm = uu.drop_duplicates("conta").set_index("conta")["csm"].to_dict()
    por_mes = {m: set(c.loc[c["mes"] == m, "Email"]) for m in meses_f[-4:]}
    ultimo_cons = c.groupby("Email")["data"].max()
    ja_cons = set(c["Email"])
    rows = []
    for conta, g in uu.groupby("conta"):
        at_em = set(g.loc[g["usuario_ativo"], "email"])
        in_em = set(g.loc[~g["usuario_ativo"], "email"])
        n_at = len(at_em)
        viu = por_mes.get(ult, set())
        a_viu = len(at_em & viu)
        i_viu = len(in_em & viu)
        mau_at = a_viu / n_at * 100 if n_at else None
        mau_todos = (a_viu + i_viu) / (n_at + i_viu) * 100 if (n_at + i_viu) else None
        hist = [len(at_em & por_mes[m]) / n_at * 100 if n_at and m in por_mes else None for m in meses_f[-4:-1]]
        hist = [h for h in hist if h is not None]
        base3 = float(np.mean(hist)) if hist else None
        ult_c = max([ultimo_cons.get(e) for e in at_em | in_em if e in ultimo_cons.index] or [pd.NaT])
        dias = (hoje - ult_c).days if pd.notna(ult_c) else None
        ativ = len(at_em & ja_cons) / n_at * 100 if n_at else None
        score = None
        if n_at >= 3:
            s1 = min((mau_at or 0) / 50, 1) * 40
            s2 = 20 if base3 in (None, 0) else float(np.clip(((mau_at or 0) / base3 - 0.5) / 0.5, 0, 1) * 20)
            s3 = 0 if dias is None else float(np.clip((60 - dias) / 53, 0, 1) * 20)
            s4 = (ativ or 0) / 100 * 20
            score = round(s1 + s2 + s3 + s4)
        rows.append(dict(conta=conta, nome=nome.get(conta, conta), csm=csm.get(conta) or "Sem CSM", ativos=n_at,
                         inativos_viram=i_viu, viram_ativos=a_viu, mau_ativos=mau_at, mau_todos=mau_todos,
                         mau_3m=base3, dias=dias, ativacao=ativ, score=score))
    sem_user = [nome.get(cid, cid) for cid in ativas if cid not in set(uu["conta"])]
    ex["contas"] = rows
    ex["contas_sem_usuario"] = sorted(sem_user)
    ex["ult"], ex["ant"] = ult, ant
    return ex


def reviews_com_acesso(reviews_g, reviews_v, usuarios, grupos_acesso_do_usuario):
    """Linhas compactas para o filtro de Notas no navegador:
    [mês, tipo(0 vivo/1 gravado), grupo do conteúdo, nota, aula, comentário, grupos de acesso de quem avaliou]."""
    mapa = {}
    for e, g in zip(usuarios["email"], usuarios["grupo_acesso"]):
        mapa.setdefault(e, set()).update(grupos_acesso_do_usuario(g))

    def am(s):
        s = str(s)
        m = re.match(r'(\d{4})-(\d{2})', s)
        if m:
            return m[1] + '-' + m[2]
        m = re.match(r'(\d{2})/(\d{2})/(\d{4})', s)
        return m[3] + '-' + m[2] if m else None

    out = []
    for df, t in ((reviews_v, 0), (reviews_g, 1)):
        for r in df.itertuples(index=False):
            a = am(r.data)
            if not a or pd.isna(r.nota):
                continue
            e = str(getattr(r, "email", "") or "").strip().lower()
            ga = "|".join(sorted(mapa.get(e, {"Não identificado"})))
            com = re.sub(r"\s+", " ", str(r.comentario or "")).strip()[:420]
            out.append([a, t, r.grupo, int(r.nota), str(r.curso or "").strip()[:140], com, ga])
    return out


# ---------------------------------------------------------------- montagem
def montar(d, ex, rev_rows):
    P = d["mau_pace"]
    ph = {}
    status_aba = {}
    ult, ant = ex["ult"], ex["ant"]

    # ============== números-base ==============
    mm = [r for r in d["mau_mes"] if r["mes"] < d["meta"]["mes_parcial"]][-12:]
    ativos = d["usuarios"]["ativos"]
    for r in d["mau_mes"]:
        r["pct_ativos"] = r["mau_ativos"] / ativos * 100 if ativos else None
        den = ativos + max(r["mau"] - r["mau_ativos"], 0)
        r["pct_todos"] = r["mau"] / den * 100 if den else None
    mpct = [r["pct_ativos"] for r in mm]
    D = d["dormentes"]
    nunca_pct = D["nunca_consumiram"] / D["total_usuarios_ativos"] * 100 if D["total_usuarios_ativos"] else 0
    C = pd.DataFrame(ex["contas"])
    Cv = C[C["ativos"] > 0]
    baixo = int((Cv["mau_ativos"] < 25).sum())
    alto = int((Cv["mau_ativos"] >= 50).sum())
    meio = len(Cv) - baixo - alto
    tot_c = len(Cv)

    # notas 3m vs 3m anteriores
    rv = pd.DataFrame(rev_rows, columns=["am", "t", "g", "n", "c", "com", "ga"])
    mes_rv = sorted(rv["am"].unique())
    mes_rv = [m for m in mes_rv if m < d["meta"]["mes_parcial"]]
    rec, prv = mes_rv[-3:], mes_rv[-6:-3]

    def nstat(t):
        x = rv[rv.t == t]
        a, b = x[x.am.isin(rec)].n, x[x.am.isin(prv)].n
        mens = [x[x.am == m].n.mean() if (x.am == m).sum() >= 10 else None for m in mes_rv[-9:]]
        return dict(rec=a.mean() if len(a) else None, ant=b.mean() if len(b) else None, nrec=len(a),
                    baixa=(a <= 3).mean() * 100 if len(a) else None, baixa_ant=(b <= 3).mean() * 100 if len(b) else None, mens=mens)
    AV, GV = nstat(0), nstat(1)

    # ============== PACE ==============
    di = P["dia_ref"]
    base = P["grupos"][0]
    meta = base["metas"].get(P["mes"])
    cur = P["curva"][di - 1] if di else None
    proj = base["diario"][di - 1] / cur if (di and cur) else None
    gr = [(b["grupo"], b["base_ativa"], b["metas"].get(P["mes"]), (b["diario"][di - 1] / cur) if (di and cur) else None)
          for b in P["grupos"][1:]]
    abaixo = [g for g in gr if g[2] and g[3] is not None and g[3] < g[2] and g[1] >= 30]
    st_pace = "info" if (proj is None or meta is None or di < 3) else ("ok" if proj >= meta else ("warn" if proj >= meta * 0.9 else "bad"))
    status_aba["pace"] = st_pace

    # ============== retenção M+1 (só coortes com o mês seguinte fechado) ==============
    coh = [r for r in d["cohort_retencao"] if r.get("m1") is not None and r["cohort"] < ult]
    m1 = [r["m1"] for r in coh]
    m1_last = coh[-1] if coh else None
    m1_avg = float(np.mean(m1[-4:-1])) if len(m1) >= 4 else None

    at = ex["ativacao"]
    at_m = list(at)
    at_last = at[at_m[-1]] if at_m else None
    at_prev = float(np.mean([at[m]["pct"] for m in at_m[-4:-1]])) if len(at_m) >= 2 else None

    # ============== FARÓIS DA VISÃO GERAL ==============
    F = []
    F.append(("pace", farol(st_pace, f"PACE de MAU · {ml(P['mes'])}", f"{br(proj)}%" if proj else "—",
                            f"Projeção de fechamento vs. meta de {br(meta)}%." + (f" Com só {di} dias de dado, ainda oscila." if di and di < 10 else ""),
                            seta(proj - meta if (proj and meta) else None) + " vs. meta" if proj and meta else "", "",
                            f"{len(abaixo)} de {len(gr)} grupos de acesso abaixo da meta — ver aba PACE" if abaixo else "")))
    dm = mpct[-1] - mpct[-2] if len(mpct) >= 2 else None
    st_mau = "ok" if (dm is not None and dm >= 0) else ("warn" if dm is not None and dm > -3 else "bad")
    F.append(("mau", farol(st_mau, "MAU · último mês fechado", f"{br(mpct[-1])}%",
                           f"{ni(mm[-1]['mau_ativos'])} dos {ni(ativos)} usuários ativos assistiram em {ml(mm[-1]['mes'])}.",
                           seta(dm) + f" vs. {ml(mm[-2]['mes'])}" if dm is not None else "", spark(mpct))))
    st_ct = "bad" if tot_c and baixo / tot_c >= 0.5 else ("warn" if tot_c and baixo / tot_c >= 0.3 else "ok")
    F.append(("contas", farol(st_ct, "Contas com MAU < 25%", f"{baixo} <small>de {tot_c}</small>",
                              f"{br(baixo / tot_c * 100 if tot_c else 0)}% das contas ativas (usuários ativos). Só {alto} contas têm MAU ≥ 50%.",
                              "", "", "Priorizar CS nas maiores contas desta faixa — ver aba Contas")))
    st_dm = "bad" if nunca_pct >= 20 else ("warn" if nunca_pct >= 10 else "ok")
    F.append(("dorm", farol(st_dm, "Usuários que nunca consumiram", ni(D["nunca_consumiram"]),
                            f"{br(nunca_pct)}% dos {ni(D['total_usuarios_ativos'])} usuários ativos. Outros {ni(D['sem_consumo_3m'])} estão sem consumo há +3 meses.",
                            "", "", "Lista acionável na aba Dormentes")))
    if at_last:
        dv = at_last["pct"] - at_prev if at_prev is not None else None
        st_at = "ok" if at_last["pct"] >= 50 else ("warn" if at_last["pct"] >= 35 else "bad")
        F.append(("mau", farol(st_at, "Ativação em 30 dias", f"{br(at_last['pct'])}%",
                               f"Dos {ni(at_last['n'])} usuários criados em {ml(at_m[-1])}, quantos concluíram a 1ª aula em até 30 dias. TTFV mediano: {br(d['ttfv']['mediana_dias'], 0)} dias.",
                               (seta(dv) + " vs. média anterior") if dv is not None else "", spark([at[m]["pct"] for m in at_m]))))
    if m1_last:
        dv = m1_last["m1"] - m1_avg if m1_avg is not None else None
        st_m1 = "ok" if (dv is None or dv >= 0) else ("warn" if dv > -8 else "bad")
        F.append(("mau", farol(st_m1, "Retenção M+1 (coorte)", f"{br(m1_last['m1'])}%",
                               f"Coorte {ml(m1_last['cohort'])}: % que voltou a consumir no mês seguinte." + (f" Média das 3 anteriores: {br(m1_avg)}%." if m1_avg else ""),
                               (seta(dv) + " vs. média") if dv is not None else "", spark(m1))))
    for nome, S, chave in (("Ao Vivo", AV, "notas"), ("Gravado", GV, "notas")):
        if S["rec"] is None:
            continue
        dv = S["rec"] - S["ant"] if S["ant"] else None
        st_n = "bad" if (dv is not None and dv <= -0.15) or (S["baixa"] or 0) >= 10 else (
            "warn" if (dv is not None and dv <= -0.05) or (S["baixa"] or 0) >= 5 else "ok")
        F.append((chave, farol(st_n, f"Nota {nome} · últimos 3 meses", f"{br(S['rec'], 2)} ★",
                               f"{ml(rec[0])}–{ml(rec[-1])} vs. 3 meses anteriores ({br(S['ant'], 2)}). Notas ≤ 3: {br(S['baixa_ant'])}% → {br(S['baixa'])}%.",
                               (seta(dv, 2, "") + f" · {ni(S['nrec'])} avaliações") if dv is not None else "", spark(S["mens"]))))
    farois = "".join(h for _, h in F)
    st_f = {}
    for aba, h in F:
        st_f.setdefault(aba, []).append(h.split('class="farol ')[1].split('"')[0])
    n_bad, n_warn, n_ok = farois.count("farol bad"), farois.count("farol warn"), farois.count("farol ok")

    # ============== ATENÇÃO / BEM ==============
    cg = {g: (int(_mau(d, ult, g)), int(_mau(d, ant, g))) for g in GRUPOS_CONTEUDO}
    aten, bem = [], []
    if tot_c and baixo / tot_c >= 0.3:
        aten.append(item("bad", f"<b>{baixo} contas ativas ({br(baixo / tot_c * 100)}%) com MAU abaixo de 25%</b> em {ml(ult)}. A conta para de consumir, em mediana, <b>{br(d['lead_time_churn']['mediana_dias'], 0)} dias antes</b> do churn."))
    if nunca_pct >= 10:
        aten.append(item("bad" if nunca_pct >= 20 else "warn", f"<b>{ni(D['nunca_consumiram'])} usuários ativos ({br(nunca_pct)}%) nunca assistiram nenhuma aula.</b>"))
    if abaixo and proj:
        txt = ", ".join(f"{g[0]} ({br(g[3])}% vs. meta {br(g[2])}%)" for g in sorted(abaixo, key=lambda g: g[3] / g[2]))
        aten.append(item("warn", f"<b>PACE por grupo de acesso:</b> {txt} estão abaixo da meta."))
    if AV["ant"] and AV["rec"] and AV["rec"] - AV["ant"] <= -0.05:
        aten.append(item("warn", f"<b>Nota das aulas ao vivo caindo:</b> {br(AV['ant'], 2)} → {br(AV['rec'], 2)} (3 meses vs. 3 anteriores); notas ≤ 3 em {br(AV['baixa'])}%."))
    for g, (a, b) in sorted(cg.items(), key=lambda kv: (kv[1][0] / kv[1][1]) if kv[1][1] else 1):
        if b and a / b - 1 <= -0.15:
            aten.append(item("warn", f"<b>{g} perdeu {br((1 - a / b) * 100, 0)}% dos usuários</b> em {ml(ult)} ({ni(b)} → {ni(a)})."))
        elif b and a / b - 1 >= 0.08:
            bem.append(item("ok", f"<b>{g} cresceu {br((a / b - 1) * 100, 0)}%</b> em usuários em {ml(ult)} ({ni(b)} → {ni(a)})."))
    if len(mpct) >= 3 and mpct[-1] < mpct[-2] < mpct[-3]:
        aten.append(item("warn", f"<b>MAU caiu 2 meses seguidos:</b> {br(mpct[-3])}% → {br(mpct[-2])}% → {br(mpct[-1])}%."))
    if proj and meta and proj >= meta:
        top_g = max([g for g in gr if g[1] >= 30] or gr, key=lambda g: g[3] or 0)
        bem.insert(0, item("ok", f"<b>{ml(P['mes'])} projeta {br(proj)}% de MAU</b>, acima da meta de {br(meta)}%. Melhor grupo: {top_g[0]} ({br(top_g[3])}%)."))
    if d["ttfv"]["mediana_dias"] is not None and d["ttfv"]["mediana_dias"] <= 14:
        bem.append(item("ok", f"<b>Ativação rápida:</b> mediana de {br(d['ttfv']['mediana_dias'], 0)} dias até a 1ª aula."))
    if GV["ant"] and GV["rec"] and GV["rec"] - GV["ant"] > -0.05:
        bem.append(item("ok", f"<b>Gravado estável em {br(GV['rec'], 2)} ★</b> nos últimos 3 meses."))
    ph["ATENCAO"] = "".join(aten) or item("ok", "Nenhum ponto crítico pelas regras atuais.")
    ph["BEM"] = "".join(bem) or item("info", "Nada se destacou positivamente neste mês.")
    ph["FAROIS"] = farois
    ct, us = d["contas"], d["usuarios"]
    card = lambda lab, val, sub, cls="": f'<div class="card"><div class="kpi-label">{lab}</div><div class="kpi-value {cls}">{val}</div><div class="kpi-sub">{sub}</div></div>'
    ph["BASE"] = (card("Contas ativas hoje", ni(ct["ativas"]), f"{br(ct['ativas'] / ct['total'] * 100)}% de {ni(ct['total'])} contas", "green")
                  + card("Contas inativas", ni(ct["inativas"]), "sem contrato vigente", "red")
                  + card("Usuários ativos hoje", ni(us["ativos"]), f"{br(us['ativos'] / us['total'] * 100)}% de {ni(us['total'])} usuários · ativos na Waid", "green")
                  + card("Usuários inativos", ni(us["inativos"]), "inativos na Waid", "red"))
    ph["NBAD"], ph["NWARN"], ph["NOK"] = str(n_bad), str(n_warn), str(n_ok)
    resumo = []
    if proj and meta:
        resumo.append(f"{ml(P['mes'])} {'está no ritmo da' if proj >= meta else 'está abaixo da'} meta de MAU (projeção {br(proj)}% vs. {br(meta)}%)")
    if tot_c:
        resumo.append(f"{br(baixo / tot_c * 100, 0)}% das contas ativas usam pouco (MAU &lt; 25%)")
    resumo.append(f"{br(nunca_pct, 0)}% dos usuários ativos nunca assistiram uma aula")
    ph["RESUMO"] = "; ".join(resumo) + "."

    # ============== ABA MAU & RETENÇÃO ==============
    serie = [r for r in d["mau_mes"] if r["mes"] <= d["meta"]["mes_parcial"]][-13:]
    ms = [r["mes"] for r in serie]
    dados_mau = dict(labels=[ml(m) + ("*" if m == d["meta"]["mes_parcial"] else "") for m in ms],
                     ativos=[round(r["pct_ativos"], 2) for r in serie], todos=[round(r["pct_todos"], 2) for r in serie],
                     n_ativos=[r["mau_ativos"] for r in serie], base_ativos=[ativos for _ in serie],
                     n_todos=[r["mau"] for r in serie], base_todos=[ativos + max(r["mau"] - r["mau_ativos"], 0) for r in serie],
                     parcial=ms[-1] == d["meta"]["mes_parcial"])
    graf = ('<div class="chart-wrap tall"><canvas id="mauCanvas"></canvas></div>'
            f'<script type="application/json" id="mauDados">{json.dumps(dados_mau)}</script>')
    coh_rows = ""
    offs = range(0, 7)
    for r in d["cohort_retencao"]:
        cells = ""
        for o in offs:
            v = r.get(f"m{o}")
            mes_alvo = _add_mes(r["cohort"], o)
            if v is None:
                cells += '<td class="n muted">—</td>'
            elif mes_alvo >= d["meta"]["mes_parcial"]:
                cells += f'<td class="n parcial" title="mês ainda em andamento">{br(v, 0)}%*</td>'
            else:
                alpha = min(v / 70, 1) * 0.35
                cells += f'<td class="n" style="background:rgba(30,94,255,{alpha:.2f})">{br(v, 0)}%</td>'
        coh_rows += f'<tr><td>{ml(r["cohort"])}</td><td class="n">{ni(r["tamanho"])}</td>{cells}</tr>'
    volm = pd.DataFrame(d["consumo_mes"]).groupby("mes")["aulas_assistidas"].sum()
    vol_u, vol_a = float(volm.get(ult, 0)), float(volm.get(ant, 0))
    var_vol = (vol_u / vol_a - 1) * 100 if vol_a else None
    st_vol = "info" if var_vol is None else ("ok" if var_vol >= 0 else ("warn" if var_vol > -15 else "bad"))
    vol_s = [float(volm.get(m, 0)) for m in [r["mes"] for r in mm]]
    fr = ex["frequencia"]
    fr_m = list(fr)
    cn = ex["conteudo_novo"]
    cn_m = list(cn)
    k_mau = "".join([
        farol(st_mau, f"MAU (só ativos hoje) · {ml(mm[-1]['mes'])}", f"{br(mpct[-1])}%", f"{ml(mm[-1]['mes'])}. Média 12 meses: {br(np.mean(mpct))}%.", seta(dm) + " vs. mês anterior" if dm is not None else "", spark(mpct)),
        farol("info", f"MAU (com quem saiu depois) · {ml(mm[-1]['mes'])}", f"{br(mm[-1]['pct_todos'])}%", "Inclui quem assistiu no mês e depois foi inativado na Waid. Não perde o histórico.", seta(mm[-1]['pct_todos'] - mm[-2]['pct_todos']) + " vs. mês anterior" if len(mm) >= 2 else "", spark([r["pct_todos"] for r in mm], col="var(--teal)")),
        farol("info", "Tempo até a 1ª aula (TTFV)", f"{br(d['ttfv']['mediana_dias'], 0)} dias", f"Mediana entre cadastro e 1ª aula (n={ni(d['ttfv']['amostra'])}). Média: {br(d['ttfv']['media_dias'])} dias."),
        farol(st_vol, f"Aulas concluídas · {ml(ult)}", ni(vol_u), f"Média dos últimos 12 meses fechados: {ni(d['volume_medio']['media_aulas_por_mes'])} aulas/mês.",
              seta(var_vol, 0, "%") + f" vs. {ml(ant)} ({ni(vol_a)})", spark(vol_s))])
    at_rows = "".join(f'<tr><td>{ml(m)}</td><td class="n">{ni(v["n"])}</td><td class="n"><b>{br(v["pct"])}%</b></td></tr>' for m, v in at.items())
    ph["MAU"] = (f'<div class="farois">{k_mau}</div>'
                 f'<div class="sec"><h2>MAU % mês a mês — duas leituras</h2><span class="n">azul: só quem está ativo na Waid hoje (mesma base do PACE) · verde: inclui quem assistiu e depois saiu · * mês em andamento</span></div>'
                 f'<div class="box">{graf}<p class="note">Passe o mouse sobre o mês para ver quantas pessoas assistiram. Linha tracejada = mês em andamento.</p></div>'
                 f'<div class="cols" style="margin-top:12px"><div class="box tbl"><h3>Retenção por coorte de ativação</h3><table><tr><th>Coorte</th><th class="n">Tamanho</th>{"".join(f"<th class=n>M+{o}</th>" for o in offs)}</tr>{coh_rows}</table>'
                 f'<p class="note">Coorte = mês da 1ª aula concluída. Células com * usam o mês corrente, que ainda não fechou — não compare com as outras.</p></div>'
                 f'<div class="box tbl"><h3>Ativação em 30 dias por mês de cadastro</h3><table><tr><th>Cadastro</th><th class="n">Usuários</th><th class="n">1ª aula em até 30 dias</th></tr>{at_rows}</table>'
                 f'<p class="note">Só entram meses em que todos os usuários já tiveram 30 dias completos.</p></div></div>')
    status_aba["mau"] = pior(st_f.get("mau", []) + [st_mau])

    # ============== NOTAS (dados p/ JS) ==============
    ph["REV"] = json.dumps(rev_rows, ensure_ascii=False, separators=(",", ":"))
    status_aba["notas"] = pior(st_f.get("notas", ["info"]))

    # ============== CONSUMO POR GRUPO ==============
    ph["CONSUMO"], status_aba["consumo"] = _consumo(d, ex, ult, ant)

    # ============== CONTAS ==============
    ph["CONTAS"], status_aba["contas"] = _contas(d, ex, C, ult)

    # ============== DORMENTES ==============
    ph["DORM"], ph["DLIST"], status_aba["dorm"] = _dormentes(d)

    # ============== QUALIDADE ==============
    ph["QUAL"] = _qualidade(d, ex, rev_rows)

    status_aba["geral"] = "bad" if n_bad else ("warn" if n_warn else "ok")
    for k in ("geral", "pace", "mau", "notas", "consumo", "contas", "dorm"):
        ph["SD_" + k.upper()] = status_aba.get(k, "info")
    return ph


def _add_mes(m, n):
    y, mo = int(m[:4]), int(m[5:]) - 1 + n
    y += mo // 12
    return f"{y}-{mo % 12 + 1:02d}"


def _mau(d, mes, g):
    for r in d["mau_grupo_mes"]:
        if r["mes"] == mes and r["grupo"] == g:
            return r["mau"]
    return 0


def _consumo(d, ex, ult, ant):
    meses_f = [r["mes"] for r in d["mau_mes"] if r["mes"] < d["meta"]["mes_parcial"]]
    tres = meses_f[-4:-1]
    doze = meses_f[-12:]
    cm = pd.DataFrame(d["consumo_mes"])
    au = cm.groupby(["mes", "grupo"])["aulas_assistidas"].sum()
    av = cm[cm["tipo"] == "Ao Vivo"].groupby(["mes", "grupo"])["aulas_assistidas"].sum()
    G = []
    for g in GRUPOS_CONTEUDO:
        u = lambda m: float(_mau(d, m, g))
        a = lambda m: float(au.get((m, g), 0))
        v = lambda m: float(av.get((m, g), 0))
        u3 = np.mean([u(m) for m in tres]) if tres else 0
        a3, uu3 = sum(a(m) for m in tres), sum(u(m) for m in tres)
        G.append(dict(grupo=g, usuarios=u(ult), ant=u(ant), u3=u3,
                      var_mes=(u(ult) / u(ant) - 1) * 100 if u(ant) else None, var_3m=(u(ult) / u3 - 1) * 100 if u3 else None,
                      apu=a(ult) / u(ult) if u(ult) else None, apu3=a3 / uu3 if uu3 else None,
                      vivo=v(ult) / a(ult) * 100 if a(ult) else None, vivo3=sum(v(m) for m in tres) / a3 * 100 if a3 else None,
                      serie=[u(m) for m in doze], parcial=u(d["meta"]["mes_parcial"])))
    sts = []
    cards = []
    for g in sorted(G, key=lambda g: g["var_3m"] if g["var_3m"] is not None else 0):
        st = "info" if g["var_3m"] is None else ("bad" if g["var_3m"] <= -15 else ("warn" if g["var_3m"] <= -5 else "ok"))
        sts.append(st)
        tops = ex["top_aulas"].get(g["grupo"], [])
        tl = "".join(f'<li><span class="tt">{esc(t["aula"][:70])}</span><b>{ni(t["usuarios"])}</b>{seta(t["usuarios"] - t["ant"], 0, "") if t["ant"] else "<span class=tag>nova</span>"}</li>' for t in tops)
        cards.append(
            f'<div class="farol {st}"><div class="f-top"><span class="f-label">{g["grupo"]}</span>{chip(st)}</div>'
            f'<div class="f-val">{ni(g["usuarios"])} <small>usuários em {ml(ult)}</small></div>'
            f'<div class="f-trend">{seta(g["var_mes"], 0, "%")} vs. {ml(ant)} · {seta(g["var_3m"], 0, "%")} vs. média 3m</div>'
            f'{spark(g["serie"])}'
            f'<div class="mini-kv"><span>Aulas por usuário</span><b>{br(g["apu"])}</b><span class="muted">média 3m {br(g["apu3"])}</span></div>'
            f'<div class="mini-kv"><span>% das aulas ao vivo</span><b>{br(g["vivo"], 0)}%</b><span class="muted">média 3m {br(g["vivo3"], 0)}%</span></div>'
            f'<div class="mini-kv"><span>Top aulas do mês (usuários)</span><b></b></div><ul class="toplist">{tl or "<li class=muted>—</li>"}</ul>'
            f'<div class="f-sub">{ml(d["meta"]["mes_parcial"])} até agora: {ni(g["parcial"])} usuários</div></div>')
    ins = []
    for g in sorted([g for g in G if (g["var_3m"] or 0) <= -10], key=lambda g: g["var_3m"]):
        ins.append(item("bad", f'<b>{g["grupo"]}</b> está {br(abs(g["var_3m"]), 0)}% abaixo da média dos últimos 3 meses ({ni(g["usuarios"])} vs. {ni(g["u3"])} usuários).'))
    for g in G:
        if g["apu"] and g["apu3"] and g["apu"] / g["apu3"] - 1 <= -0.1:
            ins.append(item("warn", f'<b>{g["grupo"]}</b>: cada usuário assistiu menos aulas ({br(g["apu"])} vs. {br(g["apu3"])}). Mais gente experimentando, menos profundidade.'))
    mix = sorted([g for g in G if g["vivo"] is not None and g["vivo3"] is not None and g["vivo"] - g["vivo3"] >= 10], key=lambda g: -(g["vivo"] - g["vivo3"]))[:2]
    for g in mix:
        ins.append(item("info", f'<b>{g["grupo"]}</b> (maior mudança de formato): o ao vivo passou a pesar {br(g["vivo"], 0)}% das aulas (era {br(g["vivo3"], 0)}%).'))
    for g in sorted([g for g in G if (g["var_mes"] or 0) >= 5], key=lambda g: -g["var_mes"]):
        ins.append(item("ok", f'<b>{g["grupo"]}</b> cresceu {br(g["var_mes"], 0)}% vs. {ml(ant)} ({ni(g["ant"])} → {ni(g["usuarios"])}).'))
    linha = lambda r: f'<tr><td>{esc(r["aula"][:90])}</td><td>{esc(r["grupo"])}</td><td class="n">{ni(r["b"])}</td><td class="n"><b>{ni(r["a"])}</b></td><td class="n">{seta(r["a"] - r["b"], 0, "")}</td></tr>'
    cab = f'<tr><th>Aula</th><th>Grupo</th><th class="n">{ml(ant)}</th><th class="n">{ml(ult)}</th><th class="n">Var.</th></tr>'
    cn = ex["conteudo_novo"]
    html_ = (f'<div class="sec"><h2>Leitura do mês ({ml(ult)}, último fechado)</h2><span class="n">gerada pelas regras · compara com o mês anterior e com a média dos 3 meses antes</span></div>'
             f'<div class="box"><ul class="alerts">{"".join(ins) or item("ok", "Nenhuma mudança relevante.")}</ul></div>'
             f'<div class="sec"><h2>Por grupo de conteúdo</h2><span class="n">ordenado do mais em queda para o mais em alta · linha = usuários únicos nos últimos 12 meses</span></div>'
             f'<div class="farois">{"".join(cards)}</div>'
             f'<p class="note">Status: ▼ crítico se os usuários do mês estão 15%+ abaixo da média de 3 meses; ● atenção se 5–15% abaixo. “Aulas por usuário” mede profundidade.</p>'
             f'<div class="cols" style="margin-top:12px"><div class="box tbl"><h3>{chip("ok", "Em alta")} Aulas que mais ganharam usuários</h3><table>{cab}{"".join(linha(r) for r in ex["alta"]) or "<tr><td class=muted>—</td></tr>"}</table></div>'
             f'<div class="box tbl"><h3>{chip("bad", "Em queda")} Aulas que mais perderam usuários</h3><table>{cab}{"".join(linha(r) for r in ex["queda"]) or "<tr><td class=muted>—</td></tr>"}</table></div></div>'
             '')
    return html_, pior(sts)


def _contas(d, ex, C, ult):
    Cv = C[C["ativos"] > 0].copy()
    tot = len(Cv)
    f50 = int((Cv["mau_ativos"] >= 50).sum())
    f25 = int((Cv["mau_ativos"] < 25).sum())
    fm = tot - f50 - f25
    zero = int((Cv["viram_ativos"] == 0).sum())
    em_risco = int(Cv.loc[Cv["mau_ativos"] < 25, "ativos"].sum())
    tot_u = int(Cv["ativos"].sum())
    csm = []
    for c, s in Cv.groupby("csm"):
        a = s["ativos"].sum()
        csm.append(dict(csm=c, contas=len(s), ativos=int(a), mau_at=s["viram_ativos"].sum() / a * 100 if a else 0,
                        mau_todos=(s["viram_ativos"].sum() + s["inativos_viram"].sum()) / (a + s["inativos_viram"].sum()) * 100 if a else 0,
                        baixo=int((s["mau_ativos"] < 25).sum()), pct_baixo=(s["mau_ativos"] < 25).mean() * 100,
                        risco=int(s.loc[s["mau_ativos"] < 25, "ativos"].sum()),
                        score=s["score"].dropna().mean() if s["score"].notna().any() else None))
    csm.sort(key=lambda x: -x["risco"])
    rows = "".join(
        f'<tr><td>{esc(c["csm"])}</td><td class="n">{c["contas"]}</td><td class="n">{ni(c["ativos"])}</td><td class="n"><b>{br(c["mau_at"])}%</b></td>'
        f'<td class="n">{br(c["mau_todos"])}%</td><td class="n">{c["baixo"]} <span class="muted">({br(c["pct_baixo"], 0)}%)</span></td><td class="n">{ni(c["risco"])}</td>'
        f'<td class="n">{br(c["score"], 0)}</td><td>{chip("bad" if c["pct_baixo"] >= 60 else ("warn" if c["pct_baixo"] >= 45 else "ok"))}</td></tr>' for c in csm)
    sc = Cv.dropna(subset=["score"])
    saud = int((sc["score"] >= 70).sum())
    aten = int(((sc["score"] >= 40) & (sc["score"] < 70)).sum())
    risc = int((sc["score"] < 40).sum())
    piores = sc.sort_values(["score", "ativos"], ascending=[True, False])
    pr = "".join(
        f'<tr><td>{esc(r.nome)}</td><td>{esc(r.csm)}</td><td class="n">{ni(r.ativos)}</td><td class="n">{br(r.mau_ativos)}%</td>'
        f'<td class="n">{br(r.mau_3m)}%</td><td class="n">{"—" if r.dias is None or pd.isna(r.dias) else ni(r.dias)}</td><td class="n">{br(r.ativacao, 0)}%</td>'
        f'<td class="n"><b class="{"down" if r.score < 40 else ("up" if r.score >= 70 else "")}">{ni(r.score)}</b></td></tr>' for r in piores.itertuples())
    caiu = sc[(sc["mau_3m"].notna()) & (sc["ativos"] >= 5)].assign(d=lambda t: t["mau_ativos"] - t["mau_3m"]).sort_values("d").head(10)
    cr = "".join(f'<tr><td>{esc(r.nome)}</td><td>{esc(r.csm)}</td><td class="n">{ni(r.ativos)}</td><td class="n">{br(r.mau_3m)}%</td><td class="n"><b>{br(r.mau_ativos)}%</b></td><td class="n">{seta(r.d, 0)}</td></tr>' for r in caiu.itertuples() if r.d < 0)
    ref = Cv[Cv["ativos"] >= 10].sort_values("mau_ativos", ascending=False).head(8)
    rf = "".join(f'<tr><td>{esc(r.nome)}</td><td>{esc(r.csm)}</td><td class="n">{ni(r.ativos)}</td><td class="n"><b class="up">{br(r.mau_ativos)}%</b></td></tr>' for r in ref.itertuples())
    bar = lambda n, col: f'<div style="flex:{max(n, 0.001)};background:{col}" title="{n}"></div>'
    pior_c = csm[0] if csm else None
    st = "bad" if tot and f25 / tot >= 0.5 else ("warn" if tot and f25 / tot >= 0.3 else "ok")
    html_ = (
        '<div class="farois">'
        + farol(st, "Contas com MAU < 25%", f"{f25} <small>de {tot}</small>", f"{br(f25 / tot * 100 if tot else 0)}% das contas ativas em {ml(ult)} (usuários ativos).")
        + farol("bad" if tot_u and em_risco / tot_u >= 0.4 else "warn", "Usuários em contas de baixo uso", ni(em_risco),
                f"{br(em_risco / tot_u * 100 if tot_u else 0, 0)}% dos {ni(tot_u)} usuários ativos das contas ativas.", "", "",
                f"Maior carteira em risco: {esc(pior_c['csm'])} ({ni(pior_c['risco'])} usuários)" if pior_c else "")
        + farol("warn" if zero else "ok", "Contas sem ninguém assistindo", str(zero), f"Contas ativas com usuários ativos e zero consumo em {ml(ult)}.")
        + farol("bad" if risc > saud else "warn", "Saúde da conta", f"{risc} <small>em risco</small>",
                f"Nota de 0 a 100 (contas com 3+ ativos): {saud} saudáveis (≥ 70), {aten} em atenção, {risc} em risco (&lt; 40).")
        + '</div>'
        f'<div class="box" style="margin-top:12px"><div class="stack">{bar(f50, "var(--ok)")}{bar(fm, "var(--warn)")}{bar(f25, "var(--bad)")}</div>'
        f'<div class="legend"><span><i style="background:var(--ok)"></i>MAU ≥ 50%: {f50}</span><span><i style="background:var(--warn)"></i>25–49%: {fm}</span><span><i style="background:var(--bad)"></i>&lt; 25%: {f25}</span></div></div>'
        '<div class="sec"><h2>Saúde da carteira por CSM</h2><span class="n">ordenado pelos usuários em risco</span></div>'
        '<div class="box tbl"><table><tr><th>CSM</th><th class="n">Contas</th><th class="n">Usuários ativos</th><th class="n">MAU (ativos)</th><th class="n">MAU (com quem saiu)</th>'
        f'<th class="n">Contas &lt; 25%</th><th class="n">Usuários em risco</th><th class="n">Saúde média</th><th>Status</th></tr>{rows}</table>'
        f'<p class="note"><b>MAU (ativos)</b> = usuários ativos na Waid hoje que concluíram 1+ aula em {ml(ult)} ÷ usuários ativos hoje — mesma base do PACE. '
        '<b>MAU (com quem saiu)</b> soma no numerador e no denominador quem assistiu no mês e depois foi inativado, para o histórico não cair só porque a base foi limpa.</p></div>'
        '<div class="sec"><h2>Saúde da conta — todas as contas</h2><span class="n">MAU 40 pts · tendência vs. 3 meses 20 pts · dias desde o último consumo 20 pts · % de ativos que já assistiram 20 pts</span></div>'
        f'<div class="filters"><label for="sBusca">Buscar<input id="sBusca" type="search" placeholder="Conta ou CSM"></label><span class="hint">{len(sc)} contas com 3+ usuários ativos · da pior para a melhor nota</span></div>'
        '<div class="box tbl list-scroll"><table id="sTab"><tr><th>Conta</th><th>CSM</th><th class="n">Ativos</th><th class="n">MAU</th><th class="n">MAU 3m</th><th class="n">Dias sem consumo</th><th class="n">Já assistiram</th><th class="n">Nota</th></tr>'
        f'{pr}</table><p class="note">Hoje uma conta para de consumir, em mediana, {br(d["lead_time_churn"]["mediana_dias"], 0)} dias antes do churn — a nota antecipa esse sinal.</p></div>'
        f'<div class="cols" style="margin-top:12px"><div class="box tbl"><h3>{chip("bad", "Piorou")} Maiores quedas vs. média de 3 meses</h3><table><tr><th>Conta</th><th>CSM</th><th class="n">Ativos</th><th class="n">MAU 3m</th><th class="n">MAU</th><th class="n">Var.</th></tr>{cr or "<tr><td class=muted>—</td></tr>"}</table></div>'
        f'<div class="box tbl"><h3>{chip("ok", "Referência")} Contas com 10+ ativos e maior MAU</h3><table><tr><th>Conta</th><th>CSM</th><th class="n">Ativos</th><th class="n">MAU</th></tr>{rf}</table></div></div>')
    return html_, st


def _dormentes(d):
    L = d["dormentes_listas"]
    lst = []
    for r in L["nunca"]:
        lst.append([r["nome_usuario"] or "—", r["email"], r["nome_conta"] or "—", r["csm"] or "—", None, "Nunca assistiu"])
    for r in L["tres_meses"]:
        dd = r["dias_desde_ultimo"]
        f = "3–6 meses" if dd <= 182 else ("6–12 meses" if dd <= 365 else "+12 meses")
        lst.append([r["nome_usuario"] or "—", r["email"], r["nome_conta"] or "—", r["csm"] or "—", dd, f])
    df = pd.DataFrame(lst, columns=["nome", "email", "conta", "csm", "dias", "sit"])
    fx = df["sit"].value_counts()
    ativos = d["dormentes"]["total_usuarios_ativos"]
    nunca = int(fx.get("Nunca assistiu", 0))
    porc = df.pivot_table(index="csm", columns="sit", values="email", aggfunc="count", fill_value=0)
    for col in ["Nunca assistiu", "3–6 meses", "6–12 meses", "+12 meses"]:
        if col not in porc:
            porc[col] = 0
    porc["total"] = porc[["Nunca assistiu", "3–6 meses", "6–12 meses", "+12 meses"]].sum(axis=1)
    porc = porc.sort_values("total", ascending=False)
    drow = "".join(f'<tr><td>{esc(i)}</td><td class="n">{ni(r["Nunca assistiu"])}</td><td class="n">{ni(r["3–6 meses"])}</td><td class="n">{ni(r["6–12 meses"] + r["+12 meses"])}</td><td class="n"><b>{ni(r.total)}</b></td></tr>' for i, r in porc.iterrows())
    tot_conta = {r["nome_conta"]: r["total_usuarios"] for r in d["empresas_ativas"]}
    pc = df.groupby("conta")["sit"].agg(total="count", nunca=lambda s: (s == "Nunca assistiu").sum()).sort_values("total", ascending=False).head(12)
    crow = "".join(f'<tr><td>{esc(i)}</td><td class="n">{ni(r.nunca)}</td><td class="n">{ni(r.total - r.nunca)}</td><td class="n"><b>{ni(r.total)}</b></td><td class="n">{(br(r.total / tot_conta[i] * 100, 0) + "%") if tot_conta.get(i) else "—"}</td></tr>' for i, r in pc.iterrows())
    st = "bad" if ativos and nunca / ativos >= 0.2 else "warn"
    html_ = (
        '<div class="farois">'
        + farol(st, "Nunca assistiram", ni(nunca), f"{br(nunca / ativos * 100 if ativos else 0)}% dos usuários ativos. Foco: ativação (1ª aula).", "", "", "Régua de boas-vindas com aula curta recomendada pelo grupo de acesso")
        + farol("warn", "Pararam há 3–6 meses", ni(fx.get("3–6 meses", 0)), "Ainda recuperáveis: já viram valor e pararam há pouco.", "", "", "Reengajar com a aula mais bem avaliada do grupo deles")
        + farol("info", "Pararam há 6–12 meses", ni(fx.get("6–12 meses", 0)), "Recuperação mais difícil; cruzar com a renovação da conta.")
        + farol("info", "Parados há mais de 12 meses", ni(fx.get("+12 meses", 0)), "Candidatos a inativar na Waid (limpa a base e o MAU).")
        + '</div><div class="cols" style="margin-top:12px">'
        f'<div class="box tbl"><h3>Por CSM</h3><table><tr><th>CSM</th><th class="n">Nunca</th><th class="n">3–6m</th><th class="n">+6m</th><th class="n">Total</th></tr>{drow}</table></div>'
        f'<div class="box tbl"><h3>Contas com mais dormentes</h3><table><tr><th>Conta</th><th class="n">Nunca</th><th class="n">Parados</th><th class="n">Total</th><th class="n">% da conta</th></tr>{crow}</table>'
        '<p class="note">“—” = conta sem contrato ativo hoje (ex.: B2C, parcerias), sem base na aba Contas.</p></div></div>'
        '<div class="sec"><h2>Lista para o plano de ação</h2><span class="n">usuários ativos na Waid sem consumo · filtre e baixe o CSV para o CS</span></div>'
        '<div class="filters"><label for="dBusca">Buscar<input id="dBusca" type="search" placeholder="Nome, e-mail ou conta"></label>'
        '<label for="dSit">Situação<select id="dSit"><option value="">Todas</option><option>Nunca assistiu</option><option>3–6 meses</option><option>6–12 meses</option><option>+12 meses</option></select></label>'
        '<label for="dCsm">CSM<select id="dCsm"><option value="">Todos</option></select></label>'
        '<label for="dConta">Conta<select id="dConta"><option value="">Todas</option></select></label>'
        '<button class="btn ghost" id="dCsv" type="button">⬇ Baixar CSV</button><span class="hint" id="dCount"></span></div>'
        '<div class="box tbl list-scroll"><table id="dTab"></table></div>'
        '<p class="note">Mostra até 300 linhas na tela; o CSV leva todas as linhas do filtro.</p>')
    return html_, json.dumps(lst, ensure_ascii=False, separators=(",", ":")), st


def _qualidade(d, ex, rev_rows):
    SG = d["sem_grupo_tabela"]
    revsg = {}
    for r in rev_rows:
        if r[2] == "Sem Grupo Identificado":
            revsg[r[4]] = revsg.get(r[4], 0) + 1
    revsg = sorted(revsg.items(), key=lambda x: -x[1])
    nd = dict(revsg).get("N/D", 0)
    semuser = ex["contas_sem_usuario"]
    sem_ident = sum(1 for r in rev_rows if r[6] == "Não identificado")
    sg_rows = "".join(f'<tr><td>{esc(r["Conteúdo"])}</td><td class="n">{ni(r["ocorrencias"])}</td></tr>' for r in SG)
    rv_rows = "".join(f'<tr><td>{esc(t)}</td><td class="n">{v}</td></tr>' for t, v in revsg[:80])
    su_rows = "".join(f"<li>{esc(n)}</li>" for n in semuser) or "<li class=muted>Nenhuma.</li>"
    return (
        '<div class="headline"><div class="big"><b>Para que serve:</b> tudo o que o painel não conseguiu encaixar. Cada item corrigido na origem (Biblioteca PAI, Supabase, Waid ou Zoom) melhora os números das outras abas na próxima atualização.</div></div>'
        '<div class="farois">'
        + farol("warn" if SG else "ok", "Aulas sem grupo de conteúdo", str(len(SG)),
                f'{ni(sum(r["ocorrencias"] for r in SG))} conclusões caem em “Sem Grupo Identificado”.', "", "", "Deixar o título na Biblioteca PAI idêntico ao da Waid")
        + farol("warn" if revsg else "ok", "Avaliações sem grupo", ni(sum(v for _, v in revsg)),
                f"{ni(nd)} vêm do Zoom sem o tópico da reunião (N/D); as demais têm título que não bate com a Biblioteca PAI.", "", "",
                "Preencher o tópico da reunião no Zoom e cadastrar o título na Biblioteca PAI")
        + farol("warn" if sem_ident else "ok", "Avaliações sem grupo de acesso", ni(sem_ident),
                "O e-mail de quem avaliou não está na tabela usuarios — não entram no filtro por grupo de acesso.", "", "", "Conferir se são convidados, e-mails pessoais ou usuários faltando no Supabase")
        + farol("warn" if semuser else "ok", "Contas ativas sem usuário", str(len(semuser)),
                "Contrato vigente e nenhum usuário cadastrado: usuário em outra conta ou conta não implantada.", "", "", "Conferir o id_conta dos usuários dessas empresas")
        + '</div><div class="cols" style="margin-top:12px">'
        '<div class="box tbl"><h3>Aulas sem grupo (consumo)</h3><div class="filters" style="border:0;padding:0;margin-bottom:8px"><label for="qBusca">Buscar<input id="qBusca" type="search" placeholder="Título da aula"></label></div>'
        f'<div class="list-scroll"><table id="qTab"><tr><th>Título como veio da Waid</th><th class="n">Conclusões</th></tr>{sg_rows}</table></div></div>'
        f'<div class="box tbl"><h3>Avaliações sem grupo</h3><div class="list-scroll"><table><tr><th>Título da aula avaliada</th><th class="n">Avaliações</th></tr>{rv_rows}</table></div>'
        f'<h3 style="margin-top:14px">Contas ativas sem usuário</h3><ul class="list">{su_rows}</ul></div></div>')
