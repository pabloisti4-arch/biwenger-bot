"""Prueba de humo sin credenciales: cruce, formato de tabla y cabeceras.

No toca la red. Borrable cuando la Fase 1 este verificada contra la API real.
"""

import json
import os

import config
from client import API, BiwengerClient, BiwengerError, VersionError
from main import print_table
from market import index_players, normalize

PLAYERS = {
    "players": {
        "1": {"id": 1, "name": "Lewandowski", "position": 4, "price": 45000000,
              "points": 180, "teamID": 10, "fitness": [10, 6, 2]},
        "2": {"id": 2, "name": "Nico Williams", "position": 3, "price": 22000000,
              "points": 95, "teamID": 11},
        "3": {"id": 3, "name": "Unai Simon", "position": 1, "price": 8000000,
              "points": 60, "teamID": 11},
    },
    "teams": {
        "10": {"id": 10, "name": "Barcelona"},
        "11": {"id": 11, "name": "Athletic"},
    },
}

# Tres formas distintas del campo `player` / `user` a proposito.
MARKET = {
    "sales": [
        {"id": 901, "player": 1, "price": 52000000,
         "user": {"id": 7, "name": "Pepe"}, "until": 1786000000},
        {"id": 902, "player": {"id": 2}, "price": 19500000,
         "user": None, "until": 1786000000},
        {"id": 903, "player": 3, "price": 8000000, "user": 42, "until": 1786000000},
        {"id": 904, "player": 9999, "price": 1000000, "user": None},  # id desconocido
    ]
}


class FakeResponse:
    """Respuesta minima con la interfaz que usa BiwengerClient._get."""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": "application/json"}

    def json(self):
        import json

        return json.loads(self.text)


OLD_VERSION_BODY = (
    '{"status":400,"message":"Old version",'
    '"userMessage":"\\u00a1Est\\u00e1s usando una versi\\u00f3n antigua!"}'
)


def check_headers():
    """X-Version sale del entorno; el resto de cabeceras estan cableadas."""
    saved = os.environ.copy()
    try:
        os.environ.update(
            {
                "BIWENGER_TOKEN": "Bearer fake.token.value",
                "BIWENGER_LEAGUE": "111",
                "BIWENGER_USER": "222",
            }
        )
        os.environ.pop("BIWENGER_VERSION", None)
        cfg = config.load()
        assert cfg["version"] == "631", cfg["version"]
        # El prefijo "Bearer " pegado por error se limpia.
        assert cfg["token"] == "fake.token.value", cfg["token"]

        headers = BiwengerClient(cfg).session.headers
        assert headers["X-Version"] == "631", headers["X-Version"]
        assert headers["Authorization"] == "Bearer fake.token.value"
        assert headers["X-League"] == "111"
        assert headers["X-User"] == "222"

        # La variable de entorno manda sobre el defecto.
        os.environ["BIWENGER_VERSION"] = "999"
        cfg2 = config.load()
        client2 = BiwengerClient(cfg2)
        assert client2.session.headers["X-Version"] == "999"

        # La sesion publica NO lleva el token.
        assert "Authorization" not in client2.public.headers

        # Un 400 "Old version" se traduce a VersionError, no a un volcado crudo.
        client2.session.get = lambda *a, **k: FakeResponse(400, OLD_VERSION_BODY)
        try:
            client2.market()
        except VersionError as exc:
            assert "999" in str(exc), exc
            assert "BIWENGER_VERSION" in str(exc)
        else:
            raise AssertionError("se esperaba VersionError")

        # Un 400 cualquiera sigue siendo BiwengerError generico.
        client2.session.get = lambda *a, **k: FakeResponse(400, '{"status":400}')
        try:
            client2.market()
        except VersionError:
            raise AssertionError("no deberia ser VersionError")
        except BiwengerError:
            pass

        # Envoltorio {"status":200,"data":...} desempaquetado.
        client2.session.get = lambda *a, **k: FakeResponse(
            200, '{"status":200,"data":{"sales":[]}}'
        )
        assert client2.market() == {"sales": []}

        assert API == "https://biwenger.as.com/api/v2"
        assert "***" not in config.mask("")  # no revienta con vacio
        assert "fake.t" in config.mask("fake.token.value.longer.than.twelve")
    finally:
        os.environ.clear()
        os.environ.update(saved)
    print("cabeceras y errores OK")


