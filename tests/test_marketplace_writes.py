"""Regresiones de los metadatos obligatorios del marketplace canónico."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app import models
from app.routers import marketplace
from app.schemas.operation import ProductCreateIn, ProductPatchIn


class ProductDb:
    def __init__(self, product=None):
        self.product = product

    def add(self, product):
        self.product = product

    async def get(self, model, identity):
        assert model is models.Product and identity == self.product.id
        return self.product

    async def flush(self):
        assert self.product.created_by is not None
        if self.product.id is None:
            self.product.id = uuid4()
            self.product.status = "draft"
        self.product.updated_at = datetime.now(timezone.utc)

    async def refresh(self, product):
        assert product is self.product


@pytest.mark.asyncio
async def test_creation_assigns_creator_without_input_id(monkeypatch):
    monkeypatch.setattr(marketplace, "audit", AsyncMock())
    admin = SimpleNamespace(id=uuid4())
    db = ProductDb()
    body = ProductCreateIn(name="Producto de prueba", category="Apoyo", vendor="AgeCare",
                           price_clp=1000, external_url="https://example.com/producto")
    out = await marketplace.create_product(body, SimpleNamespace(), db, admin)
    assert db.product.created_by == admin.id
    assert out.id == db.product.id
    assert out.status == "draft"
    assert "id" not in ProductCreateIn.model_fields


@pytest.mark.asyncio
async def test_publication_and_archive_assign_dates_and_editor(monkeypatch):
    monkeypatch.setattr(marketplace, "audit", AsyncMock())
    admin = SimpleNamespace(id=uuid4())
    product = models.Product(id=uuid4(), name="Producto de prueba", category="Apoyo",
                             vendor="AgeCare", price_clp=1000, external_url="https://example.com/producto",
                             created_by=uuid4(), status="draft", updated_at=datetime.now(timezone.utc))
    db = ProductDb(product)
    await marketplace.patch_product(product.id, ProductPatchIn(status="published"),
                                    SimpleNamespace(), db, admin)
    published_at = product.published_at
    assert published_at is not None
    assert product.updated_by == admin.id
    await marketplace.patch_product(product.id, ProductPatchIn(status="archived"),
                                    SimpleNamespace(), db, admin)
    assert product.archived_at is not None
    assert product.published_at == published_at
    assert product.status == "archived"


@pytest.mark.asyncio
async def test_repeated_publication_preserves_original_date(monkeypatch):
    monkeypatch.setattr(marketplace, "audit", AsyncMock())
    previous = datetime(2026, 10, 1, tzinfo=timezone.utc)
    product = models.Product(id=uuid4(), name="Producto de prueba", category="Apoyo",
                             vendor="AgeCare", external_url="https://example.com/producto",
                             created_by=uuid4(), status="published", published_at=previous,
                             updated_at=previous)
    await marketplace.patch_product(product.id, ProductPatchIn(status="published"),
                                    SimpleNamespace(), ProductDb(product), SimpleNamespace(id=uuid4()))
    assert product.published_at == previous
