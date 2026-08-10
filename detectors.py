"""Los cuatro detectores de oportunidades. Solo calculan y proponen.

Ninguna funcion de este modulo escribe nada ni ejecuta ninguna accion.
"""

from dataclasses import dataclass, field
from itertools import zip_longest

from bidding import (
    CHOLLO_THRESHOLD,
    INFLADO_THRESHOLD,
    clause_advice,
    count_alternatives,
    market_advice,
)
from scoring import POSITIONS, Score, euros


@dataclass
class Opportunity:
    kind: str
    player_id: int
    name: str
    position: str
    team: str
    value: int
    cost: int | None
    score: Score
    improvement: float | None = None
    advice: object = None
    extra: dict = field(default_factory=dict)

    @property
    def roi(self):
        """Mejora de plantilla por millon gastado."""
        if not self.cost or self.improvement is None:
            return None
        return self.improvement / (self.cost / 1_000_000)


def _describe(signals, player):
    return (
        player.get("name") or "?",
        POSITIONS.get(player.get("position"), "?"),
        signals.team_name(player.get("teamID") or player.get("teamId")),
    )


def squad_baseline(signals, my_player_ids):
    """Peor puntuacion propia por posicion: el listón que hay que superar."""
    baseline = {}
    for pid in my_player_ids:
        player = signals.player(pid)
        if not signals.playable(player):
            continue
        pos = player.get("position")
        score = signals.score_buy(player)
        current = baseline.get(pos)
        if current is None or score.final < current[0]:
            baseline[pos] = (score.final, int(pid))
    return baseline


def _improvement(signals, player, baseline):
    """Cuanto mejora mi plantilla, comparando con mi peor jugador de su puesto."""
    score = signals.score_buy(player)
    pos = player.get("position")
    if pos not in baseline:
        # No tengo a nadie en ese puesto: cualquier fichaje es mejora neta.
        return score.final, None
    worst, worst_id = baseline[pos]
    return score.final - worst, worst_id


def _score_market(signals, sales, baseline, max_bid, scorer):
    """Puntua todo el mercado asequible. Base comun de (1) y (3)."""
    scored = []
    for sale in sales:
        pid = sale.get("player_id")
        player = signals.player(pid) if pid else None
        if not signals.playable(player):
            continue
        asking = sale.get("market_price")
        if not isinstance(asking, (int, float)) or asking <= 0 or asking > max_bid:
            continue

        improvement, worst_id = _improvement(signals, player, baseline)
        scored.append(
            {
                "player": player,
                "player_id": int(pid),
                "position_id": player.get("position"),
                "cost": int(asking),
                "score": scorer(player, asking),
                "improvement": improvement,
                "worst_id": worst_id,
                "sale": sale,
            }
        )
    for entry in scored:
        entry["alternatives"] = count_alternatives(entry, scored)
    return scored


def market_price_stats(signals, sales):
    """Cuantos piden por debajo, igual o por encima de su valor."""
    below = same = above = total = 0
    for sale in sales:
        player = signals.player(sale.get("player_id")) if sale.get("player_id") else None
        value = (player or {}).get("price") or 0
        asking = sale.get("market_price")
        if not value or not isinstance(asking, (int, float)):
            continue
        total += 1
        if asking <= value * CHOLLO_THRESHOLD:
            below += 1
        elif asking >= value * INFLADO_THRESHOLD:
            above += 1
        else:
            same += 1
    return {"total": total, "por_debajo": below, "a_su_valor": same, "por_encima": above}


# -- (1) compras en el mercado ------------------------------------------------

def find_bargains(signals, sales, baseline, balance, max_bid, limit=8):
    scored = _score_market(signals, sales, baseline, max_bid, signals.score_buy)

    out = []
    for entry in scored:
        player = entry["player"]
        name, pos, team = _describe(signals, player)
        advice = market_advice(
            player,
            entry["cost"],
            entry["score"],
            signals.trend_info(player),
            balance,
            max_bid,
            entry["alternatives"],
        )
        out.append(
            Opportunity(
                kind="chollo",
                player_id=entry["player_id"],
                name=name,
                position=pos,
                team=team,
                value=player.get("price") or 0,
                cost=entry["cost"],
                score=entry["score"],
                improvement=entry["improvement"],
                advice=advice,
                extra={
                    "vendedor": entry["sale"].get("seller"),
                    "hasta": entry["sale"].get("until"),
                    "alternativas": entry["alternatives"],
                    "sustituiria_a": (
                        signals.label(entry["worst_id"]) if entry["worst_id"]
                        else "hueco libre"
                    ),
                },
            )
        )
    out.sort(key=lambda o: -o.score.final)
    return out[:limit]


# -- (2) cuando vender a los mios --------------------------------------------

def find_sales(signals, my_player_ids, limit=6):
    out = []
    for pid in my_player_ids:
        player = signals.player(pid)
        if not signals.playable(player):
            continue
        score = signals.score_sell(player)
        info = signals.trend_info(player)
        name, pos, team = _describe(signals, player)
        price = player.get("price") or 0
        out.append(
            Opportunity(
                kind="venta",
                player_id=int(pid),
                name=name,
                position=pos,
                team=team,
                value=price,
                cost=None,
                score=score,
                extra={
                    "ingreso_estimado": price,
                    "clausula_propia": signals.estimated_clause(player),
                    "tendencia": info.note,
                    "forma_fiable": info.reliable_shape,
                    "status": player.get("status"),
                },
            )
        )
    out.sort(key=lambda o: -o.score.final)
    return out[:limit]


# -- (3) especulacion por tendencia de valor ---------------------------------

