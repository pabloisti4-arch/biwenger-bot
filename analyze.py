"""Fase 2: motor de analisis y reporte diario. MODO PROPUESTA.

Solo GET. No puja, no vende, no ficha, no ejecuta clausulas.

Uso:
    py analyze.py --offline          # con los volcados de .\\data\\, sin red
    py analyze.py                    # en vivo contra la API
    py analyze.py --save-report      # guarda el reporte en .\\reports\\
    py analyze.py --max-clause-requests=15 --delay=1.5
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, timedelta

import config
from auth import LoginError, bootstrap
from cache import ClauseReader, TTLCache
from client import AuthError, BiwengerClient, BiwengerError, VersionError
from detectors import (
    find_bargains,
    find_clause_raids,
    find_sales,
    find_speculation,
    market_price_stats,
    squad_baseline,
)
from history import MIN_DAYS_FOR_SHAPE, PriceHistory
from mailer import MailError, build_message, send, subject_for
from market import index_players, normalize
from report import Report, Section, render_html, render_text
from scoring import CLAUSE_MULTIPLIER, Signals

CACHE_PATH = os.path.join("data", "cache", "clauses.json")
# Clausulas confirmadas a mano durante la exploracion, para el modo --offline.
KNOWN_CLAUSES = {1462: 945000, 18174: 1575000}


def parse_args(argv):
    p = argparse.ArgumentParser(description="Biwenger Fase 2 - analisis (solo lectura)")
    p.add_argument("--offline", action="store_true", help="usar volcados de .\\data\\")
    p.add_argument("--save-report", action="store_true", help="guardar en .\\reports\\")
    p.add_argument("--delay", type=float, default=1.2, help="pausa entre peticiones")
    p.add_argument("--max-clause-requests", type=int, default=25,
                   help="tope de lecturas de clausula por ejecucion")
    p.add_argument("--confirm-limit", type=int, default=12,
                   help="cuantas candidatas a clausulazo se confirman por API")
    p.add_argument("--cache-ttl", type=int, default=6 * 3600, help="TTL de cache (s)")
    p.add_argument("--unlock", default="", help="fecha fin de bloqueo inicial (YYYY-MM-DD)")
    p.add_argument("--no-record", action="store_true",
                   help="no guardar la foto de precios de hoy")
    p.add_argument("--email", action="store_true", help="enviar el reporte por correo")
    p.add_argument("--email-dry-run", action="store_true",
                   help="preparar el correo y mostrar el resumen SIN enviarlo")
    p.add_argument("--html-out", default="",
                   help="guardar el HTML en un fichero para verlo en el navegador")
    return p.parse_args(argv)


# -- carga offline -----------------------------------------------------------

def _unwrap(payload):
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def _load_json(pattern):
    paths = sorted(glob.glob(os.path.join("data", pattern)))
    paths = [p for p in paths if os.path.getsize(p) > 0]
    if not paths:
        return None
    with open(paths[-1], encoding="utf-8") as fh:
        return _unwrap(json.load(fh))


class OfflineSource:
    """Sirve los mismos datos que la API, leidos de .\\data\\."""

    def __init__(self):
        self.market = _load_json("*-market.json")
        self.players = _load_json("*-players.json")
        self.league = _load_json("explore-league_fields.json")
        self.my_squad = _load_json("explore-user_self_players.json")
        self.rival_squad = _load_json("explore-user_rival_players.json")

        missing = [n for n in ("market", "players", "league", "my_squad")
                   if getattr(self, n) is None]
        if missing:
            raise SystemExit(
                "Faltan volcados en .\\data\\ para el modo --offline: "
                + ", ".join(missing)
            )


class OfflineClauseReader:
    """No hace peticiones. Devuelve lo confirmado a mano y nada mas."""

    def __init__(self, known):
        self.known = dict(known)
        self.requests_made = 0
        self.budget_exhausted = False
        self.errors = {}

    def read(self, player_id):
        value = self.known.get(int(player_id))
        if value is None:
            return None, None
        return value, "confirmada-a-mano"

    def summary(self):
        return f"0 peticiones (offline), {len(self.known)} clausulas conocidas"


# -- carga en vivo -----------------------------------------------------------

def _throttle(last, delay):
    elapsed = time.time() - last
    if last and elapsed < delay:
        time.sleep(delay - elapsed)
    return time.time()


def fetch_live(client, cfg, delay):
    """Todas las lecturas necesarias, con pausa entre ellas."""
    last = 0.0

    print("  GET /market ...")
    market = client.market()
    last = time.time()

    print(f"  GET /league/{cfg['league']} ...")
    last = _throttle(last, delay)
    status, payload = client.probe(
        f"/league/{cfg['league']}", {"fields": "*,standings,settings"}
    )
    league = _unwrap(payload) if status == 200 else {}

    print("  GET dataset de jugadores (publico) ...")
    players = client.players()

    print(f"  GET /user/{cfg['user']} (mi plantilla) ...")
    last = _throttle(last, delay)
    status, payload = client.probe(
        f"/user/{cfg['user']}", {"fields": "*,players(*)"}
    )
    my_squad = _unwrap(payload) if status == 200 else {}

    rivals = {}
    for rival_id in _rival_ids(league, cfg["user"]):
        last = _throttle(last, delay)
        status, payload = client.probe(f"/user/{rival_id}", {"fields": "*,players(*)"})
        if status != 200:
            print(f"    rival {rival_id}: HTTP {status}, omitido")
            continue
        data = _unwrap(payload)
        rivals[rival_id] = [p["id"] for p in (data.get("players") or [])
                            if isinstance(p, dict) and "id" in p]
        print(f"    rival {rival_id}: {len(rivals[rival_id])} jugadores")

    return market, league, players, my_squad, rivals


def _rival_ids(league, own_user_id):
    """Ids de managers de la liga, excluyendo el propio."""
    found = set()

    def walk(node, depth=0):
        if depth > 6:
            return
        if isinstance(node, dict):
            uid = node.get("id")
            if uid is not None and "teamID" not in node and any(
                k in node for k in ("points", "position", "balance", "joinDate")
            ):
                try:
                    found.add(int(uid))
                except (TypeError, ValueError):
                    pass
            for value in node.values():
                walk(value, depth + 1)
        elif isinstance(node, list):
            for value in node:
                walk(value, depth + 1)

    walk(league)
    found.discard(int(own_user_id))
    return sorted(found)


# -- construccion del reporte ------------------------------------------------

def _round_label(players_data):
    rounds = ((players_data or {}).get("season") or {}).get("rounds") or []
    for rnd in rounds:
        if isinstance(rnd, dict) and rnd.get("status") == "pending":
            return f"jornada {rnd.get('short') or rnd.get('name') or '?'}"
    return "jornada desconocida"


def _history_caveats(history):
    """Advertencias sobre la profundidad del histórico, que crece cada dia."""
    depth = history.depth
    if depth == 0:
        return [
            "HISTORICO: no hay ninguna foto de precios. Tendencia y rango de puja "
            "usan solo el priceIncrement de hoy: son lo mas impreciso que van a ser.",
        ]
    if depth < MIN_DAYS_FOR_SHAPE:
        return [
            f"HISTORICO: {history.span}. No se puede distinguir 'sigue subiendo' "
            f"de 'ha tocado techo', asi que el detector de ventas ignora la "
            f"tendencia por prudencia. Hacen falta {MIN_DAYS_FOR_SHAPE} dias.",
            "HISTORICO: en el rango de puja la tendencia va amortiguada al 25% "
            "porque viene de un solo dia. Se ira ajustando sola conforme acumule "
            "fotos.",
        ]
    return [
        f"HISTORICO: {history.span}. La tendencia ya se calcula sobre la curva "
        "real, no sobre el incremento de un dia.",
    ]


def _caveats(signals, settings, unlock, clause_stats, rivals_count):
    total = len(signals.players)
    sin_historial = sum(
        1 for p in signals.players.values() if not (p.get("pointsLastSeason") or 0)
    )
    out = [
        "RENDIMIENTO: la temporada actual esta a 0 en los 555 jugadores "
        "(points, minutos y fitness vacios). El factor se calcula SOLO con "
        "pointsLastSeason.",
        f"{sin_historial} de {total} jugadores no tienen ni temporada pasada: "
        "para ellos el factor rendimiento no puntua y la confianza baja.",
        "CALENDARIO: el dataset trae 1 solo partido futuro por equipo, asi que el "
        "horizonte es una jornada.",
        "CALENDARIO: se asume que difficulty.rating alto = rival duro. Es un "
        "SUPUESTO NO VALIDADO; por eso es el factor de menor peso (15%).",
        f"CLAUSULAS: la estimacion local es {CLAUSE_MULTIPLIER}x el valor, "
        "confirmada en 2 jugadores. Solo vale para jugadores nunca traspasados: "
        f"clauseIncrement={settings.get('clauseIncrement', '?')} la altera tras un fichaje.",
        "SALDO RIVAL: settings.balance='hidden', asi que no se puede saber si un "
        "rival podria pagar tu clausula. El riesgo defensivo no se evalua.",
        "PRESUPUESTO: se usa 'puja maxima' para el mercado y 'saldo' para las "
        "clausulas, que se pagan en efectivo. Es una interpretacion, no algo "
        "documentado por Biwenger.",
    ]
    if rivals_count <= 1:
        out.append(
            f"COBERTURA: solo se analizo {rivals_count} plantilla rival. "
            "En vivo se recorren todas las de la liga."
        )
    if unlock:
        out.append(
            f"BLOQUEO INICIAL: con clauseActivationDelay="
            f"{settings.get('clauseActivationDelay', '?')} dias, los clausulazos no "
            f"son ejecutables hasta el {unlock}. Los objetivos se listan para "
            "preparar, no para hoy."
        )
    else:
        out.append(
            "BLOQUEO INICIAL: no se ha indicado la fecha de fin de bloqueo "
            "(--unlock=YYYY-MM-DD). Comprueba en la app si las clausulas ya "
            "son ejecutables."
        )
    return out


def build_report(args, market, league, players_data, my_squad, rivals, clause_reader,
                 league_id="(offline)", extra_caveats=()):
    settings = (league or {}).get("settings") or {}

    # La foto de precios es gratis: el dataset ya esta descargado.
    history = PriceHistory()
    if not args.no_record:
        path, existed = history.record(players_data)
        if path:
            verbo = "actualizada" if existed else "guardada"
            print(f"  foto de precios {verbo}: {path}  (histórico: {history.span})")

    signals = Signals(players_data, settings, history=history)

    players_by_id, team_names = index_players(players_data)
    sales = normalize(market, players_by_id, team_names)

    status = (market or {}).get("status") or {}
    balance = status.get("balance") or (my_squad or {}).get("balance") or 0
    max_bid = status.get("maximumBid") or balance

    my_ids = [p["id"] for p in ((my_squad or {}).get("players") or [])
              if isinstance(p, dict) and "id" in p]
    baseline = squad_baseline(signals, my_ids)

    bargains = find_bargains(signals, sales, baseline, balance, max_bid)
    to_sell = find_sales(signals, my_ids)
    specs = find_speculation(signals, sales, baseline, balance, max_bid)
    raids_eff, raids_impact, clause_stats = find_clause_raids(
        signals, rivals, baseline, balance, clause_reader,
        confirm_limit=args.confirm_limit,
    )

    # Honestidad sobre el titular: si nadie vende por debajo de su valor, esta
    # seccion NO esta encontrando chollos, esta ordenando por atractivo.
    pstats = market_price_stats(signals, sales)
    if pstats["por_debajo"] == 0:
        market_note = (
            f"SIN DESCUENTOS REALES HOY: de {pstats['total']} ventas, "
            f"{pstats['a_su_valor']} piden exactamente su valor y "
            f"{pstats['por_encima']} por encima. Ninguna por debajo. Esto ordena "
            "por relacion calidad/precio, no por descuento."
        )
    else:
        market_note = (
            f"{pstats['por_debajo']} de {pstats['total']} ventas piden por debajo "
            f"de su valor ({pstats['a_su_valor']} a su valor, "
            f"{pstats['por_encima']} por encima)."
        )

    report = Report(
        generated_at=datetime.now(),
        league_id=league_id,
        round_label=_round_label(players_data),
        balance=balance,
        max_bid=max_bid,
        history_span=history.span,
    )
    report.sections = [
        Section("chollo", "COMPRAS EN EL MERCADO: MEJOR RELACION CALIDAD/PRECIO",
                bargains, "nada asequible en el mercado hoy", note=market_note),
        Section("venta", "CANDIDATOS A VENTA DE TU PLANTILLA", to_sell,
                "ningun jugador tuyo pide salir"),
        Section("especulacion", "ESPECULACION POR TENDENCIA DE VALOR", specs,
                "nadie en el mercado sube de valor con fuerza"),
        Section(
            "clausulazo",
            "CLAUSULAZOS (a) TOP POR EFICIENCIA: mas mejora por millon",
            raids_eff,
            "ninguna clausula rival pagable que mejore tu plantilla",
            note=(
                f"{clause_stats['evaluados']} jugadores rivales evaluados, "
                f"{clause_stats['candidatos']} pasaron el filtro local, "
                f"{clause_stats['confirmados_por_api']} confirmados, "
                f"{clause_stats['pagables_tras_confirmar']} pagables"
                + (
                    f" ({clause_stats['no_pagables_tras_confirmar']} descartados al "
                    "confirmar la clausula real)"
                    if clause_stats["no_pagables_tras_confirmar"] else ""
                )
                + ". Gangas que tapan agujeros de plantilla."
            ),
        ),
        Section(
            "clausulazo_impacto",
            "CLAUSULAZOS (b) TOP POR IMPACTO: mas mejora absoluta",
            raids_impact,
            "ninguna clausula rival pagable que mejore tu plantilla",
            note=(
                "mismos candidatos pagables, ordenados por cuanto suben tu nivel "
                "sin penalizar el precio. Puede repetir jugadores del ranking (a): "
                "si alguien sale en los dos, es ganga Y jugadorazo."
            ),
        ),
    ]
    report.caveats = _caveats(signals, settings, args.unlock, clause_stats, len(rivals))
    report.caveats.extend(_history_caveats(history))
    report.caveats.extend(f"LOGIN: {aviso}" for aviso in extra_caveats)
    report.stats = {
        "clausulas": clause_reader.summary(),
        "descartados_por_saldo": clause_stats["descartados_por_saldo"],
        "descartados_sin_mejora": clause_stats["descartados_sin_mejora"],
        "jugadores_en_mercado": len(sales),
        "mi_plantilla": len(my_ids),
        "plantillas_rivales": len(rivals),
    }
    return report


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.offline:
        print("MODO OFFLINE: leyendo .\\data\\, sin ninguna peticion de red.")
        src = OfflineSource()
        rivals = {}
        if src.rival_squad:
            rival_id = src.rival_squad.get("id", "rival")
            rivals[rival_id] = [
                p["id"] for p in (src.rival_squad.get("players") or [])
                if isinstance(p, dict) and "id" in p
            ]
        clause_reader = OfflineClauseReader(KNOWN_CLAUSES)
        report = build_report(
            args, src.market, src.league, src.players, src.my_squad, rivals, clause_reader
        )
    else:
        try:
            cfg = config.load_auth()
        except config.ConfigError as exc:
            print(f"ERROR de configuracion:\n{exc}", file=sys.stderr)
            return 2

        print(f"AUTENTICACION: modo {cfg['mode']}")
        try:
            cfg, login_avisos = bootstrap(cfg)
        except LoginError as exc:
            print(f"ERROR de login:\n{exc}", file=sys.stderr)
            return 8

        client = BiwengerClient(cfg)
        cache = TTLCache(CACHE_PATH, args.cache_ttl)
        clause_reader = ClauseReader(
            client, cache, delay=args.delay, max_requests=args.max_clause_requests
        )
        try:
            market, league, players_data, my_squad, rivals = fetch_live(
                client, cfg, args.delay
            )
            report = build_report(
                args, market, league, players_data, my_squad, rivals, clause_reader,
                league_id=str(cfg["league"]), extra_caveats=login_avisos,
            )
        except VersionError as exc:
            print(f"ERROR de version:\n{exc}", file=sys.stderr)
            return 5
        except AuthError as exc:
            print(f"ERROR de autenticacion:\n{exc}", file=sys.stderr)
            return 3
        except BiwengerError as exc:
            print(f"ERROR de API:\n{exc}", file=sys.stderr)
            return 4
        finally:
            cache.save()

    text = render_text(report)
    print()
    print(text)

    if args.save_report:
        os.makedirs("reports", exist_ok=True)
        path = os.path.join(
            "reports", f"{report.generated_at.strftime('%Y%m%d-%H%M%S')}-reporte.txt"
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"\nguardado: {path}")

    if args.html_out:
        with open(args.html_out, "w", encoding="utf-8") as fh:
            fh.write(render_html(report))
        print(f"HTML guardado: {args.html_out}")

    if args.email or args.email_dry_run:
        return _deliver(args, report, text)

    return 0


def _deliver(args, report, text):
    """Prepara y (opcionalmente) envia el correo. El correo solo informa."""
    try:
        mail = config.load_mail()
    except config.ConfigError as exc:
        print(f"\nERROR de configuracion del correo:\n{exc}", file=sys.stderr)
        return 6

    subject = subject_for(report)
    message = build_message(
        subject=subject,
        text_body=text,
        html_body=render_html(report),
        sender=mail["sender"],
        recipients=mail["recipients"],
    )

    print()
    print("=" * 74)
    print("CORREO")
    print("=" * 74)
    print(f"  servidor: {mail['host']}:{mail['port']}")
    print(f"  de: {mail['sender']}")
    print(f"  para: {', '.join(mail['recipients'])}")
    print(f"  contrasena: {config.mask(mail['password'], keep=2)}")
    print(f"  asunto: {subject}")
    print(f"  partes: texto plano ({len(text)} chars) + HTML")

    if args.email_dry_run:
        print("\n--email-dry-run: NO se ha enviado nada.")
        return 0

    try:
        send(
            message,
            sender=mail["sender"],
            password=mail["password"],
            host=mail["host"],
            port=mail["port"],
        )
    except MailError as exc:
        print(f"\nERROR enviando el correo:\n{exc}", file=sys.stderr)
        return 7

    print("\nenviado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
