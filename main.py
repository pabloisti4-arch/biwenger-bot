"""Fase 1: verificar conexion con Biwenger e imprimir el mercado del dia.

Modo propuesta: este script solo lee. No puja, no vende, no acepta ofertas.

Uso:
    python main.py
    python main.py --raw          # volcar el JSON crudo por pantalla
    python main.py --save         # guardar el JSON crudo en ./data/
    python main.py --sort=diff    # ordenar por sobreprecio en vez de por precio
"""

import argparse
import json
import os
import sys
from datetime import datetime

import config
from client import AuthError, BiwengerClient, BiwengerError, VersionError
from market import euros, index_players, normalize


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Biwenger - Fase 1 (solo lectura)")
    parser.add_argument("--raw", action="store_true", help="imprimir JSON crudo")
    parser.add_argument("--save", action="store_true", help="guardar JSON crudo en ./data/")
    parser.add_argument(
        "--sort",
        choices=("price", "diff", "name", "points"),
        default="price",
        help="criterio de orden de la tabla (por defecto: price)",
    )
    return parser.parse_args(argv)


def print_table(rows, sort_key):
    if not rows:
        print("  (el mercado esta vacio o la respuesta no tenia el formato esperado;")
        print("   ejecuta con --raw para ver el JSON tal cual)")
        return

    keyfuncs = {
        "price": lambda r: -(r["market_price"] or 0),
        "diff": lambda r: -(r["diff_pct"] if r["diff_pct"] is not None else -9e9),
        "name": lambda r: (r["name"] or "").lower(),
        "points": lambda r: -(r["points"] or 0),
    }
    rows = sorted(rows, key=keyfuncs[sort_key])

    header = f"{'JUGADOR':<24}{'POS':<5}{'EQUIPO':<16}{'PRECIO MERCADO':>16}{'VALOR':>14}{'DIF%':>8}{'PTS':>6}  VENDEDOR"
    print(header)
    print("-" * len(header))
    for r in rows:
        diff = f"{r['diff_pct']:+.1f}" if r["diff_pct"] is not None else "-"
        pts = r["points"] if r["points"] is not None else "-"
        print(
            f"{r['name'][:23]:<24}"
            f"{r['position']:<5}"
            f"{str(r['team'])[:15]:<16}"
            f"{euros(r['market_price']):>16}"
            f"{euros(r['base_price']):>14}"
            f"{diff:>8}"
            f"{str(pts):>6}"
            f"  {r['seller']}"
        )
    print("-" * len(header))
    print(f"  {len(rows)} jugadores en el mercado")
    libres = sum(1 for r in rows if r["seller"] == "MERCADO LIBRE")
    print(f"  {libres} de mercado libre / {len(rows) - libres} puestos por managers")


def summarize_account(data):
    if not isinstance(data, dict):
        print(f"  respuesta inesperada: {type(data).__name__}")
        return
    account = data.get("account") if isinstance(data.get("account"), dict) else data
    name = account.get("name") or account.get("email") or "?"
    print(f"  Cuenta: {name}  (id={account.get('id', '?')})")

    leagues = data.get("leagues") or account.get("leagues") or []
    if isinstance(leagues, list):
        for lg in leagues:
            if not isinstance(lg, dict):
                continue
            user = lg.get("user") if isinstance(lg.get("user"), dict) else {}
            balance = user.get("balance")
            marker = " <-- X-League" if str(lg.get("id")) == os.environ.get(
                "BIWENGER_LEAGUE", ""
            ).strip() else ""
            line = f"  Liga: {lg.get('name', '?')} (id={lg.get('id', '?')})"
            if balance is not None:
                line += f"  saldo={euros(balance)}"
            print(line + marker)


def save_raw(name, payload):
    os.makedirs("data", exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join("data", f"{stamp}-{name}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"  guardado: {path}")
    return path


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"ERROR de configuracion:\n{exc}", file=sys.stderr)
        return 2

    print("=" * 90)
    print("BIWENGER - FASE 1 (modo propuesta: solo lectura)")
    print("=" * 90)
    print(f"  Liga (X-League): {cfg['league']}")
    print(f"  Usuario (X-User): {cfg['user']}")
    print(f"  Token: {config.mask(cfg['token'])}")
    print(f"  Competicion: {cfg['competition']}")
    print(f"  X-Version: {cfg['version']}")
    print()

    client = BiwengerClient(cfg)

    try:
        print("[1/3] GET /account ...")
        account = client.account()
        summarize_account(account)
        if args.save:
            save_raw("account", account)
        if args.raw:
            print(json.dumps(account, ensure_ascii=False, indent=2)[:4000])
        print()

        print("[2/3] GET /market ...")
        market_data = client.market()
        if isinstance(market_data, dict):
            print(f"  claves de la respuesta: {sorted(market_data.keys())}")
        if args.save:
            save_raw("market", market_data)
        if args.raw:
            print(json.dumps(market_data, ensure_ascii=False, indent=2)[:8000])
        print()

        print(f"[3/3] GET dataset de jugadores ({cfg['competition']}) ...")
        players_data = client.players()
        players_by_id, team_names = index_players(players_data)
        print(f"  {len(players_by_id)} jugadores, {len(team_names)} equipos indexados")
        if args.save:
            save_raw("players", players_data)
        print()

    except VersionError as exc:
        print(f"ERROR de version:\n{exc}", file=sys.stderr)
        return 5
    except AuthError as exc:
        print(f"ERROR de autenticacion:\n{exc}", file=sys.stderr)
        return 3
    except BiwengerError as exc:
        print(f"ERROR de API:\n{exc}", file=sys.stderr)
        return 4

    print("=" * 90)
    print(f"MERCADO DEL DIA - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 90)
    print_table(normalize(market_data, players_by_id, team_names), args.sort)
    print()
    print("OK: la conexion funciona. Fase 1 completada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
