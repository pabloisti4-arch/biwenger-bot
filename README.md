# Biwenger bot — modo propuesta

Bot de análisis para la liga de Biwenger. **Modo propuesta**: no ejecuta acciones
(no puja, no vende, no acepta ofertas). Solo lee, analiza y propone.

## Fases

| Fase | Estado | Contenido |
|------|--------|-----------|
| 1 | **hecha** | Login con token capturado + leer mercado del día e imprimirlo |
| 2 | **en curso** | Motor de análisis y reporte diario (`analyze.py`) |
| 3 | **hecha** | Envío del reporte por Gmail SMTP (contraseña de aplicación) |
| 4 | pendiente | GitHub Actions con cron diario + login programático |

## Regla de oro sobre credenciales

- **Nunca** pegues el token, la contraseña de Biwenger ni la contraseña de
  aplicación de Gmail en el chat ni en el código.
- Van siempre en variables de entorno (local) o en GitHub Secrets (Fase 4).
- `.gitignore` ya bloquea `.env` y `data/`.
- El token del Bearer **caduca**. Cuando el script dé un 401, recaptúralo.

## Instalación

```powershell
cd "$HOME\biwenger-bot"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Si `Activate.ps1` falla por política de ejecución:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## Variables de entorno en Windows PowerShell

### Opción A — persistentes, sin dejar rastro en el historial (recomendada)

`Read-Host` evita que el secreto quede escrito en
`ConsoleHost_history.txt` (que es lo que pasaría si tecleas `$env:X = "ey..."`).

```powershell
# Token: se pide oculto y se guarda a nivel de usuario
$sec = Read-Host "Pega el token de Biwenger (sin 'Bearer ')" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$tok  = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
[Environment]::SetEnvironmentVariable("BIWENGER_TOKEN", $tok, "User")
Remove-Variable tok, sec

