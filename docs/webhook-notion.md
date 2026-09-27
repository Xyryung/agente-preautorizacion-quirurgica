# Webhook de Notion

Con el webhook, crear o editar un informe en Notion dispara el agente sin intervención manual.
Endpoint: `POST https://agente-preautorizacion.onrender.com/webhook/notion`

## Cómo funciona

1. Notion envía un evento cuando cambia una página a la que la integración tiene acceso
   (`page.created`, `page.properties_updated`, `page.content_updated`). Los eventos llegan
   agrupados, normalmente en menos de 1 minuto.
2. El endpoint verifica `X-Notion-Signature` (`sha256=` + HMAC-SHA256 del cuerpo crudo con el
   `verification_token`). Si la firma no coincide, responde **401**.
3. Descarta los eventos repetidos por `id` (Notion reintenta hasta 8 veces) y los que no son de páginas.
4. Responde **200** de inmediato y ejecuta `run_once()` en segundo plano.
5. `run_once()` solo procesa informes con `estado = pendiente`. Nuestras propias escrituras
   (la resolución creada y el cambio de estado) también generan eventos, pero ya no hay
   pendientes, así que no se forman bucles.
6. Nunca corren dos ciclos a la vez: si llegan eventos durante un ciclo, se agrupan y el ciclo
   se repite una sola vez al terminar.

## Configuración (una sola vez)

Requisito: el servicio de Render ya tiene `NOTION_TOKEN`, `NOTION_DB_INFORMES`,
`NOTION_DB_POLIZAS` y `NOTION_DB_RESOLUCIONES` en **Environment**, y la integración de Notion
tiene acceso a las tres bases.

1. En Notion: **Settings → Integrations → (la integración) → Webhooks → Create a subscription**.
2. URL: `https://agente-preautorizacion.onrender.com/webhook/notion`. Eventos: los de **Page**
   (al menos *created*, *properties updated* y *content updated*).
3. Notion envía el handshake. Abre antes el enlace de la demo para que el servicio esté despierto;
   si Notion marca error, vuelve a intentarlo.
4. En Render → **Logs**, busca la línea `Handshake de Notion recibido` y copia el token (`secret_...`).
5. En Notion, pega el token en **Verify** para activar la suscripción.
6. En Render → **Environment**, agrega `NOTION_WEBHOOK_VERIFICATION_TOKEN` con ese token.
   Render reinicia el servicio.

Mientras `NOTION_WEBHOOK_VERIFICATION_TOKEN` no esté configurado, el endpoint rechaza los
eventos con **503**. Una vez configurado, ignora handshakes nuevos. Para crear otra suscripción,
borra primero la variable.

## Prueba de punta a punta

1. Crea un informe en `Informes_Hospital` con `estado = pendiente` y un `paciente_id` que tenga póliza.
2. En menos de ~1 minuto debe aparecer su fila en `Resoluciones` y el informe debe cambiar de estado.
3. En Render → Logs se ve la línea con la decisión, por ejemplo `P001/Colecistectomía -> PREAPROBADA`.

## Limitaciones

- Los ids de eventos vistos viven en memoria: se olvidan con cada deploy o reinicio.
- Si el servicio está dormido, el primer evento espera el arranque en frío (~30 s). El keep-alive
  lo evita casi siempre, y Notion reintenta los eventos no entregados.
- Cada evento ejecuta un ciclo completo (lee todas las pólizas y los informes pendientes).
  Es suficiente para la demo; con mucho volumen convendría procesar solo la página del evento.
