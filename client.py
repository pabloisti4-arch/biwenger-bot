"""Cliente HTTP minimo para la API interna de Biwenger.

Solo lectura (GET). El bot esta en modo propuesta: nunca escribe.
"""

import json

import requests

API = "https://biwenger.as.com/api/v2"
# Endpoint publico con el dataset de jugadores (no requiere autenticacion).
DATA_API = "https://cf.biwenger.com/api/v2"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class BiwengerError(RuntimeError):
    pass


class AuthError(BiwengerError):
    pass


class VersionError(BiwengerError):
    """Biwenger considera obsoleto el valor de X-Version que enviamos."""


class BiwengerClient:
    """Wrapper de requests con los headers que espera el frontend de Biwenger."""

    def __init__(self, cfg, timeout=20):
        self.cfg = cfg
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {cfg['token']}",
                "X-League": str(cfg["league"]),
                "X-User": str(cfg["user"]),
                "X-Lang": cfg["lang"],
                # Configurable: Biwenger lo incrementa con cada build del frontend.
                "X-Version": str(cfg["version"]),
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Origin": "https://biwenger.as.com",
                "Referer": "https://biwenger.as.com/",
            }
        )

        # Sesion aparte y SIN token para el endpoint publico de datos:
        # no hay razon para exponer la credencial a un host que no la necesita.
        self.public = requests.Session()
        self.public.headers.update(
            {"Accept": "application/json", "User-Agent": USER_AGENT}
        )

    # -- interno ---------------------------------------------------------

    def _get(self, session, url, params=None):
        try:
            resp = session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BiwengerError(f"Fallo de red pidiendo {url}: {exc}") from exc

        if resp.status_code in (401, 403):
            raise AuthError(
                f"{resp.status_code} en {url}. El token suele caducar: vuelve a "
                "capturarlo en DevTools y reescribe BIWENGER_TOKEN.\n"
                f"Respuesta: {resp.text[:300]}"
            )
        if resp.status_code == 400 and "old version" in resp.text.lower():
            raise VersionError(
                f"Biwenger rechaza X-Version={self.cfg['version']} por obsoleta.\n"
                "Captura el valor actual en DevTools > Network > cualquier peticion a\n"
                "api/v2 > Request Headers > x-version, y actualizalo:\n"
                '  [Environment]::SetEnvironmentVariable("BIWENGER_VERSION", "<nuevo>", "User")\n'
                "Luego abre una consola nueva.\n"
                f"Respuesta: {resp.text[:300]}"
            )
        if resp.status_code >= 400:
            raise BiwengerError(
                f"HTTP {resp.status_code} en {url}\nRespuesta: {resp.text[:500]}"
            )

        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise BiwengerError(
                f"La respuesta de {url} no es JSON (content-type="
                f"{resp.headers.get('content-type')}):\n{resp.text[:300]}"
            ) from exc

        # Biwenger envuelve todo en {"status": 200, "data": ...}.
        if isinstance(payload, dict) and "data" in payload:
            status = payload.get("status")
            if status is not None and int(status) >= 400:
                raise BiwengerError(f"La API devolvio status={status} en {url}: {payload}")
            return payload["data"]
        return payload

    # -- endpoints -------------------------------------------------------

    def probe(self, path, params=None, base=API):
        """GET crudo que NO lanza excepcion: devuelve (status, payload|texto).

        Para explorar endpoints candidatos sin que un 404 corte el barrido.
        """
        url = path if path.startswith("http") else f"{base}{path}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            return None, f"<error de red: {exc}>"
        try:
            return resp.status_code, resp.json()
        except json.JSONDecodeError:
            return resp.status_code, resp.text[:400]

    def account(self):
        """Datos de la cuenta y ligas. Sirve como comprobacion de que el token vive."""
        return self._get(self.session, f"{API}/account")

    def market(self):
        """Mercado del dia de la liga indicada en X-League."""
        return self._get(self.session, f"{API}/market")

    def players(self):
        """Dataset completo de jugadores de la competicion (publico)."""
        url = f"{DATA_API}/competitions/{self.cfg['competition']}/data"
        return self._get(
            self.public,
            url,
            params={"lang": self.cfg["lang"], "score": self.cfg["score"]},
        )
