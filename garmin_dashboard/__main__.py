"""Kommandozeile: ``python -m garmin_dashboard <befehl>``."""

from __future__ import annotations

import argparse
import logging
import webbrowser
from pathlib import Path

from . import analysis, normalize, report


def build(data_dir: Path, out: Path, sleep_need: float) -> Path:
    data = normalize.load(data_dir)
    result = analysis.analyze(data, sleep_need_h=sleep_need)
    report.write(result, out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(prog="garmin_dashboard", description="Health- & Trainings-Dashboard aus Garmin-Connect-Daten")
    ap.add_argument("--data", type=Path, default=Path("data"), help="Ordner für Rohdaten (Standard: data)")
    ap.add_argument("--out", type=Path, default=Path("output/dashboard.html"), help="Ziel-HTML")
    ap.add_argument("--sleep-need", type=float, default=8.0, help="persönlicher Schlafbedarf in Stunden")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="Daten von Garmin Connect laden")
    f.add_argument("--days", type=int, default=180, help="Zeitraum in Tagen (Standard: 180)")
    f.add_argument("--tokens", default="~/.garminconnect", help="Ablage der Login-Tokens")
    f.add_argument("--details", type=int, default=40, help="Zeitreihen für die letzten N Ausdauer-Einheiten laden")

    sub.add_parser("build", help="Dashboard aus vorhandenen Daten erzeugen")

    u = sub.add_parser("update", help="fetch + build in einem Schritt")
    u.add_argument("--days", type=int, default=180)
    u.add_argument("--tokens", default="~/.garminconnect")
    u.add_argument("--details", type=int, default=40)
    u.add_argument("--open", action="store_true", help="Dashboard im Browser öffnen")

    d = sub.add_parser("demo", help="Demo-Daten erzeugen und Dashboard bauen")
    d.add_argument("--days", type=int, default=365)
    d.add_argument("--open", action="store_true")

    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    if args.cmd in ("fetch", "update"):
        from . import fetch

        fetch.fetch_all(args.data, args.days, args.tokens, args.details)
    if args.cmd == "demo":
        from . import demo

        if args.data == Path("data"):
            args.data = Path("data/demo")
        demo.generate(args.data, days=args.days)
    if args.cmd in ("build", "update", "demo"):
        out = build(args.data, args.out, args.sleep_need)
        print(f"Dashboard geschrieben: {out.resolve()}")
        if getattr(args, "open", False):
            webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
