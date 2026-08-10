"""Compara las respuestas de plantilla (propia vs rival) buscando clausulas.

Solo lee ficheros locales de .\\data\\. No toca la red.
Redacta nombres de managers y jugadores: solo interesan campos y valores.
"""

import json
import os
import re

FILES = [
    ("MI PLANTILLA", "explore-user_self_players.json"),
    ("MI PLANTILLA (sin fields)", "explore-user_self.json"),
    ("RIVAL (fields=*,players(*))", "explore-user_rival_players.json"),
    ("RIVAL (fields deep + clause)", "explore-user_rival_deep.json"),
    ("RIVAL (sin fields)", "explore-user_rival.json"),
]

# Cualquier cosa que pueda ser una clausula, por laxo que sea el nombre.
CLAUSE_RE = re.compile(
    r"clause|clausula|lock|protect|steal|buyout|rescis", re.IGNORECASE
)
NAME_KEYS = {"name", "email", "icon", "avatar", "image", "slug", "nickname", "phone"}


def unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def show(key, value):
    if key.lower() in NAME_KEYS:
        return "<redactado>"
    if isinstance(value, (dict, list)):
        return f"<{type(value).__name__} de {len(value)}>"
    return repr(value)


def walk_clause_like(node, path="", hits=None, depth=0):
    if hits is None:
        hits = []
    if depth > 10:
        return hits
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if CLAUSE_RE.search(k):
                hits.append((here, type(v).__name__, show(k, v)))
            walk_clause_like(v, here, hits, depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):
            walk_clause_like(v, f"{path}[{i}]", hits, depth + 1)
    return hits


def find_players(node, depth=0):
    """Devuelve la primera lista que parezca una plantilla."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        for key in ("players", "squad", "lineup"):
            value = node.get(key)
            if isinstance(value, list):
                return value
        for v in node.values():
            found = find_players(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node[:3]:
            found = find_players(v, depth + 1)
            if found is not None:
                return found
    return None


def report(label, filename):
    path = os.path.join("data", filename)
    print("=" * 76)
    print(label)
    print(f"  fichero: {filename}")
    print("=" * 76)

    if not os.path.exists(path):
        print("  NO EXISTE (la sonda no llego a guardarse)\n")
        return
    if os.path.getsize(path) == 0:
        print("  VACIO (0 bytes)\n")
        return

    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    data = unwrap(raw)

    if isinstance(data, dict):
        print(f"  claves de primer nivel: {sorted(data.keys())}")
        for k, v in sorted(data.items()):
            print(f"    {k}: {type(v).__name__} = {show(k, v)}")
    else:
        print(f"  tipo raiz: {type(data).__name__}")

    players = find_players(data)
    if players is None:
        print("\n  >> NO hay ninguna lista de plantilla en esta respuesta")
    elif not players:
        print("\n  >> lista de plantilla PRESENTE pero VACIA (0 jugadores)")
    else:
        print(f"\n  >> plantilla: {len(players)} entradas")
        first = players[0]
        if isinstance(first, dict):
            print(f"     claves de players[0]: {sorted(first.keys())}")
            for k, v in sorted(first.items()):
                print(f"       {k}: {type(v).__name__} = {show(k, v)}")
            # union de claves por si las entradas no son homogeneas
            union = set()
            for p in players:
                if isinstance(p, dict):
                    union |= set(p.keys())
            extra = union - set(first.keys())
            if extra:
                print(f"     claves presentes solo en otras entradas: {sorted(extra)}")
        else:
            print(f"     players[0] es {type(first).__name__} = {first!r}")

    hits = walk_clause_like(data)
    print("\n  campos tipo clausula (clause/lock/protect/steal/buyout/rescis):")
    if hits:
        seen = set()
        for p, kind, val in hits:
            if p in seen:
                continue
            seen.add(p)
            print(f"    {p}: {kind} = {val}")
    else:
        print("    NINGUNO")
    print()


def main():
    for label, filename in FILES:
        report(label, filename)


if __name__ == "__main__":
    main()
