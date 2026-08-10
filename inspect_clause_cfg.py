"""Detalle de la configuracion de clausulas, el feed y el endpoint de jugador.

Solo lee ficheros locales de .\\data\\. Redacta nombres.
"""

import json
import os
import re

NAME_KEYS = {"name", "email", "icon", "avatar", "image", "nickname", "phone", "title"}
OWNER_RE = re.compile(r"owner|clause|price|bid|market|status|fitness", re.IGNORECASE)


def load(filename):
    path = os.path.join("data", filename)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("data") if isinstance(data, dict) and "data" in data else data


def redact_deep(node, depth=0):
    """Copia con los campos de nombre redactados, para poder imprimir seguro."""
    if depth > 8:
        return "<...>"
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k.lower() in NAME_KEYS and isinstance(v, str):
                out[k] = f"<{len(v)} chars>"
            else:
                out[k] = redact_deep(v, depth + 1)
        return out
    if isinstance(node, list):
        return [redact_deep(v, depth + 1) for v in node[:4]]
    return node


def section(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


def main():
    # --- 1. Configuracion completa de clausulas de la liga -------------
    section("1. AJUSTES DE CLAUSULA DE LA LIGA")
    league = load("explore-league_fields.json")
    settings = (league or {}).get("settings") or {}
    for k in sorted(settings):
        if "clause" in k.lower() or k in ("marketMode", "balance", "immediateSales"):
            print(f"  {k}: {json.dumps(settings[k], ensure_ascii=False)}")

    # --- 2. Feed: como se registran los movimientos --------------------
    section("2. FEED DE LA LIGA (playerMovements)")
    board = load("explore-board.json")
    if isinstance(board, list):
        for entry in board:
            if isinstance(entry, dict) and entry.get("type") == "playerMovements":
                content = entry.get("content")
                print(f"  tipo de 'content': {type(content).__name__}")
                if isinstance(content, list) and content:
                    print(f"  {len(content)} movimientos; claves del primero:")
                    print("   ", sorted(content[0].keys()))
                    print("  primer movimiento (redactado):")
                    print(json.dumps(redact_deep(content[0]), ensure_ascii=False, indent=4))
                elif isinstance(content, str):
                    print(f"  es texto plano de {len(content)} chars -> inservible")
                    print(f"  muestra: {content[:200]!r}")
                break

    # --- 3. Endpoint de jugador individual -----------------------------
    section("3. /players/la-liga/{id}  (via campos de propiedad)")
    detail = load("explore-player_detail.json")
    if isinstance(detail, dict):
        print(f"  claves de primer nivel: {sorted(detail.keys())}")
        for k in sorted(detail):
            v = detail[k]
            if OWNER_RE.search(k):
                if isinstance(v, (dict, list)):
                    inner = sorted(v.keys()) if isinstance(v, dict) else f"list de {len(v)}"
                    print(f"    {k}: {type(v).__name__} -> {inner}")
                else:
                    print(f"    {k}: {type(v).__name__} = {v!r}")


if __name__ == "__main__":
    main()
