"""Motor de puntuacion de oportunidades.

Cada factor devuelve un Factor(score, note) con score en [0,1] o None si no hay
dato. El compuesto renormaliza los pesos sobre los factores que SI tienen dato y
reporta la fraccion de peso cubierta como `confidence`. Asi una oportunidad
puntuada solo con la mitad de las senales no se disfraza de certeza.
"""

import bisect
from dataclasses import dataclass, field

from history import MIN_DAYS_FOR_SHAPE, trend_from_increment

POSITIONS = {1: "POR", 2: "DEF", 3: "MED", 4: "DEL", 5: "ENT"}

# Confirmado empiricamente: clausula = 1.5 x price.
#   Moi Gomez  630.000 -> 945.000
#   De Jong  1.050.000 -> 1.575.000
# Cruza el millon, asi que no hay umbral. SOLO valido para jugadores nunca
# traspasados: clauseIncrement=2 la modifica tras un fichaje.
CLAUSE_MULTIPLIER = 1.5

# Orden de prioridad pedido: rendimiento > tendencia > precio > calendario.
WEIGHTS = {"performance": 0.40, "trend": 0.25, "price": 0.20, "fixtures": 0.15}

STATUS_PENALTY = {
    "ok": 1.00,
    "doubt": 0.70,
    "sanctioned": 0.40,
    "injured": 0.25,
    "discarded": 0.15,
    "unknown": 0.60,
}

# Puntuacion neutra hacia la que se encoge lo que tiene poca confianza.
NEUTRAL = 0.5

# Subida diaria (fraccion del valor) que satura el factor tendencia.
TREND_FULL_SCALE = 0.05
# Descuento sobre el valor que satura el factor precio.
PRICE_FULL_SCALE = 0.30


def clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def euros(value):
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:,.0f}".replace(",", ".")


@dataclass
class Factor:
    score: float | None
    note: str


@dataclass
class Score:
    value: float
    confidence: float
    penalty: float
    factors: dict = field(default_factory=dict)

    @property
    def final(self):
        """Puntuacion encogida hacia la neutra segun la confianza.

        Sin esto, un jugador sin historial puntuado solo por tendencia compite
        de tu a tu con uno que tiene las cuatro senales, y los 164 novatos del
        dataset se colaban arriba con una subida de precio de un dia. El
        encogimiento hace que la falta de datos cueste posiciones en vez de
        salir gratis.
        """
        shrunk = self.confidence * self.value + (1 - self.confidence) * NEUTRAL
        return shrunk * self.penalty

    def why(self, limit=4):
        """Explicacion legible de los factores con dato, mejor primero."""
        rated = [(n, f) for n, f in self.factors.items() if f.score is not None]
        rated.sort(key=lambda kv: -kv[1].score)
        parts = [f"{n}={f.score:.2f} ({f.note})" for n, f in rated[:limit]]
        missing = [n for n, f in self.factors.items() if f.score is None]
        if missing:
            parts.append("sin dato: " + ",".join(missing))
        return " | ".join(parts)