# Estos dos no son secretos, son solo ids
[Environment]::SetEnvironmentVariable("BIWENGER_LEAGUE", (Read-Host "x-league"), "User")
[Environment]::SetEnvironmentVariable("BIWENGER_USER",   (Read-Host "x-user"),   "User")
```

**Importante:** las variables `"User"` solo aparecen en consolas **nuevas**.
Cierra y reabre PowerShell después de definirlas.

Comprobar (sin imprimir el token entero):

```powershell
$env:BIWENGER_TOKEN.Length
$env:BIWENGER_LEAGUE
$env:BIWENGER_USER
```

### Opción B — solo para la sesión actual

Se borran al cerrar la consola. Ojo: esto **sí** queda en el historial.

```powershell
$env:BIWENGER_TOKEN = "..."
$env:BIWENGER_LEAGUE = "..."
$env:BIWENGER_USER = "..."
```

### La cabecera `X-Version`

Biwenger rechaza las peticiones que envían un build antiguo del frontend:

```json
{"status":400,"message":"Old version","userMessage":"¡Estás usando una versión antigua!"}
```

El valor por defecto en `config.py` es **631** (capturado el 10-08-2026). Cuando
Biwenger lo incremente y vuelva el error, **no toques el código**:

```powershell
[Environment]::SetEnvironmentVariable("BIWENGER_VERSION", "632", "User")
```

Y abre una consola nueva. El valor actual lo tienes en DevTools → Network →
cualquier petición a `api/v2` → Request Headers → `x-version`. El script ya
detecta ese 400 concreto y sale con código `5` recordándote esto.

### Borrar / rotar

```powershell
[Environment]::SetEnvironmentVariable("BIWENGER_TOKEN", $null, "User")
```

### Nota sobre `setx`

`setx BIWENGER_TOKEN "..."` también funciona, pero **no lo recomiendo**: trunca a
1024 caracteres y deja el valor en el historial. Usa la Opción A.

## Cómo capturar token, x-league y x-user

1. Abre `biwenger.as.com`, ya logueado, con DevTools (F12) → pestaña **Network**.
2. Filtra por `api/v2` y haz clic en cualquier petición (p. ej. `market`).
3. En **Request Headers** copia:
   - `authorization: Bearer eyJ...` → el token es lo que va después de `Bearer `
   - `x-league: 1234567`
   - `x-user: 7654321`

## Uso

```powershell
python main.py                # tabla del mercado
python main.py --raw          # + JSON crudo por pantalla
python main.py --save         # guarda el JSON crudo en ./data/ (ignorado por git)
python main.py --sort=diff    # ordena por sobreprecio respecto al valor de mercado
```

Códigos de salida: `0` ok, `2` configuración incompleta, `3` token inválido/caducado,
`4` error de API o red, `5` `X-Version` obsoleta.

## Fase 2: el reporte diario

```powershell
py analyze.py --offline        # con los volcados de .\data\, cero peticiones
py analyze.py                  # en vivo
py analyze.py --save-report    # guarda en .\reports\
py analyze.py --unlock=2026-08-17 --max-clause-requests=15 --delay=1.5
```

`--offline` es el modo de desarrollo: reconstruye el reporte entero desde los
JSON ya guardados, sin red y sin token. Úsalo para tocar el motor de puntuación.

### Histórico de precios

Cada ejecución guarda una foto en `data/history/prices-YYYY-MM-DD.json`
(~15 KB/día). **Cuesta cero peticiones**: el dataset ya viene descargado. Es
idempotente — repetir el día sobreescribe. Se desactiva con `--no-record`.

Se versiona a propósito (excepción en `.gitignore`): solo contiene ids y precios
de jugadores, y en la Fase 4 el runner de GitHub Actions es efímero. Si no se
commitea, se pierde una foto al día y el detector de ventas nunca madura.

Lo que desbloquea cada tramo:

| Fotos | Qué se puede decir |
|---|---|
| 1 | Nada de tendencia real. Se usa `priceIncrement` (un día) amortiguado al 25 % en la puja. El detector de ventas ignora la tendencia por prudencia |
| 2 | Pendiente real, pero sin forma de curva |
| ≥3 | Se distingue *sigue subiendo* de *ha tocado techo*. El detector de ventas ya usa el momento |

### Los dos rankings de clausulazos

La sección de cláusulas sale por duplicado, porque son dos preguntas distintas:

- **(a) Top por eficiencia** — más mejora por millón. Gangas que tapan agujeros
  de plantilla.
- **(b) Top por impacto** — más mejora absoluta, sin penalizar el precio. Los
  jugadorazos que suben tu nivel, siempre que el saldo llegue.

Ambos exigen lo mismo: cláusula pagable con el saldo actual y mejora real de
plantilla. Un jugador puede salir en los dos; si lo hace, es ganga *y* jugadorazo.

Detalle que importa: la lista corta que se confirma por API es la **unión** de
los mejores por cada criterio, intercalados. Si se ordenara solo por eficiencia,
los baratos coparían las plazas de confirmación y los caros de alto impacto no
llegarían nunca al ranking (b). Está cubierto por `check_clause_rankings()` en
`smoke_test.py`, que hace los dos criterios disjuntos a propósito.

### Precios: exacto, estimado y recomendado

El reporte nunca mezcla una cifra leída con una calculada:

- **`[EXACTO]`** — cláusula leída de `GET /owners/player/{id}/clause`. Precio fijo.
- **`[ESTIMADO]`** — cláusula calculada en local (1,5 × valor), sin confirmar.
- **`[RECOMENDADO]`** — rango de puja de mercado. Subasta a ciegas: no se ven las
  pujas rivales. La prima sube con el atractivo y la tendencia, y baja si hay
  alternativas más baratas en la misma posición, si el jugador está inflado, o si
  la confianza de la puntuación es baja.

### Ritmo de peticiones

Leer la cláusula de todos los jugadores de todos los rivales serían ~15×N
llamadas. Hay cuatro frenos:

1. **Pre-filtro local** (el que más ahorra): la cláusula se estima como
   1,5 × valor, se descarta lo inasumible y lo que no mejora la plantilla, y
   solo se confirma por API la lista corta (`--confirm-limit`, 12 por defecto).
2. **Caché en disco con TTL** en `data/cache/clauses.json` (`--cache-ttl`, 6 h).
3. **Pausa entre peticiones** (`--delay`, 1,2 s).
4. **Presupuesto duro** por ejecución (`--max-clause-requests`, 25).

El reporte declara al final cuántas peticiones gastó y si agotó el presupuesto.

## Fase 3: el email

### Variables

```powershell
[Environment]::SetEnvironmentVariable("BIWENGER_MAIL_FROM", (Read-Host "remitente"), "User")
[Environment]::SetEnvironmentVariable("BIWENGER_MAIL_TO",   (Read-Host "destinatario"), "User")

