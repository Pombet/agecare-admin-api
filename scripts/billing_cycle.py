"""Ciclo de facturación fuera de Vercel: conciliación, renovaciones, reintentos y expiración.

Uso:
    python -m scripts.billing_cycle

Corre el ciclo en cada tenant activo (RLS por tenant), igual que el cron de Vercel
(GET /api/v1/billing/internal/cron/billing-cycle). Sale con código 1 si hubo errores
para que el orquestador lo marque como fallido.
"""
import asyncio
import json
import sys

from app.billing.gateway import get_gateway
from app.billing.jobs import run_billing_cycle
from app.billing.tenancy import tenant_sessions
from app.config import get_settings
from app.logs import setup_logging


async def main() -> int:
    s = get_settings()
    setup_logging(s.log_level, s.log_format, s.environment)
    results, failed = {}, False
    async for code, db in tenant_sessions():
        stats = await run_billing_cycle(db, get_gateway())
        await db.commit()
        results[code] = stats.as_dict()
        failed = failed or bool(stats.errors)
    print(json.dumps(results, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