class Signals:
    """Indices y factores derivados del dataset de competicion."""

    def __init__(self, players_data, league_settings=None, history=None):
        raw = players_data or {}
        self.players = {int(k): v for k, v in (raw.get("players") or {}).items()}
        self.teams = {int(k): v for k, v in (raw.get("teams") or {}).items()}
        self.settings = league_settings or {}
        self.history = history

        # Los entrenadores no puntuan si la liga no los usa.
        self.skip_positions = set()
        if not self.settings.get("lineupCoach", False):
            self.skip_positions.add(5)

        self._points_by_pos = {}
        for player in self.players.values():
            pts = player.get("pointsLastSeason") or 0
            if pts > 0:
                self._points_by_pos.setdefault(player.get("position"), []).append(pts)
        for table in self._points_by_pos.values():
            table.sort()

    # -- utilidades ------------------------------------------------------

    def player(self, player_id):
        return self.players.get(int(player_id))

    def team_name(self, team_id):
        team = self.teams.get(int(team_id)) if team_id else None
        return (team or {}).get("name") or "?"

    def label(self, player_id):
        p = self.player(player_id)
        if not p:
            return f"<id {player_id}>"
        return p.get("name") or f"<id {player_id}>"

    def playable(self, player):
        return player and player.get("position") not in self.skip_positions

    def estimated_clause(self, player):
        price = (player or {}).get("price") or 0
        return int(price * CLAUSE_MULTIPLIER)

    # -- factores --------------------------------------------------------

    def performance(self, player):
        pts = player.get("pointsLastSeason") or 0
        if pts <= 0:
            return Factor(None, "sin historial de temporada pasada")
        table = self._points_by_pos.get(player.get("position")) or []
        if not table:
            return Factor(None, "sin tabla de su posicion")
        pct = bisect.bisect_left(table, pts) / len(table)
        pos = POSITIONS.get(player.get("position"), "?")
        return Factor(pct, f"{pts} pts el ano pasado, percentil {pct * 100:.0f} en {pos}")

    def trend_info(self, player):
        """Tendencia real si hay histórico; si no, el incremento diario."""
        if self.history is not None:
            return self.history.trend(player.get("id"), player)
        return trend_from_increment(player)

    def trend(self, player):
        if not (player.get("price") or 0):
            return Factor(None, "sin valor de mercado")
        info = self.trend_info(player)
        if info.slope_pct is None:
            return Factor(None, info.note)
        score = 0.5 + clamp(info.slope_pct / TREND_FULL_SCALE) / 2
        return Factor(score, info.note)

    def sell_timing(self, player):
        """Momento de venta. Prudente mientras no haya forma de curva fiable.

        Con una o dos fotos NO se puede distinguir "subiendo" de "ha tocado
        techo", y premiar al que mas sube seria justo el error contrario:
        vender en plena escalada. Asi que devuelve neutro y deja que decidan
        rendimiento y calendario.
        """
        info = self.trend_info(player)
        if not info.reliable_shape:
            return Factor(
                0.5,
                f"neutro por prudencia: {info.note} (hacen falta "
                f"{MIN_DAYS_FOR_SHAPE} fotos para saber si esta en pico)",
            )
        if info.turning:
            return Factor(0.90, f"TECHO: {info.note} -> vender ahora")
        if info.falling:
            return Factor(0.75, f"CAIDA: {info.note} -> vender antes de perder mas")
        if info.at_peak:
            return Factor(0.35, f"maximo pero aun subiendo: {info.note} -> esperar")
        return Factor(0.50, f"lateral: {info.note}")

    def price_value(self, player, asking):
        base = player.get("price") or 0
        if not base or not asking:
            return Factor(None, "sin precio de referencia")
        discount = (base - asking) / base
        score = 0.5 + clamp(discount / PRICE_FULL_SCALE) / 2
        return Factor(
            score,
            f"piden {euros(asking)} vs valor {euros(base)} ({discount * 100:+.1f}%)",
        )

    def fixtures(self, player):
        """Dificultad del proximo partido de su equipo.

        SUPUESTO NO VALIDADO: rating alto = rival duro. El dataset da
        difficulty.rating 0-100 por lado del partido sin documentar el sentido.
        Es el factor de menor peso justamente por esto.
        """
        team_id = player.get("teamID") or player.get("teamId")
        team = self.teams.get(int(team_id)) if team_id else None
        games = (team or {}).get("nextGames") or []
        if not games:
            return Factor(None, "sin proximo partido")

        game = games[0]
        for side in ("home", "away"):
            node = game.get(side) or {}
            if node.get("id") == int(team_id):
                rating = (node.get("difficulty") or {}).get("rating")
                if not isinstance(rating, (int, float)):
                    return Factor(None, "partido sin rating de dificultad")
                donde = "en casa" if side == "home" else "fuera"
                return Factor(1 - rating / 100, f"dificultad {rating}/100 {donde}")
        return Factor(None, "su equipo no aparece en el partido")

    # -- compuestos ------------------------------------------------------

    def _combine(self, factors, weights):
        acc = 0.0
        covered = 0.0
        for name, factor in factors.items():
            weight = weights.get(name, 0.0)
            if factor.score is None or not weight:
                continue
            acc += weight * factor.score
            covered += weight
        total = sum(weights.get(n, 0.0) for n in factors)
        if not covered:
            return 0.0, 0.0
        return acc / covered, covered / total if total else 0.0

    def score_buy(self, player, asking=None):
        """Atractivo de fichar a este jugador a `asking` (o a su valor)."""
        factors = {
            "performance": self.performance(player),
            "trend": self.trend(player),
            "price": self.price_value(player, asking or player.get("price")),
            "fixtures": self.fixtures(player),
        }
        value, confidence = self._combine(factors, WEIGHTS)
        penalty = STATUS_PENALTY.get(player.get("status"), 0.6)
        return Score(value, confidence, penalty, factors)

    def score_sell(self, player):
        """Conveniencia de VENDER: rendimiento y calendario invertidos.

        Vender interesa cuando el jugador rinde poco, su valor ha tocado techo
        o cae, y el calendario aprieta. Ojo: una subida fuerte NO es senal de
        venta, es senal de esperar; eso lo resuelve sell_timing.
        """
        perf = self.performance(player)
        fix = self.fixtures(player)
        factors = {
            "performance": Factor(
                None if perf.score is None else 1 - perf.score,
                f"invertido: {perf.note}",
            ),
            "timing": self.sell_timing(player),
            "fixtures": Factor(
                None if fix.score is None else 1 - fix.score,
                f"invertido: {fix.note}",
            ),
        }
        weights = {"performance": 0.40, "timing": 0.35, "fixtures": 0.25}
        value, confidence = self._combine(factors, weights)
        # Un lesionado es MAS urgente de vender: la penalizacion no aplica aqui.
        return Score(value, confidence, 1.0, factors)

    def score_speculation(self, player, asking=None):
        """Compra para revender: manda la tendencia, el rendimiento pesa poco."""
        factors = {
            "trend": self.trend(player),
            "price": self.price_value(player, asking or player.get("price")),
            "performance": self.performance(player),
            "fixtures": self.fixtures(player),
        }
        weights = {"trend": 0.55, "price": 0.25, "performance": 0.15, "fixtures": 0.05}
        value, confidence = self._combine(factors, weights)
        penalty = STATUS_PENALTY.get(player.get("status"), 0.6)
        return Score(value, confidence, penalty, factors)
