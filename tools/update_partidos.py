#!/usr/bin/env python3
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OUTPUT = Path(__file__).resolve().parents[1] / "partidos.json"

API_FOOTBALL_URL = "https://v3.football.api-sports.io/fixtures"
FOOTBALL_DATA_URL = "https://api.football-data.org/v4/matches"

# IDs históricos de API-Football para las 5 competiciones objetivo.
# Se pueden sobreescribir sin tocar el script usando la variable de repositorio
# API_FOOTBALL_LEAGUE_IDS, por ejemplo: 2,11,13,128,250
DEFAULT_API_FOOTBALL_LEAGUE_IDS = {2, 11, 13, 128, 250}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().lower()
    return text


def parse_ids() -> set[int]:
    raw = os.getenv("API_FOOTBALL_LEAGUE_IDS", "").strip()
    if not raw:
        return set(DEFAULT_API_FOOTBALL_LEAGUE_IDS)
    result = set()
    for part in raw.split(","):
        try:
            result.add(int(part.strip()))
        except ValueError:
            pass
    return result or set(DEFAULT_API_FOOTBALL_LEAGUE_IDS)


def http_json(url: str, headers: dict[str, str], timeout: int = 25):
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            body = resp.read().decode("utf-8", errors="replace")
            if status == 429:
                raise RuntimeError("RATE_LIMIT")
            if status < 200 or status >= 300:
                raise RuntimeError(f"HTTP_{status}")
            return json.loads(body)
    except HTTPError as e:
        if e.code == 429:
            raise RuntimeError("RATE_LIMIT") from e
        raise RuntimeError(f"HTTP_{e.code}") from e
    except URLError as e:
        raise RuntimeError(f"NETWORK_{e.reason}") from e


def iso_utc(value: str) -> str:
    if not value:
        return ""
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def target_football_data_match(match: dict) -> bool:
    comp = match.get("competition") or {}
    area = match.get("area") or {}
    c_name = norm(comp.get("name", ""))
    c_code = norm(comp.get("code", ""))
    area_name = norm(area.get("name", ""))

    if "champions league" in c_name or c_code == "cl":
        return True
    if "libertadores" in c_name:
        return True
    if "sudamericana" in c_name:
        return True

    if area_name == "argentina":
        return any(x in c_name for x in ("liga profesional", "primera division", "primera division"))
    if area_name == "paraguay":
        return any(x in c_name for x in ("division profesional", "primera division", "primera division"))

    return False


def from_api_football(start: str, end: str) -> list[dict]:
    key = os.getenv("API_FOOTBALL_KEY", "").strip()
    if not key:
        raise RuntimeError("MISSING_API_FOOTBALL_KEY")

    params = urlencode({"from": start, "to": end})
    payload = http_json(
        f"{API_FOOTBALL_URL}?{params}",
        {
            "x-apisports-key": key,
            "Accept": "application/json",
            "User-Agent": "FPT-FixtureBot/1.0",
        },
    )

    errors = payload.get("errors")
    if errors:
        blob = norm(json.dumps(errors, ensure_ascii=False))
        if "limit" in blob or "request" in blob or "rate" in blob:
            raise RuntimeError("RATE_LIMIT")
        raise RuntimeError(f"API_FOOTBALL_ERRORS:{errors}")

    allowed_ids = parse_ids()
    out = []
    for item in payload.get("response") or []:
        league = item.get("league") or {}
        if int(league.get("id") or -1) not in allowed_ids:
            continue

        fixture = item.get("fixture") or {}
        teams = item.get("teams") or {}
        home = (teams.get("home") or {}).get("name", "").strip()
        away = (teams.get("away") or {}).get("name", "").strip()
        start_iso = iso_utc(fixture.get("date", ""))
        if not home or not away or not start_iso:
            continue

        fixture_id = fixture.get("id")
        league_name = league.get("name", "").strip()
        out.append({
            "provider_id": f"af:{fixture_id}",
            "titulo": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "competicion": league_name,
            "inicio": start_iso,
            "fuente": "api-football",
            "fuentes": ["api-football"],
            "activo": True,
            "notificar_minutos_antes": 30,
        })
    return out


