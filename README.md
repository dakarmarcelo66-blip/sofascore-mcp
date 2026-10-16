# Sofascore MCP (fútbol) para Claude

Conector personalizado: Claude llama solo a Sofascore y te trae estadísticas,
remates por jugador con xG, cuotas con probabilidad implícita y forma reciente.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `search` | Busca equipos, jugadores o torneos y da su ID |
| `matches_by_date` | Partidos de una fecha (filtro opcional: torneo o equipo) |
| `match_details` | Ficha del partido + IDs de equipos, torneo y temporada |
| `match_statistics` | Remates, a puerta, córners, atajadas, xG, posesión, faltas, tarjetas |
| `match_players` | Stats por jugador (remates, a puerta, atajadas, xG, rating) |
| `match_shotmap` | Cada remate con xG + resumen por jugador |
| `match_odds` | Cuotas decimales + probabilidad implícita sin margen |
| `match_h2h` | Historial directo |
| `team_last_matches` | Últimos partidos de un equipo |
| `team_recent_averages` | Promedios a favor y en contra de los últimos N partidos |
| `tournament_standings` | Tabla de posiciones |

## Despliegue desde el celular (sin laptop)

1. **GitHub**: en github.com crea un repositorio nuevo (ej. `sofascore-mcp`) y
   sube estos 3 archivos: `server.py`, `requirements.txt`, `README.md`
   (botón *Add file → Upload files*).
2. **Render** (render.com): entra con tu cuenta de GitHub → *New → Web Service*
   → elige tu repo y configura:
   - Runtime: **Python 3**
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `python server.py`
   - Instance Type: **Free**
   - Environment Variables:
     - `MCP_PATH` = un texto largo y raro (ej. `betark-x7k29qpz84mw`). Es tu
       "contraseña": sin ella nadie puede usar tu servidor.
     - `PYTHON_VERSION` = `3.12.3` (recomendado)
3. Cuando Render termine, te da una URL tipo `https://sofascore-mcp.onrender.com`.
   **Tu link para Claude es:** `https://TU-APP.onrender.com/TU_MCP_PATH`
4. En Claude: *Settings → Connectors → Add custom connector*, pega ese link y guarda.
5. En un chat, activa el conector y pregunta, por ejemplo:
   *"Trae los partidos de hoy de Champions y para el primero dame remates a
   puerta por jugador, córners promedio de ambos equipos y la probabilidad
   implícita de las cuotas."*

## Ojo

- Plan gratis de Render: el servidor se duerme tras un rato sin uso; la primera
  consulta puede tardar ~1 minuto.
- Sofascore no tiene API oficial. Puede bloquear (403) a IPs de servidores
  o cambiar nombres de campos. Si pasa, la herramienta te devuelve un error claro.
- Las probabilidades salen de las cuotas, no son un modelo propio.
- Uso personal; los términos de Sofascore restringen el scraping comercial.
