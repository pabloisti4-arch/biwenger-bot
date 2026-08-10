"""Recomendacion de cuanto pujar.

Dos casos que NO se deben mezclar:

  EXACTO       clausulazo. El precio es fijo y lo hemos leido de la API.
               No hay subasta ni margen: se paga esa cifra o no se ficha.

  RECOMENDADO  puja de mercado. Es una subasta a ciegas: no se ven las pujas
               rivales. Cualquier cifra es una recomendacion con incertidumbre
               irreducible, y se da como RANGO, nunca como numero unico.

La amplitud y el centro del rango dependen de la calidad de la tendencia, asi
que el rango se afina solo a medida que el histórico acumula dias.
"""

from dataclasses import dataclass, field

from scoring import POSITIONS, euros

# Prima maxima sobre lo que piden, por muy atractivo que sea el jugador.
MAX_PREMIUM = 0.30
# Prima base minima: en una subasta, igualar la cifra pedida casi nunca gana.
BASE_PREMIUM = 0.05
# Umbrales para clasificar el precio pedido frente al valor de mercado.
CHOLLO_THRESHOLD = 0.95
INFLADO_THRESHOLD = 1.05
# Fraccion del saldo a partir de la cual se avisa de concentracion de riesgo.
BUDGET_WARN_FRACTION = 0.60

STEP = 10_000


def _round_to(value, step=STEP):
    return int(round(value / step) * step)


@dataclass
class BidAdvice:
    kind: str                 # "exacto" | "estimado" | "recomendado"
    flag: str
    exact: int | None = None
    low: int | None = None
    high: int | None = None
    rationale: list = field(default_factory=list)
    warning: str = ""

    @property
    def headline(self):
        if self.kind == "exacto":
            return f"PRECIO EXACTO {euros(self.exact)}"
        if self.kind == "estimado":
            return f"PRECIO ESTIMADO {euros(self.exact)} - SIN CONFIRMAR"
        return f"PUJA RECOMENDADA {euros(self.low)} - {euros(self.high)}"


def clause_advice(clause, balance, origin, estimated=None):
    """Clausulazo: cifra fija, cero regateo.

    Si la cifra no vino de la API, la etiqueta NO puede decir "exacto": seria
    presentar una estimacion con la autoridad de un dato leido.
    """
    affordable = clause <= balance
    confirmed = origin != "estimada"
    rationale = [
        "precio fijo de clausula: no es una subasta, no hay rango que elegir",
    ]
    if confirmed:
        rationale.append(f"cifra leida de la API ({origin})")
    else:
        rationale.append(
            "cifra ESTIMADA (1,5 x valor), NO leida de la API. Confirmala con "
            "py probe_clause.py <id> antes de actuar."
        )
    rationale.append(
        f"tu saldo {euros(balance)} -> "
        + (f"quedarian {euros(balance - clause)}" if affordable
           else f"te faltan {euros(clause - balance)}")
    )
    if estimated is not None and origin not in ("estimada",) and estimated != clause:
        rationale.append(
            f"la estimacion local decia {euros(estimated)}: "
            f"desvio de {euros(clause - estimated)}"
        )

    warning = ""
    if affordable and clause > balance * BUDGET_WARN_FRACTION:
        pct = clause / balance * 100
        warning = f"se come el {pct:.0f}% de tu saldo en un solo jugador"

    return BidAdvice(
        kind="exacto" if confirmed else "estimado",
        flag="PAGABLE" if affordable else "FUERA DE PRESUPUESTO",
        exact=int(clause),
        rationale=rationale,
        warning=warning,
    )


