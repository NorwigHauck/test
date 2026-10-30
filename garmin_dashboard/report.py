"""Schreibt das Analyse-Ergebnis in die HTML-Vorlage (eine einzige Datei, offline nutzbar)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TEMPLATE = Path(__file__).with_name("template.html")


def render(result: dict[str, Any]) -> str:
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # "</" escapen, damit Namen o. Ä. das <script>-Tag nicht beenden können
    payload = payload.replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)


def write(result: dict[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(result), encoding="utf-8")
