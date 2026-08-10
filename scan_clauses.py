"""Escanea TODOS los JSON de .\\data\\ buscando rastro de clausulas y propiedad.

Solo lee ficheros locales. Responde a: aparece la palabra clausula en algun
sitio de lo que ya hemos descargado, y con que forma.
"""

import glob
import json
import os
import re

CLAUSE_RE = re.compile(r"clause|clausula|buyout|rescis|steal|protect|lock", re.IGNORECASE)
OWNER_RE = re.compile(r"owner|user|manager", re.IGNORECASE)
NAME_KEYS = {"name", "email", "icon", "avatar", "image", "nickname", "phone", "title", "body"}


def show(key, value):
    if key.lower() in NAME_KEYS:
        return "<redactado>"
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(value.keys())[:8]) + "}"
    if isinstance(value, list):
        return f"<list de {len(value)}>"
    return repr(value)


def walk(node, regex, path="", hits=None, depth=0, list_cap=3):
    if hits is None:
        hits = {}
    if depth > 12:
        return hits
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if regex.search(k):
                # Normaliza indices de lista para agrupar rutas equivalentes.
                norm = re.sub(r"\[\d+\]", "[]", here)
                hits.setdefault(norm, (type(v).__name__, show(k, v)))
            walk(v, regex, here, hits, depth + 1, list_cap)
    elif isinstance(node, list):
        for i, v in enumerate(node[:list_cap]):
            walk(v, regex, f"{path}[{i}]", hits, depth + 1, list_cap)
    return hits


def main():
    paths = sorted(glob.glob(os.path.join("data", "*.json")))
    total = 0

    for path in paths:
        if os.path.getsize(path) == 0:
            continue
        with open(path, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except json.JSONDecodeError:
                continue

        clause_hits = walk(data, CLAUSE_RE)
        if clause_hits:
            total += len(clause_hits)
            print(f"--- {os.path.basename(path)} ---")
            for route, (kind, value) in sorted(clause_hits.items()):
                print(f"    {route}: {kind} = {value}")
            print()

    if not total:
        print("Ningun campo tipo clausula en NINGUN fichero descargado.\n")

    # El feed de la liga registra los movimientos: ver que tipos existen.
    board = os.path.join("data", "explore-board.json")
    if os.path.exists(board) and os.path.getsize(board) > 0:
        with open(board, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data.get("data") if isinstance(data, dict) else data
        if isinstance(entries, list):
            print(f"--- explore-board.json: {len(entries)} entradas del feed ---")
            kinds = {}
            for e in entries:
                if isinstance(e, dict):
                    kinds.setdefault(e.get("type"), []).append(e)
            for kind, items in kinds.items():
                keys = set()
                for i in items:
                    keys |= set(i.keys())
                print(f"    type={kind!r}  x{len(items)}  claves={sorted(keys)}")
            # Detalle de un movimiento de mercado, que es donde iria una clausula.
            for kind, items in kinds.items():
                if kind and "market" in str(kind).lower() or kind == "transfer":
                    print(f"\n    ejemplo de type={kind!r}:")
                    sample = json.dumps(items[0], ensure_ascii=False, indent=6)
                    print(sample[:1200])
                    break


if __name__ == "__main__":
    main()