def find_speculation(signals, sales, baseline, balance, max_bid, limit=6, horizon_days=7):
    scored = _score_market(signals, sales, baseline, max_bid, signals.score_speculation)

    out = []
    for entry in scored:
        player = entry["player"]
        info = signals.trend_info(player)
        if info.slope is None or info.slope <= 0:
            continue  # especular a la baja no compra nada

        name, pos, team = _describe(signals, player)
        advice = market_advice(
            player, entry["cost"], entry["score"], info, balance, max_bid,
            entry["alternatives"],
        )
        out.append(
            Opportunity(
                kind="especulacion",
                player_id=entry["player_id"],
                name=name,
                position=pos,
                team=team,
                value=player.get("price") or 0,
                cost=entry["cost"],
                score=entry["score"],
                advice=advice,
                extra={
                    "tendencia": info.note,
                    "forma_fiable": info.reliable_shape,
                    # Extrapolacion lineal: la pendiente NO se mantiene constante.
                    # Es una cota optimista, no un pronostico.
                    "proyeccion_lineal": int(info.slope * horizon_days),
                    "horizonte_dias": horizon_days,
                },
            )
        )
    out.sort(key=lambda o: -o.score.final)
    return out[:limit]


# -- (4) clausulazos ---------------------------------------------------------

def find_clause_raids(
    signals,
    rival_squads,
    baseline,
    balance,
    clause_reader,
    confirm_limit=12,
    limit=8,
    estimate_margin=1.15,
):
    """Objetivos de clausulazo, gastando el minimo de peticiones.

    Fase A (0 peticiones): estima la clausula como 1.5 x valor, descarta lo
    inasumible y lo que no mejora la plantilla, y ordena por retorno.
    Fase B (1 peticion por candidata): confirma la clausula real de las mejores
    y recomprueba que sigue siendo pagable.
    """
    candidates = []
    skipped_unaffordable = 0
    skipped_no_gain = 0

    # --- Fase A: filtro en local -----------------------------------------
    for owner_id, player_ids in rival_squads.items():
        for pid in player_ids:
            player = signals.player(pid)
            if not signals.playable(player):
                continue

            estimate = signals.estimated_clause(player)
            if estimate > balance * estimate_margin:
                skipped_unaffordable += 1
                continue

            improvement, worst_id = _improvement(signals, player, baseline)
            if improvement is None or improvement <= 0:
                skipped_no_gain += 1
                continue

            candidates.append(
                {
                    "player": player,
                    "player_id": int(pid),
                    "owner_id": owner_id,
                    "estimate": estimate,
                    "improvement": improvement,
                    "worst_id": worst_id,
                }
            )

    # La lista corta es la UNION de los mejores por cada criterio, intercalados.
    # Ordenar solo por eficiencia dejaria fuera a los caros de alto impacto, que
    # son precisamente el objetivo del segundo ranking: nunca se confirmarian.
    by_efficiency = sorted(
        candidates,
        key=lambda c: -(c["improvement"] / max(c["estimate"] / 1_000_000, 0.001)),
    )
    by_impact = sorted(candidates, key=lambda c: -c["improvement"])

    shortlist = []
    seen = set()
    for pair in zip_longest(by_efficiency, by_impact):
        for cand in pair:
            if cand is None or cand["player_id"] in seen:
                continue
            if len(shortlist) >= confirm_limit:
                break
            seen.add(cand["player_id"])
            shortlist.append(cand)
        if len(shortlist) >= confirm_limit:
            break

    # --- Fase B: confirmar clausula real solo de la lista corta ----------
    confirmed = []
    for cand in shortlist:
        player = cand["player"]
        clause, origin = clause_reader.read(cand["player_id"])
        if clause is None:
            clause, origin = cand["estimate"], "estimada"

        advice = clause_advice(clause, balance, origin, cand["estimate"])
        score = signals.score_buy(player)
        name, pos, team = _describe(signals, player)

        confirmed.append(
            Opportunity(
                kind="clausulazo",
                player_id=cand["player_id"],
                name=name,
                position=pos,
                team=team,
                value=player.get("price") or 0,
                cost=int(clause),
                score=score,
                improvement=cand["improvement"],
                advice=advice,
                extra={
                    "origen_clausula": origin,
                    "estimada": cand["estimate"],
                    "pagable": clause <= balance,
                    "sustituiria_a": (
                        signals.label(cand["worst_id"]) if cand["worst_id"]
                        else "hueco libre"
                    ),
                    "duenyo_id": cand["owner_id"],
                },
            )
        )

    # Se mantiene el filtro pedido: solo pagable con el saldo actual, y solo
    # si mejora la plantilla (eso ya lo garantizo la Fase A).
    payable = [op for op in confirmed if op.extra["pagable"]]
    rejected = [op for op in confirmed if not op.extra["pagable"]]

    # (a) EFICIENCIA: mejora por millon. Premia gangas que tapan agujeros.
    efficiency = sorted(payable, key=lambda o: -(o.roi or 0))[:limit]
    # (b) IMPACTO: mejora absoluta, sin penalizar el precio. Los jugadorazos.
    impact = sorted(payable, key=lambda o: -(o.improvement or 0))[:limit]

    stats = {
        "evaluados": sum(len(v) for v in rival_squads.values()),
        "descartados_por_saldo": skipped_unaffordable,
        "descartados_sin_mejora": skipped_no_gain,
        "candidatos": len(candidates),
        "confirmados_por_api": len(shortlist),
        "pagables_tras_confirmar": len(payable),
        "no_pagables_tras_confirmar": len(rejected),
    }
    return efficiency, impact, stats
