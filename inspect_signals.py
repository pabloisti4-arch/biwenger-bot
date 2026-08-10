"""Que senales hay realmente disponibles en jornada 1.

Solo lee data\\20260810-173214-players.json. Sin red.
Decide sobre que puede apoyarse el motor de puntuacion.
"""

import glob
import json
import os
from collections import Counter

PLAYERS_GLOB = os.path.join("data", "*-players.json")


def load():
    paths = sorted(glob.glob(PLAYERS_GLOB))
    if not paths:
        raise SystemExit("No hay volcado de jugadores en .\\data\\")
    with open(paths[-1], encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("data", data)


def main():
    d = load()
    players = list(d["players"].values())
    teams = d["teams"]

    print("=" * 72)
    print("1. SENALES DE RENDIMIENTO")
    print("=" * 72)
    for field in ("points", "playedHome", "playedAway", "pointsHome", "pointsAway"):
        values = [p.get(field, 0) for p in players]
        nonzero = sum(1 for v in values if v)
        print(f"  {field:<18} no-cero: {nonzero}/{len(values)}  max={max(values)}")

    last = [p.get("pointsLastSeason") or 0 for p in players]
    nonzero_last = sum(1 for v in last if v)
    print(f"  pointsLastSeason   no-cero: {nonzero_last}/{len(last)}  max={max(last)}")

    fitness_lens = Counter(len(p.get("fitness") or []) for p in players)
    print(f"  longitudes de 'fitness': {dict(fitness_lens)}")

    status = Counter(p.get("status") for p in players)
    print(f"  status: {dict(status)}")

    print()
    print("=" * 72)
    print("2. SENALES DE TENDENCIA DE VALOR")
    print("=" * 72)
    incs = [p.get("priceIncrement") or 0 for p in players]
    print(f"  priceIncrement no-cero: {sum(1 for v in incs if v)}/{len(incs)}")
    print(f"    min={min(incs):,}  max={max(incs):,}")
    positives = sum(1 for v in incs if v > 0)
    negatives = sum(1 for v in incs if v < 0)
    print(f"    subiendo: {positives}   bajando: {negatives}")

    print()
    print("=" * 72)
    print("3. CALENDARIO: forma de nextGames y difficulty")
    print("=" * 72)
    counts = Counter(len(t.get("nextGames") or []) for t in teams.values())
    print(f"  nextGames por equipo: {dict(counts)}")

    sample = None
    for team in teams.values():
        games = team.get("nextGames") or []
        if games:
            sample = games[0]
            break
    if sample:
        print(f"  claves de nextGames[0]: {sorted(sample.keys())}")
        for side in ("home", "away"):
            node = sample.get(side) or {}
            print(f"  {side}: claves={sorted(node.keys())}")
            diff = node.get("difficulty")
            if isinstance(diff, dict):
                print(f"    difficulty: {json.dumps(diff, ensure_ascii=False)}")
            else:
                print(f"    difficulty: {type(diff).__name__} = {diff!r}")

    print()
    print("=" * 72)
    print("4. DISTRIBUCION DE PRECIO POR POSICION")
    print("=" * 72)
    by_pos = {}
    for p in players:
        by_pos.setdefault(p.get("position"), []).append(p.get("price") or 0)
    for pos in sorted(by_pos, key=lambda x: (x is None, x)):
        vals = sorted(by_pos[pos])
        mid = vals[len(vals) // 2]
        print(f"  posicion {pos}: n={len(vals):<4} mediana={mid:>12,}  max={vals[-1]:>12,}")


if __name__ == "__main__":
    main()
