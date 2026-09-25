"""Kleiner JSON-Schema-Prüfer (Teilmenge von JSON Schema, ohne Zusatzpaket).

Unterstützt: type, required, properties, additionalProperties (false), items,
minimum, maximum, minLength, maxLength, enum, pattern, minItems, maxItems.
Zahlen müssen endlich sein (NaN/Infinity aus json.loads gelten nicht als number).
Gibt eine Liste lesbarer Fehler zurück; leere Liste = gültig.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from importlib import resources

_TYPEN = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _passt_typ(wert, typ: str) -> bool:
    if typ == "integer":
        return isinstance(wert, int) and not isinstance(wert, bool)
    if typ == "number":
        return isinstance(wert, (int, float)) and not isinstance(wert, bool) and math.isfinite(wert)
    return isinstance(wert, _TYPEN[typ])


def pruefe(wert, schema: dict, pfad: str = "$") -> list[str]:
    fehler: list[str] = []
    typen = schema.get("type")
    if typen is not None:
        typen = [typen] if isinstance(typen, str) else typen
        if not any(_passt_typ(wert, t) for t in typen):
            return [f"{pfad}: erwartet {'/'.join(typen)}, bekommen {type(wert).__name__}"]
    if "enum" in schema and wert not in schema["enum"]:
        fehler.append(f"{pfad}: {wert!r} nicht erlaubt")
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        if "minimum" in schema and wert < schema["minimum"]:
            fehler.append(f"{pfad}: {wert} < {schema['minimum']}")
        if "maximum" in schema and wert > schema["maximum"]:
            fehler.append(f"{pfad}: {wert} > {schema['maximum']}")
    if isinstance(wert, str):
        if "maxLength" in schema and len(wert) > schema["maxLength"]:
            fehler.append(f"{pfad}: länger als {schema['maxLength']} Zeichen")
        if "minLength" in schema and len(wert) < schema["minLength"]:
            fehler.append(f"{pfad}: kürzer als {schema['minLength']} Zeichen")
        if "pattern" in schema and not re.search(schema["pattern"], wert):
            fehler.append(f"{pfad}: passt nicht zu {schema['pattern']}")
    if isinstance(wert, dict):
        for name in schema.get("required", []):
            if name not in wert:
                fehler.append(f"{pfad}: Feld {name!r} fehlt")
        eigenschaften = schema.get("properties", {})
        for name, unterwert in wert.items():
            if name in eigenschaften:
                fehler += pruefe(unterwert, eigenschaften[name], f"{pfad}.{name}")
            elif schema.get("additionalProperties") is False:
                fehler.append(f"{pfad}: unbekanntes Feld {name!r}")
    if isinstance(wert, list):
        if "minItems" in schema and len(wert) < schema["minItems"]:
            fehler.append(f"{pfad}: weniger als {schema['minItems']} Einträge")
        if "maxItems" in schema and len(wert) > schema["maxItems"]:
            fehler.append(f"{pfad}: mehr als {schema['maxItems']} Einträge")
        if "items" in schema:
            for i, eintrag in enumerate(wert):
                fehler += pruefe(eintrag, schema["items"], f"{pfad}[{i}]")
    return fehler


@lru_cache(maxsize=None)
def lade(name: str) -> dict:
    """Lädt ein Schema aus src/clip_pipeline/schemas/<name>.schema.json."""
    return json.loads(resources.files("clip_pipeline").joinpath(f"schemas/{name}.schema.json").read_text(encoding="utf-8"))