def _write_history(directory, prices, player_id=1):
    """Crea fotos sinteticas: una por dia, con el precio dado."""
    os.makedirs(directory, exist_ok=True)
    for index, price in enumerate(prices, start=1):
        stamp = f"2026-08-{index:02d}"
        with open(os.path.join(directory, f"prices-{stamp}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"date": stamp, "prices": {str(player_id): price}}, fh)


def check_history_and_timing():
    """Valida la deteccion de pico sin esperar dias reales."""
    import tempfile

    from bidding import clause_advice, market_advice
    from history import PriceHistory
    from scoring import Signals

    player = {
        "id": 1, "name": "Test", "position": 3, "teamID": 99,
        "price": 1_000_000, "priceIncrement": 50_000, "status": "ok",
        "pointsLastSeason": 100,
    }
    players_data = {"players": {"1": player}, "teams": {}}

    casos = {
        # serie de precios          -> (etiqueta esperada, umbral de timing)
        "subiendo": ([1000, 1100, 1200, 1300], "esperar", 0.35),
        "techo": ([1000, 1100, 1300, 1300], "TECHO", 0.90),
        "caida": ([1300, 1200, 1100, 1000], "CAIDA", 0.75),
    }
    for nombre, (serie, marca, esperado) in casos.items():
        with tempfile.TemporaryDirectory() as tmp:
            _write_history(tmp, [p * 1000 for p in serie])
            hist = PriceHistory(tmp)
            assert hist.depth == 4, hist.depth
            signals = Signals(players_data, {}, history=hist)
            factor = signals.sell_timing(player)
            assert factor.score == esperado, (nombre, factor.score, factor.note)
            assert marca.lower() in factor.note.lower(), (nombre, factor.note)

    # Con una sola foto: prudencia, neutro, y la nota lo dice.
    with tempfile.TemporaryDirectory() as tmp:
        _write_history(tmp, [1_000_000])
        hist = PriceHistory(tmp)
        signals = Signals(players_data, {}, history=hist)
        factor = signals.sell_timing(player)
        assert factor.score == 0.5, factor.score
        assert "prudencia" in factor.note, factor.note
        info = signals.trend_info(player)
        assert info.source == "priceIncrement", info.source
        assert not info.reliable_shape

        # El rango de puja con tendencia amortiguada por falta de histórico.
        score = signals.score_buy(player, 1_000_000)
        flojo = market_advice(player, 1_000_000, score, info, 13_000_000,
                              19_800_000, alternatives=0)
        assert flojo.kind == "recomendado"
        assert flojo.low == 1_000_000, flojo.low
        assert flojo.high > flojo.low

    # Misma pendiente pero con histórico fiable: la tendencia pesa mas.
    with tempfile.TemporaryDirectory() as tmp:
        _write_history(tmp, [850_000, 900_000, 950_000, 1_000_000])
        hist = PriceHistory(tmp)
        signals = Signals(players_data, {}, history=hist)
        info = signals.trend_info(player)
        assert info.reliable_shape, info
        score = signals.score_buy(player, 1_000_000)
        fuerte = market_advice(player, 1_000_000, score, info, 13_000_000,
                               19_800_000, alternatives=0)
        assert fuerte.high > flojo.high, (fuerte.high, flojo.high)

    # Alternativas baratas => menos prima.
    con_alt = market_advice(player, 1_000_000, score, info, 13_000_000,
                            19_800_000, alternatives=3)
    assert con_alt.high < fuerte.high, (con_alt.high, fuerte.high)

    # Etiquetas de clausula: exacta vs estimada NO pueden confundirse.
    leida = clause_advice(945_000, 13_000_000, "api", estimated=900_000)
    assert leida.kind == "exacto"
    assert "EXACTO" in leida.headline
    estimada = clause_advice(945_000, 13_000_000, "estimada")
    assert estimada.kind == "estimado"
    assert "SIN CONFIRMAR" in estimada.headline
    assert any("NO leida de la API" in r for r in estimada.rationale)

    # Fuera de presupuesto.
    caro = clause_advice(20_000_000, 13_000_000, "api")
    assert caro.flag == "FUERA DE PRESUPUESTO"
    assert any("te faltan" in r for r in caro.rationale)

    print("histórico, timing de venta y pujas OK")


class NoClauseReader:
    """Nunca confirma: fuerza el uso de la estimacion local."""

    requests_made = 0
    budget_exhausted = False
    errors = {}

    def read(self, player_id):
        return None, None

    def summary(self):
        return "0 peticiones (stub)"


def check_clause_rankings():
    """Los caros de alto impacto tienen que llegar a la lista corta.

    Si el pre-filtro ordenara solo por eficiencia, los baratos coparian las
    plazas de confirmacion y el ranking por impacto quedaria vacio de
    jugadorazos. Este caso los hace disjuntos a proposito.
    """
    from detectors import find_clause_raids, squad_baseline
    from scoring import Signals

    def mk(pid, price, pts):
        return {
            "id": pid, "name": f"P{pid}", "position": 3, "teamID": 1,
            "price": price, "status": "ok", "pointsLastSeason": pts,
        }

    # 1 mio flojo, 10 rivales baratos y mediocres, 3 rivales caros y buenos.
    players = {"1": mk(1, 1_000_000, 10)}
    baratos = list(range(100, 110))
    caros = [200, 201, 202]
    for pid in baratos:
        players[str(pid)] = mk(pid, 200_000, 50)
    for pid in caros:
        players[str(pid)] = mk(pid, 5_000_000, 200)

    signals = Signals({"players": players, "teams": {}}, {})
    baseline = squad_baseline(signals, [1])
    assert 3 in baseline, baseline

    # Tope de confirmacion menor que el numero de baratos: sin union, los
    # caros no entrarian ni uno.
    efficiency, impact, stats = find_clause_raids(
        signals,
        {"rival": baratos + caros},
        baseline,
        balance=13_000_000,
        clause_reader=NoClauseReader(),
        confirm_limit=6,
        limit=8,
    )

    assert stats["candidatos"] == 13, stats
    assert stats["confirmados_por_api"] == 6, stats
    assert efficiency and impact

    # El ranking (a) lo lideran los baratos: mas mejora por millon.
    assert efficiency[0].player_id in baratos, efficiency[0].player_id
    # El ranking (b) lo lideran los caros: mas mejora absoluta.
    assert impact[0].player_id in caros, impact[0].player_id
    # Y el orden de cada uno es coherente con su criterio.
    assert [o.roi for o in efficiency] == sorted(
        [o.roi for o in efficiency], reverse=True
    )
    assert [o.improvement for o in impact] == sorted(
        [o.improvement for o in impact], reverse=True
    )
    # Ambos respetan el filtro de pagable.
    assert all(o.extra["pagable"] for o in efficiency + impact)

    # Un saldo que no llega a los caros los deja fuera de los dos rankings.
    eff2, imp2, _ = find_clause_raids(
        signals, {"rival": baratos + caros}, baseline,
        balance=1_000_000, clause_reader=NoClauseReader(),
        confirm_limit=6, limit=8,
    )
    assert all(o.player_id in baratos for o in eff2 + imp2), "caros no pagables"

    print("rankings de clausulazo (eficiencia vs impacto) OK")


def check_email_rendering():
    """El HTML y el texto no pueden divergir, y nada debe romperse al escapar."""
    from datetime import datetime

    from bidding import market_advice
    from mailer import build_message, subject_for
    from report import Report, Section, facts_for, render_html, render_text
    from scoring import Signals

    # Nombre con caracteres que hay que escapar: si se cuela crudo, rompe el HTML.
    player = {
        "id": 7, "name": 'O\'Neill & <Sons> "JR"', "position": 4, "teamID": 1,
        "price": 1_000_000, "priceIncrement": 20_000, "status": "ok",
        "pointsLastSeason": 120,
    }
    signals = Signals({"players": {"7": player}, "teams": {}}, {})
    score = signals.score_buy(player, 1_000_000)
    advice = market_advice(player, 1_000_000, score, signals.trend_info(player),
                           13_000_000, 19_800_000, alternatives=1)

    from detectors import Opportunity

    op = Opportunity(
        kind="chollo", player_id=7, name=player["name"], position="DEL",
        team="Team & Co", value=1_000_000, cost=1_000_000, score=score,
        improvement=0.12, advice=advice,
        extra={"vendedor": "MERCADO LIBRE", "hasta": 0, "alternativas": 1,
               "sustituiria_a": "Otro"},
    )

    report = Report(
        generated_at=datetime(2026, 8, 10, 9, 30),
        league_id="123", round_label="jornada J1",
        balance=13_070_000, max_bid=19_802_500, history_span="1 foto",
    )
    report.sections = [
        Section("chollo", "COMPRAS", [op], "vacio", note="nota"),
        Section("venta", "VENTAS", [], "nadie quiere salir"),
    ]
    report.caveats = ["advertencia con < y &"]
    report.stats = {"clausulas": "0 peticiones"}

    text = render_text(report)
    html_body = render_html(report)

    # Los dos renderizadores consumen los MISMOS hechos.
    data = facts_for("chollo", op)
    for detail in data["details"]:
        assert detail in text, detail

    # Escapado correcto: el nombre crudo no aparece, el escapado si.
    assert "<Sons>" not in html_body
    assert "&lt;Sons&gt;" in html_body
    assert "Team &amp; Co" in html_body
    assert "advertencia con &lt; y &amp;" in html_body
    assert html_body.count("<html") == 1
    assert html_body.rstrip().endswith("</html>")

    # La leyenda y el aviso de modo propuesta van en ambos formatos.
    assert "MODO PROPUESTA" in html_body
    assert "MODO PROPUESTA" in text.upper()
    for tag in ("EXACTO", "ESTIMADO", "RECOMENDADO"):
        assert tag in html_body, tag
        assert tag in text, tag

    # Seccion vacia: se dice, no se omite.
    assert "nadie quiere salir" in text
    assert "nadie quiere salir" in html_body

    # Asunto: sin claves internas y sin contar dos veces los clausulazos.
    report.sections.append(Section("clausulazo", "A", [op], ""))
    report.sections.append(Section("clausulazo_impacto", "B", [op], ""))
    subject = subject_for(report)
    assert "clausulazo_impacto" not in subject, subject
    assert subject.count("clausulazos") == 1, subject
    assert "1 compras" in subject and "1 clausulazos" in subject, subject
    assert len(subject) < 120, len(subject)

    # El mensaje se construye multiparte y el texto plano lleva el reporte entero.
    message = build_message(subject, text, html_body, "a@b.com", ["c@d.com", "e@f.com"])
    assert message["To"] == "c@d.com, e@f.com"
    assert message.is_multipart()
    subtypes = {part.get_content_subtype() for part in message.iter_parts()}
    assert subtypes == {"plain", "html"}, subtypes
    plain = [p for p in message.iter_parts() if p.get_content_subtype() == "plain"][0]
    assert "COMPRAS" in plain.get_content()

    print("render HTML/texto y construccion del correo OK")


def main():
    check_headers()
    check_history_and_timing()
    check_clause_rankings()
    check_email_rendering()

    players_by_id, team_names = index_players(PLAYERS)
    assert len(players_by_id) == 3, players_by_id
    assert team_names[11] == "Athletic"

    rows = normalize(MARKET, players_by_id, team_names)
    assert len(rows) == 4, rows

    by_id = {r["sale_id"]: r for r in rows}
    assert by_id[901]["name"] == "Lewandowski"
    assert by_id[901]["position"] == "DEL"
    assert round(by_id[901]["diff_pct"], 1) == 15.6      # 52M sobre 45M
    assert by_id[902]["seller"] == "MERCADO LIBRE"
    assert by_id[902]["name"] == "Nico Williams"          # player como objeto
    assert by_id[903]["seller"] == "user:42"              # user como int
    assert by_id[904]["name"] == "<id 9999>"              # jugador no encontrado
    assert by_id[904]["diff_pct"] is None                 # sin valor base

    print("asserts OK\n")
    for key in ("price", "diff", "points", "name"):
        print(f"--- orden: {key} ---")
        print_table(rows, key)
        print()

    print("--- mercado vacio ---")
    print_table(normalize({}, players_by_id, team_names), "price")


if __name__ == "__main__":
    main()
