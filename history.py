"""Historico de precios: una foto diaria del dataset de competicion.

Cuesta CERO peticiones: el dataset completo ya se descarga en cada ejecucion,
asi que solo hay que persistirlo. Un fichero por dia en data\\history\\, con lo
minimo para reconstruir la serie.

Con 1 foto no hay tendencia real y se cae al `priceIncrement` que da la API.
Con 3 o mas se puede distinguir "sigue subiendo" de "ha tocado techo", que es
lo que de verdad hace falta para decidir cuando vender.
"""

import glob
import json
import os
from dataclasses import dataclass
from datetime import date, datetime

HISTORY_DIR = os.path.join("data", "history")

# Fotos minimas para fiarse de la forma de la curva y no solo de su pendiente.
MIN_DAYS_FOR_SHAPE = 3
DEFAULT_WINDOW = 7


def euros(value):
    if not isinstance(value, (int, float)):
        return "-"
    return f"{value:,.0f}".replace(",", ".")


@dataclass
class Trend:
    days: int
    slope: float | None          # euros/dia de media en la ventana
    slope_pct: float | None      # esa pendiente como fraccion del valor actual
    change_1d: int | None
    at_peak: bool | None
    turning: bool | None         # venia subiendo y ha dejado de hacerlo
    falling: bool | None
    source: str                  # historico | priceIncrement | sin dato
    note: str

    @property
    def reliable_shape(self):
        return self.source == "historico" and self.days >= MIN_DAYS_FOR_SHAPE


def trend_from_increment(player, days=0):
    """Fallback cuando no hay histórico util: el incremento diario de la API."""
    price = (player or {}).get("price") or 0
    inc = (player or {}).get("priceIncrement")
    if not price or inc is None:
        return Trend(days, None, None, None, None, None, None, "sin dato",
                     "sin histórico ni incremento diario")
    detalle = f"{days} foto(s) en histórico" if days else "sin histórico todavia"
    return Trend(
        days, float(inc), inc / price, int(inc), None, None, inc < 0,
        "priceIncrement",
        f"{euros(inc)}/dia segun la API ({inc / price * 100:+.2f}%); {detalle}",
    )


class PriceHistory:
    def __init__(self, directory=HISTORY_DIR):
        self.directory = directory
        self._days = []  # [(fecha_str, {player_id: price})] en orden
        self._load()

    # -- persistencia ----------------------------------------------------

    def _load(self):
        for path in sorted(glob.glob(os.path.join(self.directory, "prices-*.json"))):
            try:
                with open(path, encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (json.JSONDecodeError, OSError):
                continue  # una foto corrupta no invalida la serie
            prices = payload.get("prices") or {}
            stamp = payload.get("date") or os.path.basename(path)
            self._days.append(
                (stamp, {int(k): v for k, v in prices.items() if v is not None})
            )
        self._days.sort(key=lambda kv: kv[0])

    def record(self, players_data, today=None):
        """Guarda la foto de hoy. Idempotente: repetir el dia la sobreescribe."""
        players = (players_data or {}).get("players") or {}
        if not players:
            return None, False

        stamp = today or date.today().isoformat()
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"prices-{stamp}.json")
        existed = os.path.exists(path)

        prices = {}
        increments = {}
        for pid, player in players.items():
            if not isinstance(player, dict):
                continue
            prices[str(int(pid))] = player.get("price") or 0
            increments[str(int(pid))] = player.get("priceIncrement") or 0

        payload = {
            "date": stamp,
            "captured": datetime.now().isoformat(timespec="seconds"),
            "prices": prices,
            "increments": increments,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

        # Refresca el indice en memoria para que la ejecucion actual lo vea.
        self._days = [(d, p) for d, p in self._days if d != stamp]
        self._days.append((stamp, {int(k): v for k, v in prices.items()}))
        self._days.sort(key=lambda kv: kv[0])
        return path, existed

    # -- consulta --------------------------------------------------------

    @property
    def depth(self):
        return len(self._days)

    @property
    def span(self):
        if not self._days:
            return "sin fotos"
        if len(self._days) == 1:
            return f"1 foto ({self._days[0][0]})"
        return f"{len(self._days)} fotos ({self._days[0][0]} a {self._days[-1][0]})"

    def series(self, player_id):
        pid = int(player_id)
        return [(stamp, prices[pid]) for stamp, prices in self._days if pid in prices]

    def trend(self, player_id, player=None, window=DEFAULT_WINDOW):
        series = self.series(player_id)[-window:]
        if len(series) < 2:
            return trend_from_increment(player, days=len(series))

        prices = [value for _, value in series]
        steps = len(prices) - 1
        slope = (prices[-1] - prices[0]) / steps
        current = prices[-1] or 1
        change_1d = prices[-1] - prices[-2]
        peak = max(prices)
        at_peak = prices[-1] >= peak
        turning = slope > 0 and change_1d <= 0
        falling = slope < 0

        forma = []
        if turning:
            forma.append("techo")
        elif at_peak and slope > 0:
            forma.append("en maximo, aun subiendo")
        elif falling:
            forma.append("en caida")
        else:
            forma.append("lateral")

        return Trend(
            len(prices), slope, slope / current, int(change_1d), at_peak, turning,
            falling, "historico",
            f"{len(prices)} fotos: {euros(slope)}/dia de media "
            f"({slope / current * 100:+.2f}%), ultimo dia {euros(change_1d)}, "
            f"{forma[0]}",
        )