def market_advice(player, asking, score, trend, balance, max_bid, alternatives):
    """Puja de mercado libre: rango con justificacion."""
    value = player.get("price") or 0

    # --- 1. clasificar lo que piden frente al valor -----------------------
    if value and asking <= value * CHOLLO_THRESHOLD:
        flag = "CHOLLO"
    elif value and asking >= value * INFLADO_THRESHOLD:
        flag = "INFLADO"
    else:
        flag = "A PRECIO DE MERCADO"

    # --- 2. prima por atractivo -------------------------------------------
    premium = BASE_PREMIUM + 0.20 * score.final

    # --- 3. tendencia, ponderada por lo fiable que sea el histórico -------
    # Con una sola foto el `priceIncrement` es un dato de un dia: apenas mueve
    # la puja. Con la curva fiable, pesa entero. El rango se afina solo.
    if trend.reliable_shape:
        trend_weight, precision = 1.0, f"histórico fiable ({trend.days} fotos)"
    elif trend.source == "historico":
        trend_weight, precision = 0.40, f"histórico corto ({trend.days} fotos)"
    else:
        trend_weight, precision = 0.25, "sin histórico: solo el incremento del dia"

    if trend.slope_pct is not None:
        bounded = max(-1.0, min(1.0, trend.slope_pct / 0.05))
        premium += bounded * 0.08 * trend_weight

    # --- 4. correcciones por clasificacion --------------------------------
    if flag == "INFLADO":
        # Ya estas pagando por encima del valor: no persigas la subasta.
        premium = min(premium, BASE_PREMIUM)
    elif flag == "CHOLLO":
        premium += 0.05

    # --- 5. alternativas: si hay recambio igual de bueno y mas barato,
    #        pagar prima es tirar dinero.
    if alternatives >= 2:
        premium *= 0.60
    elif alternatives == 1:
        premium *= 0.80

    # --- 6. datos finos = no te estires ----------------------------------
    premium *= 0.5 + 0.5 * score.confidence

    premium = max(0.0, min(MAX_PREMIUM, premium))

    low = _round_to(asking)
    high = _round_to(asking * (1 + premium))
    if high <= low:
        high = low + STEP

    # --- 7. techos de presupuesto ----------------------------------------
    warning = ""
    if high > max_bid:
        high = _round_to(max_bid)
        warning = f"recortado a tu puja maxima ({euros(max_bid)})"
    if low > max_bid:
        warning = f"no llegas: piden {euros(asking)} y tu tope es {euros(max_bid)}"
    if high > balance * BUDGET_WARN_FRACTION:
        pct = high / balance * 100 if balance else 0
        extra = f"el techo del rango es el {pct:.0f}% de tu saldo"
        warning = f"{warning}; {extra}" if warning else extra

    diff_pct = ((asking - value) / value * 100) if value else 0
    rationale = [
        "subasta a ciegas: no se ven las pujas rivales, el rango es una "
        "recomendacion, no un precio",
        f"piden {euros(asking)} vs valor {euros(value)} ({diff_pct:+.1f}%) -> {flag}",
        f"tendencia: {trend.note}",
        f"precision del rango: {precision}",
        f"prima aplicada {premium * 100:.0f}% sobre lo que piden",
        f"{alternatives} alternativa(s) en {POSITIONS.get(player.get('position'), '?')} "
        f"igual de buenas o mejores y no mas caras"
        + (" -> no pagues prima" if alternatives >= 2 else ""),
        f"tu saldo {euros(balance)}, puja maxima {euros(max_bid)}",
    ]

    return BidAdvice(
        kind="recomendado",
        flag=flag,
        low=low,
        high=high,
        rationale=rationale,
        warning=warning,
    )


def count_alternatives(target, all_scored, tolerance=0.02):
    """Cuantos otros jugadores del mercado son igual de buenos y no mas caros.

    Es el contrapeso a pujar fuerte: si hay recambio, la prima no se justifica.
    """
    count = 0
    for other in all_scored:
        if other["player_id"] == target["player_id"]:
            continue
        if other["position_id"] != target["position_id"]:
            continue
        if other["cost"] > target["cost"]:
            continue
        if other["score"].final >= target["score"].final - tolerance:
            count += 1
    return count
