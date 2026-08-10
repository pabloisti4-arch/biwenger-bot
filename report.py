"""Estructura del reporte diario y renderizado a texto y HTML.

Cada tipo de oportunidad define sus "hechos" UNA vez (funciones _facts_*), y
los dos renderizadores los consumen. Asi el email HTML y el texto plano no
pueden divergir: si se anade un dato, aparece en los dos.
"""

import html
from dataclasses import dataclass, field
from datetime import datetime

from scoring import euros

BAR_WIDTH = 10

ADVICE_TAGS = {
    "exacto": "[EXACTO]",
    "estimado": "[ESTIMADO]",
    "recomendado": "[RECOMENDADO]",
}

LEGEND = [
    ("[EXACTO]", "cifra fija leida de la API. Es lo que cuesta la clausula, "
                 "sin regateo posible."),
    ("[ESTIMADO]", "clausula calculada en local (1,5 x valor), NO confirmada. "
                   "Confirmala con probe_clause.py antes de contar con ella."),
    ("[RECOMENDADO]", "rango sugerido para una puja de mercado. Es una subasta "
                      "a ciegas: no se ven las pujas rivales. El rango se ajusta "
                      "a medida que el histórico acumula dias."),
]


def bar(value):
    filled = int(round(max(0.0, min(1.0, value)) * BAR_WIDTH))
    return "#" * filled + "." * (BAR_WIDTH - filled)


@dataclass
class Section:
    key: str
    title: str
    opportunities: list
    empty_note: str = "sin candidatos hoy"
    note: str = ""


@dataclass
class Report:
    generated_at: datetime
    league_id: str
    round_label: str
    balance: int
    max_bid: int
    history_span: str = "sin fotos"
    sections: list = field(default_factory=list)
    caveats: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def section(self, key):
        for s in self.sections:
            if s.key == key:
                return s
        return None


# -- hechos por tipo de tarjeta (fuente unica) --------------------------------

def _facts_chollo(op):
    return {
        "flag": op.advice.flag if op.advice else "",
        "details": [
            f"valor {euros(op.value)}  ·  piden {euros(op.cost)}",
            (
                f"mejora de plantilla {op.improvement:+.3f} "
                f"(sustituiria a {op.extra['sustituiria_a']})"
                if op.improvement is not None else None
            ),
            f"vendedor: {op.extra['vendedor']}",
        ],
        "score_label": "puntuacion",
    }


def _facts_venta(op):
    return {
        "flag": "",
        "details": [
            f"valor actual {euros(op.value)}  ·  estado: {op.extra['status']}",
            f"tendencia: {op.extra['tendencia']}",
            f"tu clausula estimada: {euros(op.extra['clausula_propia'])}",
            (
                None if op.extra["forma_fiable"] else
                "sin histórico suficiente para saber si esta en pico: no se "
                "recomienda vender por tendencia"
            ),
        ],
        "score_label": "urgencia de venta",
    }


def _facts_especulacion(op):
    marca = "" if op.extra["forma_fiable"] else ", SIN histórico fiable"
    return {
        "flag": op.advice.flag if op.advice else "",
        "details": [
            f"valor {euros(op.value)}  ·  piden {euros(op.cost)}",
            f"tendencia: {op.extra['tendencia']}",
            f"proyeccion lineal a {op.extra['horizonte_dias']} dias: "
            f"{euros(op.extra['proyeccion_lineal'])} "
            f"(cota optimista, no pronostico{marca})",
        ],
        "score_label": "puntuacion",
    }


def _facts_clausulazo(op):
    return {
        "flag": op.advice.flag if op.advice else "",
        "details": [
            f"valor {euros(op.value)}",
            (
                f"mejora de plantilla {op.improvement:+.3f} "
                f"(sustituiria a {op.extra['sustituiria_a']})"
                if op.improvement is not None else None
            ),
            (
                f"retorno: {op.roi:+.3f} de mejora por millon"
                if op.roi is not None else None
            ),
        ],
        "score_label": "puntuacion",
    }


