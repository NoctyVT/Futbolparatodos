#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "partidos.json"

API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "").strip()

# API-Football league IDs (v3)
COMPETITIONS = [
    {"key": "argentina", "name": "Liga Profesional Argentina", "api_football_id": 128, "football_data_code": None},
    {"key": "paraguay", "name": "División Profesional Paraguay", "api_football_id": 250, "football_data_code": None},
    {"key": "libertadores", "name": "Copa Libertadores", "api_football_id": 13, "football_data_code": "CLI"},
    {"key": "sudamericana", "name": "Copa Sudamericana", "api_football_id": 11, "football_data_code": None},
    {"key": "champions", "name": "UEFA Champions League", "api_football_id": 2, "football_data_code": "CL"},
]

DAYS_AHEAD = 21
DEFAULT_LEAD_MINUTES = 30


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def iso_z(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def normalize_name(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return value


def stable_id(comp_key, home, away, kickoff):
    # Date-only identity survives small kickoff-time corrections.
    base = f"{comp_key}|{normalize_name(home)}|{normalize_name(away)}|{kickoff.date().isoformat()}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


def request_json(url, headers=None, timeout=20):
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_api_football(comp, date_from, date_to):
    if not API_FOOTBALL_KEY:
        raise RuntimeError("API_FOOTBALL_KEY no configurada")

    season = date_from.year
    params = {
        "league": comp["api_football_id"],
        "season": season,
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "timezone": "UTC",
    }
    url = "https://v3.football.api-sports.io/fixtures?" + urlencode(params)
    payload = request_json(
        url,
        headers={
            "x-apisports-key": API_FOOTBALL_KEY,
            "Accept": "application/json",
            "User-Agent": "FutbolParaTodos-GitHubAction",
        },
    )

    errors = payload.get("errors")
    if errors:
        raise RuntimeError(f"API-Football devolvió error: {errors}")

    out = []
    for item in payload.get("response", []):
        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        league = item.get("league") or {}
        kickoff = parse_iso(fixture.get("date"))
        if not kickoff:
            continue
        home = ((teams.get("home") or {}).get("name") or "").strip()
        away = ((teams.get("away") or {}).get("name") or "").strip()
        if not home or not away:
            continue
        out.append({
            "id": stable_id(comp["key"], home, away, kickoff),
            "titulo": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "competicion": (league.get("name") or comp["name"]).strip(),
            "competicion_clave": comp["key"],
            "inicio": iso_z(kickoff),
            "notificar_minutos_antes": DEFAULT_LEAD_MINUTES,
            "activo": True,
            "fuente": "api-football",
        })
    return out


def fetch_football_data(comp, date_from, date_to):
    code = comp.get("football_data_code")
    if not code:
        raise RuntimeError("football-data.org no está configurada para esta competición")
    if not FOOTBALL_DATA_KEY:
        raise RuntimeError("FOOTBALL_DATA_KEY no configurada")

    params = {
        "dateFrom": date_from.isoformat(),
        "dateTo": date_to.isoformat(),
    }
    url = f"https://api.football-data.org/v4/competitions/{code}/matches?" + urlencode(params)
    payload = request_json(
        url,
        headers={
            "X-Auth-Token": FOOTBALL_DATA_KEY,
            "Accept": "application/json",
            "User-Agent": "FutbolParaTodos-GitHubAction",
        },
    )

    out = []
    for item in payload.get("matches", []):
        kickoff = parse_iso(item.get("utcDate"))
        if not kickoff:
            continue
        home = ((item.get("homeTeam") or {}).get("name") or "").strip()
        away = ((item.get("awayTeam") or {}).get("name") or "").strip()
        if not home or not away:
            continue
        competition = ((item.get("competition") or {}).get("name") or comp["name"]).strip()
        out.append({
            "id": stable_id(comp["key"], home, away, kickoff),
            "titulo": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "competicion": competition,
            "competicion_clave": comp["key"],
            "inicio": iso_z(kickoff),
            "notificar_minutos_antes": DEFAULT_LEAD_MINUTES,
            "activo": True,
            "fuente": "football-data",
        })
    return out


def load_previous():
    if not OUT.exists():
        return []
    try:
        return json.loads(OUT.read_text(encoding="utf-8")).get("partidos", [])
    except Exception:
        return []


def cached_for_comp(previous, comp_key, now):
    result = []
    for item in previous:
        if item.get("competicion_clave") != comp_key:
            continue
        kickoff = parse_iso(item.get("inicio"))
        if not kickoff or kickoff < now - dt.timedelta(hours=3):
            continue
        clone = dict(item)
        clone["fuente"] = f"cache:{item.get('fuente', 'desconocida')}"
        result.append(clone)
    return result


def main():
    now = utc_now()
    date_from = now.date()
    date_to = (now + dt.timedelta(days=DAYS_AHEAD)).date()
    previous = load_previous()
    matches = []
    status = {}

    for comp in COMPETITIONS:
        key = comp["key"]
        used = None
        errors = []

        try:
            fresh = fetch_api_football(comp, date_from, date_to)
            matches.extend(fresh)
            used = "api-football"
        except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
            errors.append(f"api-football: {exc}")

        if used is None:
            try:
                fresh = fetch_football_data(comp, date_from, date_to)
                matches.extend(fresh)
                used = "football-data"
            except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
                errors.append(f"football-data: {exc}")

        if used is None:
            cached = cached_for_comp(previous, key, now)
            matches.extend(cached)
            used = "cache" if cached else "sin-datos"

        status[key] = {
            "fuente": used,
            "errores": errors,
        }

    # Dedupe by stable id and keep the freshest source over cache.
    priority = {"api-football": 3, "football-data": 2}
    dedup = {}
    for item in matches:
        current = dedup.get(item["id"])
        source = item.get("fuente", "")
        score = priority.get(source, 1 if source.startswith("cache:") else 0)
        current_score = -1
        if current:
            csrc = current.get("fuente", "")
            current_score = priority.get(csrc, 1 if csrc.startswith("cache:") else 0)
        if current is None or score > current_score:
            dedup[item["id"]] = item

    final = sorted(
        dedup.values(),
        key=lambda x: x.get("inicio", ""),
    )

    payload = {
        "schema": 1,
        "generated_at": iso_z(now),
        "window_days": DAYS_AHEAD,
        "competiciones": status,
        "partidos": final,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"partidos.json actualizado: {len(final)} partidos")
    for key, info in status.items():
        print(f"- {key}: {info['fuente']}")
        for err in info["errores"]:
            print(f"  aviso: {err}")


if __name__ == "__main__":
    main()
