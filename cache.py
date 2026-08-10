"""Cache en disco con TTL y lector de clausulas con limite de peticiones.

Leer la clausula de todos los jugadores de todos los rivales serian ~15 x N
peticiones por ejecucion. Aqui se combinan tres frenos:

  1. Cache en disco con TTL: una clausula leida hoy no se vuelve a pedir.
  2. Pausa entre peticiones (delay).
  3. Presupuesto duro de peticiones por ejecucion (max_requests).

Y sobre todo, en detectors.py se filtra ANTES de pedir: la clausula se estima
en local como 1.5 x valor y solo se confirma por API la de las candidatas que
sobreviven al filtro. Eso baja de ~120 peticiones a la decena.
"""

import json
import os
import time


class TTLCache:
    """Diccionario persistente donde cada entrada caduca."""

    def __init__(self, path, ttl_seconds):
        self.path = path
        self.ttl = ttl_seconds
        self._data = {}
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                self._data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Una cache corrupta no debe tumbar el analisis.
            self._data = {}

    def get(self, key):
        entry = self._data.get(str(key))
        if not isinstance(entry, dict):
            self.misses += 1
            return None
        age = time.time() - entry.get("ts", 0)
        if age > self.ttl:
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("value")

    def set(self, key, value):
        self._data[str(key)] = {"value": value, "ts": time.time()}
        self._dirty = True

    def save(self):
        if not self._dirty:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)
        self._dirty = False

    def __len__(self):
        return len(self._data)


class ClauseReader:
    """Lee GET /owners/player/{id}/clause con cache, pausa y presupuesto.

    SOLO LECTURA: usa client.probe(), que unicamente hace session.get.
    """

    def __init__(self, client, cache, delay=1.2, max_requests=40, verbose=True):
        self.client = client
        self.cache = cache
        self.delay = delay
        self.max_requests = max_requests
        self.verbose = verbose
        self.requests_made = 0
        self.budget_exhausted = False
        self.errors = {}
        self._last_call = 0.0

    def read(self, player_id):
        """Devuelve (valor, origen) donde origen es cache|api|None."""
        cached = self.cache.get(player_id)
        if cached is not None:
            return cached, "cache"

        if self.requests_made >= self.max_requests:
            self.budget_exhausted = True
            return None, None

        # Pausa medida desde la ultima llamada real, no un sleep ciego.
        elapsed = time.time() - self._last_call
        if self._last_call and elapsed < self.delay:
            time.sleep(self.delay - elapsed)

        status, payload = self.client.probe(f"/owners/player/{player_id}/clause")
        self._last_call = time.time()
        self.requests_made += 1

        value = self._extract(status, payload)
        if value is None:
            self.errors[player_id] = status
            if self.verbose:
                print(f"    clausula {player_id}: HTTP {status} sin valor legible")
            return None, None

        self.cache.set(player_id, value)
        return value, "api"

    @staticmethod
    def _extract(status, payload):
        """La respuesta observada es un entero desnudo, pero toleramos sobres."""
        if status != 200:
            return None
        data = payload
        if isinstance(data, dict):
            if "data" in data:
                data = data["data"]
            if isinstance(data, dict):
                for key in ("clause", "value", "amount", "price"):
                    if isinstance(data.get(key), (int, float)):
                        return int(data[key])
                return None
        if isinstance(data, (int, float)):
            return int(data)
        if isinstance(data, str) and data.strip().isdigit():
            return int(data.strip())
        return None

    def summary(self):
        parts = [
            f"{self.requests_made} peticiones de clausula",
            f"{self.cache.hits} desde cache",
        ]
        if self.budget_exhausted:
            parts.append(f"PRESUPUESTO AGOTADO (limite {self.max_requests})")
        if self.errors:
            parts.append(f"{len(self.errors)} sin leer")
        return ", ".join(parts)
