# Despliegue en Render

**URL pública:** https://agente-preautorizacion.onrender.com
**Health check:** https://agente-preautorizacion.onrender.com/health → `{"status":"ok"}`

## Configuración del Web Service

| Campo | Valor |
|---|---|
| Repositorio | `https://github.com/Xyryung/agente-preautorizacion-quirurgica` (Public Git Repository) |
| Rama | `main` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Plan | Free (0.1 CPU, 512 MB RAM) |
| Región | Virginia (US East) |
| `PYTHON_VERSION` | `3.14.3` (igual que en local y en CI) |

### Variables de entorno

Los secretos se configuran **solo** en Render → Environment, nunca en el repositorio.
La lista completa está en [`.env.example`](../.env.example). Hoy la demo no necesita
ninguna, salvo `PYTHON_VERSION`: se agregan cuando entren Notion (#8), IA (#6) y la API (#9).

## Cómo se despliega

Render no tiene acceso de GitHub a este repositorio (se conecta como repositorio público),
así que no despliega solo al hacer commit. Hay dos formas:

1. **Automática (recomendada):** el job `deploy` de [`ci.yml`](../.github/workflows/ci.yml)
   llama al Deploy Hook de Render cuando las pruebas pasan en `main`. Requiere el secret
   `RENDER_DEPLOY_HOOK_URL` en GitHub (Settings → Secrets and variables → Actions).
2. **Manual:** en Render, **Manual Deploy → Deploy latest commit** tras cada merge a `main`.

## Tiempos medidos

Medidos con `curl` desde Panamá contra `/health` el 27/09/2026 (UTC).

| Escenario | Tiempo |
|---|---|
| Arranque en frío (servicio dormido > 15 min) | **32,6 s** hasta el primer byte |
| En caliente, n = 20 | p50 **0,28 s** · p95 **0,36 s** · máx 0,51 s |
| Página `/` en caliente | 0,27 s |
| Motor de reglas (dentro del servidor) | < 0,05 ms por caso |

En caliente, casi todo el tiempo es red. El motor de reglas es instantáneo.

## Plan contra el arranque en frío durante la evaluación

El plan gratuito duerme el servicio tras 15 min sin tráfico, y el primer visitante esperaría ~33 s.

- **Keep-alive con GitHub Actions:** [`keepalive.yml`](../.github/workflows/keepalive.yml)
  hace ping a `/health` cada 10 min. Está **encendido siempre**, porque no sabemos cuándo se evalúa.
  Para apagarlo, crear la variable del repositorio `KEEPALIVE` = `off`
  (Settings → Secrets and variables → Actions → Variables). El ping no usa OpenAI ni Notion,
  así que no genera costos. Nota: GitHub puede retrasar los cron varios minutos en horas de
  alta carga y desactiva los workflows programados tras 60 días sin actividad en el repositorio.
- **Respaldo:** un monitor externo gratuito (por ejemplo UptimeRobot, cada 5 min) sobre `/health`.
- **Justo antes de presentar (si se presenta en vivo):** abrir el enlace una vez para despertarlo.

Un servicio despierto 24/7 consume ~720 h al mes, dentro de las 750 h gratuitas de Render
por workspace. Por eso conviene tener un solo servicio gratuito activo.
