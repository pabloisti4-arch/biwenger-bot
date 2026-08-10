"""Login programatico contra Biwenger.

=========================================================================
ESTE ES EL UNICO MODULO DEL PROYECTO QUE HACE UN POST, Y SOLO SIRVE PARA
AUTENTICARSE. Ninguna accion sobre la liga: ni pujar, ni vender, ni fichar,
ni ejecutar clausulas. La URL esta fijada en una constante y comprobada
antes de salir a la red.
=========================================================================

Sustituye el token capturado a mano: en cada ejecucion se cambia
email+contrasena por un token fresco, asi el bot no se cae cuando caduca.
Ademas deduce liga, usuario y sistema de puntuacion de /account, de modo que
no hay que mantener esos ids como secretos.
"""

import json

import requests

from client import API, USER_AGENT, BiwengerError

# Unico endpoint al que este modulo puede escribir.
LOGIN_URL = f"{API}/auth/login"
ACCOUNT_URL = f"{API}/account"

# Cualquier otra ruta esta prohibida aqui, por construccion.
ALLOWED_POST_URLS = frozenset({"https://biwenger.as.com/api/v2/auth/login"})


class LoginError(BiwengerError):
    pass


def _base_headers(version, lang):
    return {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "X-Version": str(version),
        "X-Lang": lang,
        "User-Agent": USER_AGENT,
        "Origin": "https://biwenger.as.com",
        "Referer": "https://biwenger.as.com/",
    }


def login(email, password, version, lang="es", timeout=20):
    """Cambia email+contrasena por un token. Devuelve el token en claro."""
    if LOGIN_URL not in ALLOWED_POST_URLS:
        raise LoginError(
            f"ABORTADO: {LOGIN_URL} no esta en la lista de POST permitidos. "
            "Este modulo solo puede autenticarse."
        )

    try:
        response = requests.post(
            LOGIN_URL,
            json={"email": email, "password": password},
            headers=_base_headers(version, lang),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise LoginError(f"Fallo de red en el login: {exc}") from exc

    if response.status_code in (400, 401, 403):
        raise LoginError(
            f"Biwenger rechazo el login (HTTP {response.status_code}).\n"
            "  - Revisa BIWENGER_EMAIL y BIWENGER_PASSWORD.\n"
            "  - Si tu cuenta entra con Google/Apple y no tiene contrasena "
            "propia, este login no funcionara: usa el modo token.\n"
            f"  respuesta: {response.text[:200]}"
        )
    if response.status_code >= 400:
        raise LoginError(
            f"HTTP {response.status_code} en el login: {response.text[:300]}"
        )

    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise LoginError(
            f"La respuesta del login no es JSON: {response.text[:200]}"
        ) from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    token = None
    for source in (data, payload):
        if isinstance(source, dict):
            candidate = source.get("token") or source.get("accessToken")
            if isinstance(candidate, str) and candidate:
                token = candidate
                break
    if not token:
        keys = sorted(payload.keys()) if isinstance(payload, dict) else type(payload)
        raise LoginError(
            f"El login respondio 200 pero sin token reconocible. Claves: {keys}"
        )

    if token.lower().startswith("bearer "):
        token = token[len("bearer ") :].strip()
    return token


def fetch_account(token, version, lang, timeout=20):
    """GET /account con el token recien obtenido. Solo lectura."""
    headers = _base_headers(version, lang)
    headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.get(ACCOUNT_URL, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise LoginError(f"Fallo de red pidiendo /account: {exc}") from exc

    if response.status_code >= 400:
        raise LoginError(
            f"HTTP {response.status_code} en /account tras el login: "
            f"{response.text[:300]}"
        )
    payload = response.json()
    return payload.get("data", payload) if isinstance(payload, dict) else payload


def _pick_league(account, wanted_league):
    leagues = (account or {}).get("leagues") or []
    if not isinstance(leagues, list) or not leagues:
        raise LoginError(
            "La cuenta no devolvio ninguna liga. Sin liga no hay analisis posible."
        )

    if wanted_league:
        for league in leagues:
            if isinstance(league, dict) and str(league.get("id")) == str(wanted_league):
                return league, ""
        disponibles = [str(l.get("id")) for l in leagues if isinstance(l, dict)]
        raise LoginError(
            f"BIWENGER_LEAGUE={wanted_league} no esta entre tus ligas "
            f"({', '.join(disponibles)})."
        )

    chosen = leagues[0]
    aviso = ""
    if len(leagues) > 1:
        aviso = (
            f"la cuenta tiene {len(leagues)} ligas y se ha elegido la primera "
            f"(id={chosen.get('id')}). Fija BIWENGER_LEAGUE si quieres otra."
        )
    return chosen, aviso


def bootstrap(cfg, timeout=20, verbose=True):
    """Deja cfg listo para BiwengerClient: token, liga, usuario y score.

    En modo token no toca nada. En modo password hace login (1 POST) y una
    lectura de /account (1 GET).
    """
    if cfg.get("mode") != "password":
        return cfg, []

    avisos = []
    if verbose:
        print(f"  POST /auth/login como {cfg['email']} ...")

    token = login(cfg["email"], cfg["password"], cfg["version"], cfg["lang"], timeout)
    if verbose:
        from config import mask

        print(f"  token obtenido: {mask(token)}")

    account = fetch_account(token, cfg["version"], cfg["lang"], timeout)
    league, aviso = _pick_league(account, cfg.get("league"))
    if aviso:
        avisos.append(aviso)

    resolved = dict(cfg)
    resolved["token"] = token
    resolved["league"] = str(league.get("id") or "")
    resolved["user"] = str(((league.get("user") or {}).get("id")) or cfg.get("user") or "")
    resolved["competition"] = league.get("competition") or cfg["competition"]

    # El sistema de puntuacion de la liga: usarlo evita analizar con puntos que
    # no son los tuyos. Solo se deduce si no lo fijaste a mano.
    from config import score_was_explicit

    score_id = league.get("scoreID")
    if score_id and not score_was_explicit():
        if str(score_id) != str(cfg["score"]):
            avisos.append(
                f"scoreID de la liga = {score_id}; se usa ese en lugar del "
                f"defecto {cfg['score']}."
            )
        resolved["score"] = str(score_id)

    if not resolved["user"]:
        raise LoginError(
            "No se pudo deducir tu id de usuario en la liga desde /account. "
            "Define BIWENGER_USER a mano."
        )

    if verbose:
        print(f"  liga={resolved['league']}  usuario={resolved['user']}  "
              f"score={resolved['score']}  competicion={resolved['competition']}")
    return resolved, avisos
