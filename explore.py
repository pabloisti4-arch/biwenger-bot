"""Barrido de exploracion de la API de Biwenger. SOLO LECTURA.

Prueba una lista de endpoints candidatos y reporta que existe, con que forma y
donde aparecen los campos de clausula. No ejecuta ninguna accion: todas las
peticiones son GET. No hay un solo POST/PUT/DELETE en este fichero.

Guarda cada respuesta con exito en .\\data\\explore-<nombre>.json para poder
inspeccionarla despues con:  py inspect_data.py explore-

Uso:
    py explore.py                      # barrido completo
    py explore.py --only=league,user   # solo algunas sondas
    py explore.py --list               # ver nombres de sondas sin pedir nada
    py explore.py --delay=1.5          # mas pausa entre peticiones
"""

import argparse
import json
import os
import sys
import time

import config
from client import API, BiwengerClient, VersionError
from inspect_data import SENSITIVE

# Campos cuya presencia queremos localizar: son la base de los clausulazos.
HUNT = ("clause", "lock", "owner", "bid", "offer", "balance", "maximum")


def parse_args(argv):
    p = argparse.ArgumentParser(description="Exploracion read-only de la API")
    p.add_argument("--only", default="", help="lista de sondas separadas por coma")
    p.add_argument("--list", action="store_true", help="listar sondas y salir")
    p.add_argument(
        "--variants",
        action="store_true",
        help="barrer variantes de sintaxis de `fields` para expandir players[]",
    )
    p.add_argument("--delay", type=float, default=1.0, help="pausa entre peticiones (s)")
    p.add_argument("--depth", type=int, default=4, help="profundidad del esquema impreso")
    return p.parse_args(argv)


def build_probes(cfg, league_id, own_user_id, sample_player_id):
    """Sondas candidatas. Varias son conjeturas: el barrido dira cuales viven."""
    lg, me = league_id, own_user_id

    return [
        # --- liga: clasificacion y lista de managers ---------------------
        ("league_plain", f"/league/{lg}", None),
        ("league_include_all", "/league", {"include": "all"}),
        ("league_fields", f"/league/{lg}",
         {"fields": "*,standings,tournaments,settings"}),
        ("league_standings", f"/league/{lg}/standings", None),

        # --- plantilla propia: referencia de que campos existen ----------
        ("user_self", f"/user/{me}", None),
        ("user_self_players", f"/user/{me}",
         {"fields": "*,players(*),lineups(round,points)"}),

        # --- plantilla rival + clausulas (EL objetivo) -------------------
        # Se rellena en runtime con un id rival descubierto en league_*.
        ("user_rival", "/user/{RIVAL}", None),
        ("user_rival_players", "/user/{RIVAL}", {"fields": "*,players(*)"}),
        ("user_rival_deep", "/user/{RIVAL}",
         {"fields": "*,players(*,player(*),clause,clauseLockedUntil)"}),

        # --- detalle de jugador -----------------------------------------
        ("player_detail", f"/players/{cfg['competition']}/{sample_player_id}",
         {"lang": cfg["lang"], "fields": "*,team,fitness,reports(*)"}),

        # --- movimientos de la liga (fichajes, clausulazos pasados) ------
        ("board", f"/league/{lg}/board", {"limit": 20}),
        ("board_offers", f"/league/{lg}/board", {"type": "market", "limit": 20}),

        # --- ofertas y calendario ---------------------------------------
        ("offers", "/offers", None),
        ("rounds", f"/rounds/{cfg['competition']}", None),
    ]


def build_field_variants(cfg, league_id):
    """Variantes de sintaxis de `fields` para expandir players[].

    El barrido inicial demostro que `fields` funciona a primer nivel pero que
    `players(*)` devuelve solo `id`: la expansion anidada se ignora en silencio
    (200, sin error). Estas sondas buscan la sintaxis que si expande.
    """
    lg = league_id
    full = "id,name,price,clause,clauseLockedUntil,status,position,teamID,owner,bid"

    return [
        # Control: reproduce el resultado conocido (solo id).
        ("fv_control_star", "/user/{RIVAL}", {"fields": "*,players(*)"}),
        # Hipotesis principal: enumerar campos en vez de usar `*` anidado.
        ("fv_enumerated", "/user/{RIVAL}",
         {"fields": "*,players(id,clause,clauseLockedUntil,owner,price)"}),
        ("fv_enumerated_full", "/user/{RIVAL}", {"fields": f"*,players({full})"}),
        # Variantes de forma.
        ("fv_bare_players", "/user/{RIVAL}", {"fields": "*,players"}),
        ("fv_player_sub", "/user/{RIVAL}", {"fields": "*,players(player(*))"}),
        ("fv_players_owner", "/user/{RIVAL}", {"fields": "*,players(*,owner)"}),
        # Otro parametro: puede que la expansion vaya por `include`.
        ("fv_include_all", "/user/{RIVAL}", {"include": "all"}),
        ("fv_include_players", "/user/{RIVAL}",
         {"include": "players", "fields": "*"}),
        # Endpoint propio sin id: a veces devuelve mas que el de rival.
        ("fv_user_self_noid", "/user", {"fields": f"*,players({full})"}),
        # Rutas alternativas donde podria vivir el estado por liga.
        ("fv_league_players", f"/league/{lg}/players", None),
        ("fv_league_users", f"/league/{lg}",
         {"fields": "*,standings,users(*,players(*))"}),
        ("fv_league_clauses", f"/league/{lg}/clauses", None),
        ("fv_market_clauses", "/market", {"type": "clauses"}),
    ]


