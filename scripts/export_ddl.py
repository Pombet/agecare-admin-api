"""Exporta el DDL canónico sin cambiar los snapshots de las migraciones."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "0002_core.sql", "0003_staff_audit.sql", "0004_metrics.sql",
    "0005_ops_support.sql", "0006_business_modules.sql", "0007_security_retention.sql",
)
DESTINATION = ROOT / "sql" / "agecare_admin_ddl.sql"


def render() -> str:
    parts = [
        "-- AgeCare: DDL canónico para una base PostgreSQL 16 nueva.\n"
        "-- Generado mediante python -m scripts.export_ddl.\n"
        "-- Incluye estructura y catálogos; no incluye datos demo ni alembic_version.\n"
        "-- Consultar docs/FRONTEND.md antes de ejecutar.\n\nBEGIN;\n",
    ]
    for name in SOURCES:
        parts.append(f"\n-- ===== alembic/sql/{name} =====\n")
        parts.append((ROOT / "alembic" / "sql" / name).read_text(encoding="utf-8").rstrip() + "\n")
    parts.append("\nCOMMIT;\n")
    return "".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Comprueba el archivo sin modificarlo")
    args = parser.parse_args()
    content = render()
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_text(encoding="utf-8") != content:
            raise SystemExit("El DDL debe actualizarse: python -m scripts.export_ddl")
        print("DDL sincronizado con los seis snapshots canónicos.")
        return
    DESTINATION.parent.mkdir(exist_ok=True)
    DESTINATION.write_text(content, encoding="utf-8", newline="\n")
    print("DDL exportado: sql/agecare_admin_ddl.sql")


if __name__ == "__main__":
    main()
