"""Sonda de UN SOLO GET a /api/v2/owners/player/{id}/clause.

Objetivo: comprobar si la clausula se puede LEER con GET, dado que la escritura
va por PUT. No modifica nada.

Garantias de este fichero:
  - El verbo esta en una constante y se comprueba antes de salir a la red.
  - Se hace exactamente UNA peticion: no hay bucles ni reintentos.
  - No existe ninguna llamada a post/put/patch/delete en este modulo.

Uso:
    py probe_clause.py --mine              # lista ids de MIS jugadores (sin red)
    py probe_clause.py 1462 --dry-run      # muestra la URL, no pide nada
    py probe_clause.py 1462                # el unico GET
"""

import argparse
import json
import os
import sys

import config
from client import API, BiwengerClient

# El verbo es una constante: cambiarlo requiere editar el fichero a mano.
HTTP_METHOD = "GET"

PATH_TEMPLATE = "/owners/player/{player_id}/clause"


def parse_args(argv):
    p = argparse.ArgumentParser(description="Sonda read-only de clausula (1 GET)")
    p.add_argument("player_id", nargs="?", type=int, help="id del jugador a consultar")
    p.add_argument("--dry-run", action="store_true", help="mostrar la URL y salir")
    p.add_argument(
        "--mine",
        action="store_true",
        help="listar ids de mi plantilla desde .\\data\\ (sin red) y salir",
    )
    p.add_argument("--save", action="store_true", help="guardar la respuesta en .\\data\\")
    return p.parse_args(argv)


def list_mine():
    """Lee los ids de mi plantilla del volcado ya guardado. No toca la red."""
    path = os.path.join("data", "explore-user_self_players.json")
    if not os.path.exists(path):
        print(f"No existe {path}. Corre antes: py explore.py --only=user_self_players")
        return 1
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    data = payload.get("data", payload)
    ids = [p["id"] for p in data.get("players", []) if isinstance(p, dict) and "id" in p]
    print(f"Ids de mi plantilla ({len(ids)}): {ids}")
    print("\nEmpieza por uno tuyo: el registro de propiedad existe con seguridad,")
    print("asi que un 404 no sera ambiguo. Luego prueba uno de un rival.")
    return 0


def describe(payload):
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if isinstance(data, dict):
        print(f"  claves: {sorted(data.keys())}")
        for k, v in sorted(data.items()):
            if isinstance(v, (dict, list)):
                print(f"    {k}: {type(v).__name__} de {len(v)}")
            else:
                print(f"    {k}: {type(v).__name__} = {v!r}")
    elif isinstance(data, list):
        print(f"  lista de {len(data)} elementos")
        if data:
            print(f"  primer elemento: {data[0]!r}")
    else:
        print(f"  {type(data).__name__} = {data!r}")


def interpret(status):
    if status == 200:
        return "LEIBLE. Se pueden leer clausulas ya, sin esperar al dia 7."
    if status in (401, 403):
        return f"{status}: prohibido leer. Pendiente para post-dia-7; pasamos a Fase 2."
    if status == 405:
        return "405: el endpoint solo acepta PUT. No es leible. Pasamos a Fase 2."
    if status == 404:
        return "404: la ruta no existe para este id (revisa el id o la ruta exacta)."
    if status == 400:
        return "400: peticion rechazada; mira el mensaje del cuerpo."
    return f"{status}: inesperado, ver cuerpo."


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.mine:
        return list_mine()

    if args.player_id is None:
        print("Falta el id del jugador. Prueba: py probe_clause.py --mine", file=sys.stderr)
        return 2

    path = PATH_TEMPLATE.format(player_id=args.player_id)
    print("=" * 74)
    print("SONDA READ-ONLY DE CLAUSULA")
    print(f"  {HTTP_METHOD} {API}{path}")
    print("  peticiones a realizar: 1")
    print("=" * 74)

    if args.dry_run:
        print("\n--dry-run: no se ha hecho ninguna peticion.")
        return 0

    # Cinturon y tirantes: si alguien cambia la constante, no sale a la red.
    if HTTP_METHOD != "GET":
        print(f"ABORTADO: HTTP_METHOD es {HTTP_METHOD!r}, solo se permite GET",
              file=sys.stderr)
        return 1

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"ERROR de configuracion:\n{exc}", file=sys.stderr)
        return 2

    client = BiwengerClient(cfg)
    status, payload = client.probe(path)  # probe() solo usa session.get

    print()
    if status is None:
        print(f"Fallo de red: {payload}")
        return 4

    print(f"HTTP {status} -> {interpret(status)}")
    print()

    if isinstance(payload, dict) or isinstance(payload, list):
        describe(payload)
        if args.save:
            os.makedirs("data", exist_ok=True)
            out = os.path.join("data", f"probe-clause-{args.player_id}.json")
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            print(f"\n  guardado: {out}")
    else:
        print(f"  cuerpo no JSON: {str(payload)[:400]!r}")

    print("\nNada fue modificado: la unica peticion fue un GET.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