def _facts_clausulazo_impacto(op):
    return {
        "flag": op.advice.flag if op.advice else "",
        "details": [
            (
                f"MEJORA DE PLANTILLA {op.improvement:+.3f} "
                f"(sustituiria a {op.extra['sustituiria_a']})"
                if op.improvement is not None else None
            ),
            f"valor {euros(op.value)}",
            (
                f"eficiencia (secundaria): {op.roi:+.3f} de mejora por millon"
                if op.roi is not None else None
            ),
        ],
        "score_label": "puntuacion",
    }


FACTS = {
    "chollo": _facts_chollo,
    "venta": _facts_venta,
    "especulacion": _facts_especulacion,
    "clausulazo": _facts_clausulazo,
    "clausulazo_impacto": _facts_clausulazo_impacto,
}


def facts_for(section_key, op):
    builder = FACTS.get(section_key, _facts_chollo)
    data = builder(op)
    data["details"] = [d for d in data["details"] if d]
    return data


# -- renderizado a texto -----------------------------------------------------

def _advice_lines_text(advice, indent="    "):
    if advice is None:
        return
    yield f"{indent}{ADVICE_TAGS.get(advice.kind, '[?]')} {advice.headline}"
    for reason in advice.rationale:
        yield f"{indent}  - {reason}"
    if advice.warning:
        yield f"{indent}  ! {advice.warning}"


def _render_op_text(section_key, op):
    data = facts_for(section_key, op)
    header = f"  {op.name} ({op.position}, {op.team})"
    if data["flag"]:
        header += f"  [{data['flag']}]"
    yield header
    for detail in data["details"]:
        yield f"    {detail}"
    yield from _advice_lines_text(op.advice)
    yield (
        f"    {data['score_label']} {op.score.final:.3f} [{bar(op.score.final)}]  "
        f"confianza {op.score.confidence:.0%}"
    )
    yield f"    por que: {op.score.why()}"


def render_text(report, width=92):
    lines = []
    rule = "=" * width

    lines.append(rule)
    lines.append("REPORTE BIWENGER - MODO PROPUESTA")
    lines.append(rule)
    lines.append(f"  generado: {report.generated_at.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  liga: {report.league_id}   {report.round_label}")
    lines.append(f"  saldo: {euros(report.balance)}   puja maxima: {euros(report.max_bid)}")
    lines.append(f"  histórico de precios: {report.history_span}")
    lines.append("  NO se ha ejecutado ninguna accion. Todo lo de abajo es una propuesta.")
    lines.append("")
    lines.append("COMO LEER LOS PRECIOS:")
    for tag, meaning in LEGEND:
        lines.append(f"  {tag:<16}{meaning}")
    lines.append("")

    for index, section in enumerate(report.sections, start=1):
        lines.append("-" * width)
        lines.append(f"[{index}] {section.title}  ({len(section.opportunities)})")
        if section.note:
            lines.append(f"    {section.note}")
        lines.append("-" * width)
        if not section.opportunities:
            lines.append(f"  {section.empty_note}")
        else:
            for op in section.opportunities:
                lines.extend(_render_op_text(section.key, op))
                lines.append("")
        lines.append("")

    lines.append(rule)
    lines.append("FIABILIDAD DE LOS DATOS")
    lines.append(rule)
    for caveat in report.caveats:
        lines.append(f"  - {caveat}")

    if report.stats:
        lines.append("")
        lines.append("CONSUMO DE API")
        for key, value in report.stats.items():
            lines.append(f"  {key}: {value}")

    lines.append("")
    lines.append(rule)
    lines.append("Modo propuesta: este bot solo lee. Las decisiones y su ejecucion")
    lines.append("son tuyas, en la app de Biwenger.")
    lines.append(rule)
    return "\n".join(lines)


# -- renderizado a HTML ------------------------------------------------------

# Estilos en linea: los clientes de correo ignoran hojas de estilo con
# frecuencia. Cada insignia lleva SIEMPRE su texto, nunca solo color, para
# que se entienda sin depender de la vista ni de la percepcion del color.
C_TEXT = "#1a1a1a"
C_MUTED = "#5b5b5b"
C_BORDER = "#dcdcdc"
C_BG_SOFT = "#f6f6f6"

