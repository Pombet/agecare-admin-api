"""Pasarela de pago y suscripciones de AgeCare (Transbank Oneclick Mall).

Módulos:
    models   — tablas admin.billing_* (tenant_id + RLS, migración 0011_billing)
    tenancy  — contexto de tenant: app, cron por tenant y filtro explícito tenant_id
    gateway  — interfaz de pasarela + cliente Transbank Oneclick Mall + simulador
    service  — reglas de negocio: suscribir, cobrar, reintentar, cancelar, reembolsar
    jobs     — ciclo diario: conciliación, renovaciones, reintentos y expiración
    router_app / router_admin — endpoints para la app y para la consola
"""