def from_football_data(start: str, end: str) -> list[dict]:
    token = os.getenv("FOOTBALL_DATA_TOKEN", "").strip()
    if not token:
        raise RuntimeError("MISSING_FOOTBALL_DATA_TOKEN")

    params = urlencode({"dateFrom": start, "dateTo": end})
    payload = http_json(
        f"{FOOTBALL_DATA_URL}?{params}",
        {
            "X-Auth-Token": token,
            "Accept": "application/json",
            "User-Agent": "FPT-FixtureBot/1.0",
        },
    )

    out = []
    for match in payload.get("matches") or []:
        if not target_football_data_match(match):
            continue

        home = ((match.get("homeTeam") or {}).get("name") or "").strip()
        away = ((match.get("awayTeam") or {}).get("name") or "").strip()
        start_iso = iso_utc(match.get("utcDate", ""))
        if not home or not away or not start_iso:
            continue

        comp = match.get("competition") or {}
        match_id = match.get("id")
        out.append({
            "provider_id": f"fd:{match_id}",
            "titulo": f"{home} vs {away}",
            "local": home,
            "visitante": away,
            "competicion": (comp.get("name") or "").strip(),
            "inicio": start_iso,
            "fuente": "football-data",
            "fuentes": ["football-data"],
            "activo": True,
            "notificar_minutos_antes": 30,
        })
    return out


def match_key(item: dict) -> str:
    start = datetime.fromisoformat(item["inicio"].replace("Z", "+00:00"))
    # Bucket de 10 minutos para tolerar pequeños desfasajes entre proveedores.
    bucket = int(start.timestamp() // 600)
    return f"{norm(item.get('local',''))}|{norm(item.get('visitante',''))}|{bucket}"


def canonical_id(item: dict) -> str:
    start = datetime.fromisoformat(item["inicio"].replace("Z", "+00:00"))
    day = start.strftime("%Y-%m-%d")
    home = re.sub(r"[^a-z0-9]+", "-", norm(item.get("local", ""))).strip("-")
    away = re.sub(r"[^a-z0-9]+", "-", norm(item.get("visitante", ""))).strip("-")
    return f"{home}-{away}-{day}"


def merge(primary: list[dict], secondary: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in primary + secondary:
        key = match_key(item)
        if key not in merged:
            merged[key] = dict(item)
            continue
        current = merged[key]
        sources = list(dict.fromkeys((current.get("fuentes") or []) + (item.get("fuentes") or [])))
        current["fuentes"] = sources
        # API-Football conserva prioridad cuando ambas coinciden.
        if current.get("fuente") != "api-football" and item.get("fuente") == "api-football":
            keep_sources = sources
            current.clear()
            current.update(item)
            current["fuentes"] = keep_sources

    result = []
    for item in merged.values():
        item["id"] = canonical_id(item)
        item.pop("provider_id", None)
        result.append(item)

    result.sort(key=lambda x: x["inicio"])
    return result


def main() -> int:
    now = datetime.now(timezone.utc)
    start = now.date().isoformat()
    end = (now + timedelta(days=7)).date().isoformat()

    af_matches = []
    fd_matches = []
    errors = []
    af_ok = False
    fd_ok = False

    try:
        af_matches = from_api_football(start, end)
        af_ok = True
        print(f"API-Football: {len(af_matches)} partidos objetivo")
    except Exception as e:
        errors.append(f"API-Football: {e}")
        print(errors[-1], file=sys.stderr)

    try:
        fd_matches = from_football_data(start, end)
        fd_ok = True
        print(f"football-data.org: {len(fd_matches)} partidos objetivo")
    except Exception as e:
        errors.append(f"football-data.org: {e}")
        print(errors[-1], file=sys.stderr)

    if not af_ok and not fd_ok:
        # No pisamos el JSON existente cuando ambos proveedores realmente fallan.
        if OUTPUT.exists():
            print("Ambas fuentes fallaron. Se conserva partidos.json existente.", file=sys.stderr)
            return 2

    partidos = merge(af_matches, fd_matches)

    doc = {
        "generado_en": now.isoformat().replace("+00:00", "Z"),
        "ventana_desde": start,
        "ventana_hasta": end,
        "fuentes_consultadas": {
            "api-football": "ok" if af_ok else "fallo",
            "football-data.org": "ok" if fd_ok else "fallo",
        },
        "partidos": partidos,
    }

    OUTPUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Escritos {len(partidos)} partidos en {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