BADGE_STYLES = {
    "EXACTO": ("#0f3d24", "#d6f0e0"),
    "ESTIMADO": ("#7a4a06", "#fdeccb"),
    "RECOMENDADO": ("#123a6b", "#d8e6f7"),
    "PAGABLE": ("#0f3d24", "#d6f0e0"),
    "FUERA DE PRESUPUESTO": ("#7a1414", "#f7dada"),
    "CHOLLO": ("#0f3d24", "#d6f0e0"),
    "INFLADO": ("#7a4a06", "#fdeccb"),
    "A PRECIO DE MERCADO": ("#3a3a3a", "#e8e8e8"),
}


def _esc(value):
    return html.escape(str(value), quote=True)


def _badge(label):
    fg, bg = BADGE_STYLES.get(label, ("#3a3a3a", "#e8e8e8"))
    return (
        f'<span style="display:inline-block;padding:2px 7px;border-radius:3px;'
        f'font-size:11px;font-weight:700;letter-spacing:.03em;color:{fg};'
        f'background:{bg}">{_esc(label)}</span>'
    )


def _score_html(label, score):
    pct = max(0.0, min(1.0, score.final)) * 100
    return (
        f'<div style="margin-top:8px">'
        f'<div style="font-size:12px;color:{C_MUTED}">'
        f'{_esc(label)} <strong style="color:{C_TEXT}">{score.final:.3f}</strong>'
        f' · confianza {score.confidence:.0%}</div>'
        f'<div style="height:6px;background:{C_BORDER};border-radius:3px;'
        f'margin-top:3px;max-width:260px">'
        f'<div style="height:6px;width:{pct:.0f}%;background:#2f6f4f;'
        f'border-radius:3px"></div></div>'
        f'</div>'
    )


def _advice_html(advice):
    if advice is None:
        return ""
    tag = ADVICE_TAGS.get(advice.kind, "[?]").strip("[]")
    reasons = "".join(
        f'<li style="margin:2px 0">{_esc(r)}</li>' for r in advice.rationale
    )
    warning = ""
    if advice.warning:
        warning = (
            f'<div style="margin-top:6px;padding:6px 8px;background:#fdeccb;'
            f'color:#7a4a06;border-radius:3px;font-size:12px">'
            f'<strong>Aviso:</strong> {_esc(advice.warning)}</div>'
        )
    return (
        f'<div style="margin-top:8px;padding:8px 10px;background:{C_BG_SOFT};'
        f'border-left:3px solid {C_BORDER};border-radius:3px">'
        f'<div style="font-size:14px;font-weight:700;color:{C_TEXT}">'
        f'{_badge(tag)} {_esc(advice.headline)}</div>'
        f'<ul style="margin:6px 0 0 18px;padding:0;font-size:12px;'
        f'color:{C_MUTED}">{reasons}</ul>{warning}</div>'
    )


def _render_op_html(section_key, op):
    data = facts_for(section_key, op)
    badge = f" {_badge(data['flag'])}" if data["flag"] else ""
    details = "".join(
        f'<div style="font-size:13px;color:{C_TEXT};margin:3px 0">{_esc(d)}</div>'
        for d in data["details"]
    )
    return (
        f'<div style="border:1px solid {C_BORDER};border-radius:6px;'
        f'padding:12px 14px;margin:0 0 12px 0">'
        f'<div style="font-size:15px;font-weight:700;color:{C_TEXT}">'
        f'{_esc(op.name)}'
        f'<span style="font-weight:400;color:{C_MUTED};font-size:13px"> '
        f'({_esc(op.position)}, {_esc(op.team)})</span>{badge}</div>'
        f'{details}'
        f'{_advice_html(op.advice)}'
        f'{_score_html(data["score_label"], op.score)}'
        f'<div style="margin-top:6px;font-size:11px;color:{C_MUTED};'
        f'line-height:1.5">{_esc(op.score.why())}</div>'
        f'</div>'
    )


