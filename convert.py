import json
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

SOURCE_URL = (
    "https://raw.githubusercontent.com/"
    "ThedarkSoldier996/NovaFree/refs/heads/main/novafree.w3u"
)

OUTPUT_FILE = Path("novafree.w3u")

EDGE_DOMAIN = "https://edge-live08-hr.cvattv.com.ar"
TOKEN_DOMAIN = "https://cdn.cvattv.com.ar"


def download_source():
    print("Descargando archivo fuente...")

    request = urllib.request.Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read().decode("utf-8")

    return json.loads(content)


def clean_path(original_url):
    """
    Obtiene solamente la ruta de la URL y elimina
    cualquier tok_{token}/ que ya exista.

    Ejemplo:

    /tok_{token}/live/c4eds/TV_Camara/...

    se convierte en:

    /live/c4eds/TV_Camara/...
    """

    parsed = urlparse(original_url)

    path = parsed.path.lstrip("/")

    # Evitar duplicación de tok_{token}
    while path.startswith("tok_{token}/"):
        path = path[len("tok_{token}/"):]

    return path


def build_edge_url(original_url):
    """
    Resultado:

    https://edge-live08-hr.cvattv.com.ar/tok_{token}/live/...
    """

    path = clean_path(original_url)

    return (
        f"{EDGE_DOMAIN}/tok_{{token}}/{path}"
    )


def build_token_url(original_url):
    """
    Resultado:

    https://cdn.cvattv.com.ar/live/...
    """

    path = clean_path(original_url)

    path = path.replace(
        "SA_Live_dash_enc",
        "SA_Live_dash_cenc"
    )

    return f"{TOKEN_DOMAIN}/{path}"


def convert_station(station):

    original_url = station.get("url", "")

    if not original_url:
        return station

    # Detectar estaciones antiguas
    is_old_format = any(
        key in station
        for key in (
            "flow",
            "type",
            "drm_license_uri",
            "icono"
        )
    )

    # También procesamos una estación que tenga
    # una URL edge ya generada, para corregir
    # posibles tok_{token} duplicados.
    is_generated_edge = (
        original_url.startswith(EDGE_DOMAIN)
    )

    if not is_old_format and not is_generated_edge:
        return station

    result = {}

    # --------------------------------
    # NAME
    # --------------------------------

    if "name" in station:
        result["name"] = station["name"]

    # --------------------------------
    # IMAGE
    # --------------------------------

    if station.get("icono"):
        result["image"] = station["icono"]

    elif station.get("image"):
        result["image"] = station["image"]

    # --------------------------------
    # URL
    # --------------------------------

    result["url"] = build_edge_url(
        original_url
    )

    # --------------------------------
    # TOKEN
    # --------------------------------

    result["token"] = build_token_url(
        original_url
    )

    # --------------------------------
    # LICENSE TYPE
    # --------------------------------

    if station.get("type"):
        result["license_type"] = str(
            station["type"]
        ).lower()

    elif station.get("license_type"):
        result["license_type"] = str(
            station["license_type"]
        ).lower()

    # --------------------------------
    # LICENSE KEY
    # --------------------------------

    if station.get("drm_license_uri"):
        result["license_key"] = station[
            "drm_license_uri"
        ]

    elif station.get("license_key"):
        result["license_key"] = station[
            "license_key"
        ]

    # --------------------------------
    # HEADERS
    # --------------------------------

    for key in (
        "Origin",
        "Referer",
        "User-Agent"
    ):
        if key in station:
            result[key] = station[key]

    # --------------------------------
    # OTROS CAMPOS
    # --------------------------------

    ignored = {
        "name",
        "image",
        "icono",
        "url",
        "flow",
        "type",
        "drm_license_uri",
        "license_type",
        "license_key",
        "token",
        "Origin",
        "Referer",
        "User-Agent"
    }

    for key, value in station.items():

        if key not in ignored:
            result[key] = value

    return result


def convert_data(data):

    total = 0
    converted = 0

    for group in data.get("groups", []):

        stations = group.get(
            "stations",
            []
        )

        for index, station in enumerate(stations):

            total += 1

            new_station = convert_station(
                station
            )

            if new_station != station:
                converted += 1

            stations[index] = new_station

    print(f"Estaciones encontradas: {total}")
    print(f"Estaciones procesadas: {converted}")

    return data


def save_file(data):

    content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    ) + "\n"

    OUTPUT_FILE.write_text(
        content,
        encoding="utf-8"
    )

    print(
        f"Archivo generado: {OUTPUT_FILE}"
    )


def main():

    data = download_source()

    data = convert_data(data)

    save_file(data)


if __name__ == "__main__":
    main()