$sec  = Read-Host "Contrasena de aplicacion de Gmail" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$pw   = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
[Environment]::SetEnvironmentVariable("BIWENGER_MAIL_PASSWORD", $pw, "User")
Remove-Variable pw, sec
```

`BIWENGER_MAIL_TO` admite varios destinatarios separados por coma. La
contraseña se acepta con o sin los espacios que muestra Gmail. Opcionales:
`BIWENGER_MAIL_HOST` (`smtp.gmail.com`) y `BIWENGER_MAIL_PORT` (`465` SSL
directo, `587` STARTTLS).

### Probar antes de automatizar

```powershell
py probe_email.py --dry-run                    # config enmascarada, no envía
py probe_email.py                              # mensaje de prueba mínimo
py analyze.py --offline --html-out=preview.html   # ver el HTML en el navegador
py analyze.py --offline --email-dry-run         # prepara el correo, no lo envía
py analyze.py --unlock=2026-08-17 --email       # en vivo, y lo envía
```

El correo va **multiparte**: texto plano + HTML, ambos con el reporte completo.
El texto plano no es un "mira el HTML" — es el reporte entero, para clientes que
no rendericen HTML. Las insignias (`EXACTO`, `PAGABLE`, `INFLADO`…) llevan
siempre su texto, nunca solo color.

Códigos de salida añadidos: `6` configuración de correo incompleta, `7` fallo de
envío.

### El correo nunca ejecuta nada

Va con `Auto-Submitted: auto-generated`, sin enlaces de acción, sin formularios
y sin nada accionable. Solo transporta información.

## Estructura

Fase 1:

- `config.py` — carga y valida el entorno; enmascara secretos en logs
- `client.py` — sesión HTTP con los headers de Biwenger; solo GET
- `market.py` — cruza el mercado con el dataset de jugadores
- `main.py` — orquesta y imprime

Fase 2:

- `scoring.py` — factores (rendimiento, tendencia, precio, calendario) y compuesto
- `history.py` — foto diaria de precios y detección de pico/caída
- `bidding.py` — recomendación de puja: exacta, estimada o rango
- `detectors.py` — los cuatro detectores; ninguno escribe nada
- `cache.py` — caché TTL y lector de cláusulas con freno
- `report.py` — modelo del reporte y renderizado a texto **y HTML**
- `analyze.py` — orquestador, con modo `--offline`

Fase 3:

- `mailer.py` — construcción del correo multiparte y envío SMTP
- `probe_email.py` — sonda de credenciales aislada del análisis

Exploración (desechable cuando la API esté estabilizada):

- `explore.py`, `probe_clause.py` — sondas read-only
- `inspect_data.py`, `analyze_squads.py`, `scan_clauses.py`,
  `inspect_clause_cfg.py`, `inspect_signals.py` — inspectores de los volcados
- `smoke_test.py` — pruebas sin red ni credenciales

## Hechos confirmados de la API

- Cláusula legible por `GET /owners/player/{id}/clause` — propia **y de rivales**.
  Devuelve un entero desnudo. La escritura es PUT; aquí nunca se usa.
- **Cláusula = 1,5 × valor** (Moi Gómez 630k→945k, De Jong 1,05M→1,575M).
  Cruza el millón, así que `clauseRanges` no impone umbral. Solo válido para
  jugadores nunca traspasados: `clauseIncrement=2` la altera tras un fichaje.
- `/user/{id}?fields=*,players(*)` devuelve `players` con **solo `id`**: la
  expansión anidada se ignora en silencio (200, sin error). Los atributos hay
  que cruzarlos con el dataset público de competición.
- `balance` aparece en la respuesta propia y **no** en la del rival
  (`settings.balance='hidden'`).
- El sobre `{"status","data"}` lo guarda `explore.py`; `main.py --save` guarda
  ya desenvuelto. Dos anidamientos distintos en `.\data\`.

## Advertencia sobre la API

`biwenger.as.com/api/v2` es una API interna, no documentada ni pública: los
endpoints y la forma de las respuestas pueden cambiar sin aviso. Por eso el
código es tolerante a variaciones de formato y `--raw` / `--save` existen: para
confirmar la forma real antes de construir la Fase 2 encima.