def render_html(report):
    parts = []
    add = parts.append

    add(
        '<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Reporte Biwenger</title></head>"
        f'<body style="margin:0;padding:0;background:#eeeeee">'
        f'<div style="max-width:760px;margin:0 auto;padding:16px;'
        f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        f'color:{C_TEXT};background:#ffffff">'
    )

    # Cabecera
    add(
        f'<h1 style="margin:0 0 4px 0;font-size:20px">Reporte Biwenger</h1>'
        f'<div style="font-size:13px;color:{C_MUTED}">'
        f'{_esc(report.generated_at.strftime("%Y-%m-%d %H:%M"))} · '
        f'liga {_esc(report.league_id)} · {_esc(report.round_label)}</div>'
        f'<div style="font-size:13px;margin-top:6px">'
        f'saldo <strong>{euros(report.balance)}</strong> · '
        f'puja maxima <strong>{euros(report.max_bid)}</strong> · '
        f'histórico {_esc(report.history_span)}</div>'
    )
    add(
        '<div style="margin:12px 0;padding:10px 12px;background:#d6f0e0;'
        'color:#0f3d24;border-radius:4px;font-size:13px;font-weight:700">'
        "MODO PROPUESTA: no se ha ejecutado ninguna accion. Todo lo de abajo "
        "es informacion para que decidas tu en la app de Biwenger."
        "</div>"
    )

    # Leyenda
    legend_items = "".join(
        f'<li style="margin:4px 0">{_badge(tag.strip("[]"))} {_esc(meaning)}</li>'
        for tag, meaning in LEGEND
    )
    add(
        f'<div style="margin:12px 0;padding:10px 12px;background:{C_BG_SOFT};'
        f'border-radius:4px"><div style="font-size:12px;font-weight:700">'
        f"COMO LEER LOS PRECIOS</div>"
        f'<ul style="margin:6px 0 0 18px;padding:0;font-size:12px;color:{C_MUTED}">'
        f"{legend_items}</ul></div>"
    )

    # Secciones
    for index, section in enumerate(report.sections, start=1):
        add(
            f'<h2 style="margin:22px 0 2px 0;font-size:15px;'
            f'border-bottom:2px solid {C_BORDER};padding-bottom:5px">'
            f"{index}. {_esc(section.title)} "
            f'<span style="color:{C_MUTED};font-weight:400">'
            f"({len(section.opportunities)})</span></h2>"
        )
        if section.note:
            add(
                f'<div style="font-size:12px;color:{C_MUTED};margin:6px 0 10px 0">'
                f"{_esc(section.note)}</div>"
            )
        if not section.opportunities:
            add(
                f'<div style="font-size:13px;color:{C_MUTED};padding:10px 0">'
                f"{_esc(section.empty_note)}</div>"
            )
        else:
            for op in section.opportunities:
                add(_render_op_html(section.key, op))

    # Advertencias
    caveats = "".join(
        f'<li style="margin:5px 0">{_esc(c)}</li>' for c in report.caveats
    )
    add(
        f'<h2 style="margin:22px 0 6px 0;font-size:15px;'
        f'border-bottom:2px solid {C_BORDER};padding-bottom:5px">'
        f"Fiabilidad de los datos</h2>"
        f'<ul style="margin:0 0 0 18px;padding:0;font-size:12px;color:{C_MUTED};'
        f'line-height:1.6">{caveats}</ul>'
    )

    if report.stats:
        rows = "".join(
            f'<tr><td style="padding:3px 10px 3px 0;color:{C_MUTED}">'
            f"{_esc(k)}</td><td style=\"padding:3px 0\">{_esc(v)}</td></tr>"
            for k, v in report.stats.items()
        )
        add(
            f'<h2 style="margin:22px 0 6px 0;font-size:15px;'
            f'border-bottom:2px solid {C_BORDER};padding-bottom:5px">'
            f"Consumo de API</h2>"
            f'<table style="font-size:12px;border-collapse:collapse">{rows}</table>'
        )

    add(
        f'<div style="margin-top:24px;padding-top:12px;'
        f'border-top:1px solid {C_BORDER};font-size:11px;color:{C_MUTED}">'
        "Modo propuesta: este bot solo lee la API de Biwenger. Las decisiones y "
        "su ejecucion son tuyas. Este correo no contiene ningun enlace de accion."
        "</div>"
    )
    add("</div></body></html>")
    return "".join(parts)
