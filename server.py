"""
Sofascore MCP — conector personalizado para Claude (fútbol).

Expone datos de Sofascore como herramientas que Claude puede llamar solo:
partidos del día, estadísticas, remates por jugador con xG, cuotas con
probabilidad implícita, forma reciente de un equipo, etc.

Uso personal. Sofascore no tiene API oficial y sus términos restringen el
scraping comercial; los campos pueden cambiar sin aviso.
"""
import functools
import os
import time
import urllib.parse
from datetime import datetime, timezone

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from curl_cffi import requests as cffi  # imita un navegador real (evita muchos 403)
except Exception:  # pragma: no cover
    cffi = None

BASE = "https://api.sofascore.com/api/v1"
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}
CACHE_TTL = 60  # segundos
_cache: dict = {}

# Ruta secreta: pon MCP_PATH en las variables de entorno con un texto largo y raro.
MCP_PATH = os.environ.get("MCP_PATH", "mcp").strip("/")

mcp = FastMCP(
    "sofascore",
    instructions=(
        "Datos de fútbol de Sofascore. Flujo típico: matches_by_date o search para "
        "conseguir el event_id / team_id, luego match_statistics, match_players, "
        "match_shotmap, match_odds, team_recent_averages. Las probabilidades salen "
        "de las cuotas (implícitas, sin margen de la casa); no son un modelo propio."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8000")),
    streamable_http_path="/" + MCP_PATH,
    stateless_http=True,
)


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #
def _get(path: str):
    now = time.time()
    hit = _cache.get(path)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    url = BASE + path
    if cffi is not None:
        r = cffi.get(url, headers=HEADERS, impersonate="chrome", timeout=20)
    else:
        r = httpx.get(url, headers=HEADERS, timeout=20)
    if r.status_code == 404:
        return {"error": "No encontrado (404)", "path": path}
    if r.status_code in (403, 429):
        return {
            "error": f"Sofascore bloqueó la petición ({r.status_code}). "
            "Puede ser límite de uso o bloqueo a la IP del servidor.",
            "path": path,
        }
    r.raise_for_status()
    data = r.json()
    _cache[path] = (now, data)
    return data


def _safe(fn):
    """Decorador: devuelve {'error': ...} en vez de reventar la herramienta."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {e}"}
    return wrapper


def _dec(frac):
    """Cuota fraccionaria '7/4' -> decimal 2.75."""
    try:
        s = str(frac)
        if "/" in s:
            n, d = s.split("/")
            return round(1 + float(n) / float(d), 3)
        return round(float(s), 3)
    except Exception:  # noqa: BLE001
        return None


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace("%", "").strip())
    except Exception:  # noqa: BLE001
        return None


def _iso(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _score(ev):
    h = (ev.get("homeScore") or {}).get("current")
    a = (ev.get("awayScore") or {}).get("current")
    return f"{h}-{a}" if h is not None and a is not None else None


def _event_row(ev):
    return {
        "event_id": ev.get("id"),
        "inicio": _iso(ev.get("startTimestamp")),
        "local": (ev.get("homeTeam") or {}).get("name"),
        "visitante": (ev.get("awayTeam") or {}).get("name"),
        "torneo": (ev.get("tournament") or {}).get("name"),
        "estado": (ev.get("status") or {}).get("description"),
        "marcador": _score(ev),
    }


# --------------------------------------------------------------------------- #
# Herramientas
# --------------------------------------------------------------------------- #
@mcp.tool()
@_safe
def search(query: str) -> dict:
    """Busca equipos, jugadores o torneos por nombre y devuelve sus IDs."""
    data = _get("/search/all?q=" + urllib.parse.quote(query))
    if "error" in data:
        return data
    out = []
    for r in (data.get("results") or [])[:15]:
        e = r.get("entity") or {}
        out.append(
            {
                "tipo": r.get("type"),
                "id": e.get("id"),
                "nombre": e.get("name"),
                "pais": (e.get("country") or {}).get("name"),
                "torneo": (e.get("tournament") or {}).get("name"),
                "equipo": (e.get("team") or {}).get("name"),
            }
        )
    return {"resultados": out}


@mcp.tool()
@_safe
def matches_by_date(date: str, filtro: str = "") -> dict:
    """Partidos de fútbol de una fecha (YYYY-MM-DD). 'filtro' opcional:
    texto que debe aparecer en el torneo o en el nombre de un equipo
    (ej. 'Champions', 'Real Madrid', 'Liga 1')."""
    data = _get(f"/sport/football/scheduled-events/{date}")
    if "error" in data:
        return data
    f = filtro.lower().strip()
    rows = []
    for ev in data.get("events") or []:
        row = _event_row(ev)
        blob = " ".join(str(row.get(k) or "") for k in ("local", "visitante", "torneo")).lower()
        uname = ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("name") or ""
        if not f or f in blob or f in uname.lower():
            rows.append(row)
    rows.sort(key=lambda r: r["inicio"] or "")
    return {"fecha": date, "total": len(rows), "partidos": rows[:80]}


@mcp.tool()
@_safe
def match_details(event_id: int) -> dict:
    """Ficha del partido: equipos, torneo, estado, marcador, árbitro, estadio e IDs
    de torneo/temporada (sirven para tournament_standings)."""
    data = _get(f"/event/{event_id}")
    if "error" in data:
        return data
    ev = data.get("event") or {}
    row = _event_row(ev)
    row.update(
        {
            "home_team_id": (ev.get("homeTeam") or {}).get("id"),
            "away_team_id": (ev.get("awayTeam") or {}).get("id"),
            "unique_tournament_id": ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("id"),
            "season_id": (ev.get("season") or {}).get("id"),
            "ronda": (ev.get("roundInfo") or {}).get("round"),
            "arbitro": (ev.get("referee") or {}).get("name"),
            "estadio": (ev.get("venue") or {}).get("name"),
        }
    )
    return row


@mcp.tool()
@_safe
def match_statistics(event_id: int, periodo: str = "ALL") -> dict:
    """Estadísticas de equipo del partido (remates, remates a puerta, córners,
    atajadas, xG, posesión, faltas, tarjetas...). periodo: ALL, 1ST o 2ND."""
    data = _get(f"/event/{event_id}/statistics")
    if "error" in data:
        return data
    out = {}
    for p in data.get("statistics") or []:
        if p.get("period") != periodo:
            continue
        for g in p.get("groups") or []:
            for it in g.get("statisticsItems") or []:
                out[it.get("name")] = {
                    "local": it.get("home"),
                    "visitante": it.get("away"),
                    "grupo": g.get("groupName"),
                }
    if not out:
        return {"aviso": "Sin estadísticas para este partido/periodo (aún no empieza o la liga no las tiene)."}
    return {"event_id": event_id, "periodo": periodo, "estadisticas": out}


@mcp.tool()
@_safe
def match_players(event_id: int) -> dict:
    """Estadísticas por jugador en el partido (remates, remates a puerta, atajadas,
    xG, goles, asistencias, minutos, rating). Solo jugadores con minutos."""
    data = _get(f"/event/{event_id}/lineups")
    if "error" in data:
        return data
    out = {}
    for side, label in (("home", "local"), ("away", "visitante")):
        players = []
        for p in (data.get(side) or {}).get("players") or []:
            s = p.get("statistics") or {}
            if not s or not s.get("minutesPlayed"):
                continue
            keep = {k: v for k, v in s.items() if isinstance(v, (int, float)) and v != 0}
            players.append(
                {
                    "jugador": (p.get("player") or {}).get("name"),
                    "player_id": (p.get("player") or {}).get("id"),
                    "posicion": p.get("position"),
                    "suplente": p.get("substitute"),
                    "stats": keep,
                }
            )
        out[label] = players
    if not out["local"] and not out["visitante"]:
        return {"aviso": "Aún no hay estadísticas por jugador para este partido."}
    return out


@mcp.tool()
@_safe
def match_shotmap(event_id: int) -> dict:
    """Mapa de remates del partido con xG. Incluye resumen por jugador:
    remates totales, a puerta (gol o atajado), goles y xG acumulado."""
    data = _get(f"/event/{event_id}/shotmap")
    if "error" in data:
        return data
    shots = data.get("shotmap") or []
    if not shots:
        return {"aviso": "Sin mapa de remates para este partido."}
    resumen: dict = {}
    detalle = []
    for s in shots:
        name = (s.get("player") or {}).get("name") or "?"
        lado = "local" if s.get("isHome") else "visitante"
        tipo = s.get("shotType")
        r = resumen.setdefault(name, {"equipo": lado, "remates": 0, "a_puerta": 0, "goles": 0, "xg": 0.0})
        r["remates"] += 1
        if tipo in ("goal", "save"):
            r["a_puerta"] += 1
        if tipo == "goal":
            r["goles"] += 1
        r["xg"] = round(r["xg"] + (s.get("xg") or 0), 3)
        detalle.append(
            {
                "jugador": name,
                "equipo": lado,
                "min": s.get("time"),
                "tipo": tipo,
                "situacion": s.get("situation"),
                "xg": round(s.get("xg") or 0, 3),
                "xgot": round(s.get("xgot") or 0, 3) if s.get("xgot") else None,
            }
        )
    top = sorted(resumen.items(), key=lambda kv: (-kv[1]["remates"], -kv[1]["xg"]))
    return {
        "nota": "a_puerta = remates que terminaron en gol o atajada.",
        "resumen_por_jugador": [{"jugador": k, **v} for k, v in top],
        "remates": detalle,
    }


@mcp.tool()
@_safe
def match_odds(event_id: int) -> dict:
    """Cuotas de los mercados del partido, en decimal, con probabilidad implícita
    normalizada (quita el margen de la casa) y el margen de cada mercado."""
    data = _get(f"/event/{event_id}/odds/1/all")
    if "error" in data:
        return data
    markets = []
    for m in data.get("markets") or []:
        ch = []
        for c in m.get("choices") or []:
            d = _dec(c.get("fractionalValue"))
            if d and d > 1:
                ch.append({"opcion": c.get("name"), "cuota": d})
        if len(ch) < 2:
            continue
        raw = [1 / c["cuota"] for c in ch]
        total = sum(raw)
        for c, r in zip(ch, raw):
            c["prob_implicita_pct"] = round(100 * r / total, 1)
        markets.append(
            {
                "mercado": m.get("marketName"),
                "linea": m.get("choiceGroup"),
                "margen_casa_pct": round(100 * (total - 1), 1),
                "opciones": ch,
            }
        )
    if not markets:
        return {"aviso": "Sin cuotas disponibles para este partido."}
    return {"event_id": event_id, "mercados": markets}


@mcp.tool()
@_safe
def match_h2h(event_id: int) -> dict:
    """Historial directo entre los dos equipos del partido (victorias y empates)."""
    data = _get(f"/event/{event_id}/h2h")
    if "error" in data:
        return data
    d = data.get("teamDuel") or {}
    return {
        "victorias_local": d.get("homeWins"),
        "victorias_visitante": d.get("awayWins"),
        "empates": d.get("draws"),
    }


@mcp.tool()
@_safe
def team_last_matches(team_id: int, n: int = 5) -> dict:
    """Últimos n partidos terminados de un equipo (máx. 15) con event_id y marcador."""
    n = max(1, min(int(n), 15))
    data = _get(f"/team/{team_id}/events/last/0")
    if "error" in data:
        return data
    evs = [e for e in data.get("events") or [] if (e.get("status") or {}).get("type") == "finished"]
    evs.sort(key=lambda e: e.get("startTimestamp") or 0, reverse=True)
    return {"team_id": team_id, "partidos": [_event_row(e) for e in evs[:n]]}


METRICAS = [
    "Total shots",
    "Shots on target",
    "Corner kicks",
    "Goalkeeper saves",
    "Expected goals",
    "Ball possession",
    "Fouls",
    "Yellow cards",
]


@mcp.tool()
@_safe
def team_recent_averages(team_id: int, n: int = 5) -> dict:
    """Promedios de un equipo en sus últimos n partidos (máx. 10): lo que hace
    ('a_favor') y lo que recibe ('en_contra') en remates, remates a puerta,
    córners, atajadas, xG, posesión, faltas y amarillas. Usa n+1 llamadas."""
    n = max(1, min(int(n), 10))
    data = _get(f"/team/{team_id}/events/last/0")
    if "error" in data:
        return data
    evs = [e for e in data.get("events") or [] if (e.get("status") or {}).get("type") == "finished"]
    evs.sort(key=lambda e: e.get("startTimestamp") or 0, reverse=True)
    evs = evs[:n]

    favor = {m: [] for m in METRICAS}
    contra = {m: [] for m in METRICAS}
    partidos = []
    for ev in evs:
        es_local = (ev.get("homeTeam") or {}).get("id") == team_id
        row = _event_row(ev)
        row["condicion"] = "local" if es_local else "visitante"
        partidos.append(row)
        st = _get(f"/event/{ev.get('id')}/statistics")
        if not isinstance(st, dict) or "error" in st:
            continue
        for p in st.get("statistics") or []:
            if p.get("period") != "ALL":
                continue
            for g in p.get("groups") or []:
                for it in g.get("statisticsItems") or []:
                    name = it.get("name")
                    if name not in favor:
                        continue
                    h = _num(it.get("homeValue") if it.get("homeValue") is not None else it.get("home"))
                    a = _num(it.get("awayValue") if it.get("awayValue") is not None else it.get("away"))
                    mine, rival = (h, a) if es_local else (a, h)
                    if mine is not None:
                        favor[name].append(mine)
                    if rival is not None:
                        contra[name].append(rival)

    def avg(xs):
        return round(sum(xs) / len(xs), 2) if xs else None

    return {
        "team_id": team_id,
        "partidos_analizados": len(partidos),
        "a_favor": {m: {"promedio": avg(v), "partidos_con_dato": len(v)} for m, v in favor.items()},
        "en_contra": {m: {"promedio": avg(v), "partidos_con_dato": len(v)} for m, v in contra.items()},
        "partidos": partidos,
    }


@mcp.tool()
@_safe
def tournament_standings(unique_tournament_id: int, season_id: int) -> dict:
    """Tabla de posiciones. Los IDs salen de match_details."""
    data = _get(f"/unique-tournament/{unique_tournament_id}/season/{season_id}/standings/total")
    if "error" in data:
        return data
    out = []
    for grp in data.get("standings") or []:
        for r in grp.get("rows") or []:
            out.append(
                {
                    "pos": r.get("position"),
                    "equipo": (r.get("team") or {}).get("name"),
                    "pj": r.get("matches"),
                    "g": r.get("wins"),
                    "e": r.get("draws"),
                    "p": r.get("losses"),
                    "gf": r.get("scoresFor"),
                    "gc": r.get("scoresAgainst"),
                    "pts": r.get("points"),
                }
            )
    return {"tabla": out}


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
