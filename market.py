"""Normalizacion del mercado.

La respuesta de /market no trae nombres de jugador, solo ids. Aqui la cruzamos
con el dataset publico de jugadores para tener algo legible.
"""

from datetime import datetime, timezone

POSITIONS = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}


def _as_id(value):
    """El campo `player`/`user` llega a veces como int y a veces como objeto."""
    if isinstance(value, dict):
        return value.get("id")
    return value


def index_players(players_data):
    """Devuelve {id(int): dict_jugador} y {id(int): nombre_equipo}."""
    players = (players_data or {}).get("players") or {}
    teams = (players_data or {}).get("teams") or {}

    by_id = {}
    # Puede venir como dict {"1234": {...}} o como lista [{...}].
    entries = players.values() if isinstance(players, dict) else players
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("id")
        if pid is not None:
            by_id[int(pid)] = entry

    team_names = {}
    tentries = teams.values() if isinstance(teams, dict) else (teams or [])
    for entry in tentries:
        if isinstance(entry, dict) and entry.get("id") is not None:
            team_names[int(entry["id"])] = entry.get("name") or entry.get("slug") or "?"

    return by_id, team_names


def _sale_list(market_data):
    """Extrae la lista de jugadores en venta, tolerando variaciones de forma."""
    if not isinstance(market_data, dict):
        return []
    for key in ("sales", "market", "offers", "items"):
        value = market_data.get(key)
        if isinstance(value, list) and value:
            return value
    # Ultimo recurso: la primera lista de dicts que encontremos.
    for value in market_data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


def normalize(market_data, players_by_id, team_names):
    """Convierte el mercado crudo en filas planas listas para imprimir."""
    rows = []
    for sale in _sale_list(market_data):
        if not isinstance(sale, dict):
            continue

        pid = _as_id(sale.get("player"))
        player = players_by_id.get(int(pid)) if pid is not None else None

        seller = sale.get("user")
        seller_name = None
        if isinstance(seller, dict):
            seller_name = seller.get("name")
        elif seller is not None:
            seller_name = f"user:{seller}"

        market_price = sale.get("price")
        base_price = (player or {}).get("price")

        diff_pct = None
        if isinstance(market_price, (int, float)) and isinstance(base_price, (int, float)):
            if base_price:
                diff_pct = (market_price - base_price) / base_price * 100

        team_id = (player or {}).get("teamID") or (player or {}).get("teamId")

        rows.append(
            {
                "sale_id": sale.get("id"),
                "player_id": pid,
                "name": (player or {}).get("name") or f"<id {pid}>",
                "position": POSITIONS.get((player or {}).get("position"), "?"),
                "team": team_names.get(int(team_id)) if team_id else "?",
                "market_price": market_price,
                "base_price": base_price,
                "diff_pct": diff_pct,
                "points": (player or {}).get("points"),
                "fitness": (player or {}).get("fitness"),
                "status": (player or {}).get("status"),
                "seller": seller_name or "MERCADO LIBRE",
                "until": _fmt_ts(sale.get("until") or sale.get("date")),
                "type": sale.get("type"),
            }
        )
    return rows


def _fmt_ts(value):
    if not isinstance(value, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return str(value)


def euros(value):
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:,.0f}".replace(",", ".")