def _variant_run(cfg, league_id, own_user_id, sample_player_id):
    """Descubrimiento de un rival + barrido de variantes de sintaxis."""
    discovery = [
        probe
        for probe in build_probes(cfg, league_id, own_user_id, sample_player_id)
        if probe[0] in ("league_plain", "league_fields")
    ]
    return discovery + build_field_variants(cfg, league_id)


def find_keys(node, substrings, path="", out=None, depth=0):
    """Localiza rutas cuyo nombre de clave contenga alguna de las subcadenas."""
    if out is None:
        out = []
    if depth > 8:
        return out
    if isinstance(node, dict):
        for k, v in node.items():
            here = f"{path}.{k}" if path else k
            if any(s in k.lower() for s in substrings):
                if isinstance(v, (dict, list)):
                    out.append((here, type(v).__name__, ""))
                else:
                    shown = "<redactado>" if k.lower() in SENSITIVE else repr(v)
                    out.append((here, type(v).__name__, shown))
            find_keys(v, substrings, here, out, depth + 1)
    elif isinstance(node, list):
        # Solo el primer elemento: las listas son homogeneas.
        if node:
            find_keys(node[0], substrings, f"{path}[0]", out, depth + 1)
    return out


def discover_rivals(payload, own_user_id):
    """Extrae (id, ) de los managers de la liga desde una respuesta de /league."""
    found = {}

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, dict):
            uid = node.get("id")
            # Un manager tiene id + (balance|points|position) y no es un jugador.
            looks_like_user = uid is not None and any(
                k in node for k in ("points", "position", "balance", "joinDate")
            ) and "teamID" not in node
            if looks_like_user:
                try:
                    found[int(uid)] = node.get("position")
                except (TypeError, ValueError):
                    pass
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)

    walk(payload)
    found.pop(int(own_user_id), None)
    return sorted(found)


def save(name, payload):
    os.makedirs("data", exist_ok=True)
    path = os.path.join("data", f"explore-{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


def summarize(payload, depth):
    """Imprime claves de primer nivel y los campos cazados."""
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload

    if isinstance(data, dict):
        print(f"      claves: {sorted(data.keys())[:18]}")
    elif isinstance(data, list):
        print(f"      lista de {len(data)} elementos")
        if data and isinstance(data[0], dict):
            print(f"      claves[0]: {sorted(data[0].keys())[:18]}")
    else:
        print(f"      tipo: {type(data).__name__}")

    # Senal clave del barrido --variants: si players[] se expandio o no.
    squad = data.get("players") if isinstance(data, dict) else None
    if isinstance(squad, list) and squad and isinstance(squad[0], dict):
        keys = sorted(squad[0].keys())
        veredicto = "SOLO id -> no expandio" if keys == ["id"] else f"EXPANDIO ({len(keys)} campos)"
        print(f"      players[0]: {veredicto}")
        print(f"        claves: {keys}")

    hits = find_keys(data, HUNT)
    if hits:
        print("      *** campos de interes ***")
        seen = set()
        for path, kind, value in hits:
            if path in seen:
                continue
            seen.add(path)
            suffix = f" = {value}" if value else ""
            print(f"        {path}: {kind}{suffix}")
    return data


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # --list es introspeccion: no necesita token ni red.
    if args.list:
        fake = {"competition": "la-liga", "lang": "es"}
        listing = build_probes(fake, "<LIGA>", "<USER>", 1676)
        if args.variants:
            listing = _variant_run(fake, "<LIGA>", "<USER>", 1676)
        for name, path, params in listing:
            print(f"  {name:<22} GET {path}  {params or ''}")
        return 0

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"ERROR de configuracion:\n{exc}", file=sys.stderr)
        return 2

    client = BiwengerClient(cfg)
    if args.variants:
        probes = _variant_run(cfg, cfg["league"], cfg["user"], 1676)
    else:
        probes = build_probes(cfg, cfg["league"], cfg["user"], sample_player_id=1676)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    rivals = []

    print("=" * 78)
    print("EXPLORACION READ-ONLY (ningun POST/PUT/DELETE)")
    print(f"liga={cfg['league']}  usuario={cfg['user']}  X-Version={cfg['version']}")
    print("=" * 78)

    for name, path, params in probes:
        if only and name not in only:
            continue

        if "{RIVAL}" in path:
            if not rivals:
                print(f"\n[{name}] SALTADA: no se descubrio ningun id rival todavia.")
                print("      corre primero las sondas league_* o pasa --only con ellas")
                continue
            path = path.replace("{RIVAL}", str(rivals[0]))

        print(f"\n[{name}] GET {API}{path}")
        if params:
            print(f"      params: {params}")

        try:
            status, payload = client.probe(path, params)
        except VersionError as exc:
            print(f"ERROR de version:\n{exc}", file=sys.stderr)
            return 5

        if status is None:
            print(f"      {payload}")
        elif status >= 400:
            msg = payload
            if isinstance(payload, dict):
                msg = payload.get("message") or payload
            print(f"      HTTP {status} -> {str(msg)[:160]}")
        else:
            print(f"      HTTP {status}  OK  -> {save(name, payload)}")
            data = summarize(payload, args.depth)

            if name.startswith("league") and not rivals:
                rivals = discover_rivals(data, cfg["user"])
                if rivals:
                    print(f"      managers rivales descubiertos: {len(rivals)} ids")

        time.sleep(args.delay)

    print("\n" + "=" * 78)
    print("Barrido terminado. Nada fue modificado en tu liga.")
    print("Inspecciona el detalle con:  py inspect_data.py explore-")
    if rivals:
        print(f"Ids rivales en memoria: {len(rivals)} (no se imprimen nombres)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
