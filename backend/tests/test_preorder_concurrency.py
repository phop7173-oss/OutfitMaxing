"""Preorder capacity under real concurrency (separate connections, real commits)."""

import threading

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientCapacityError, UnprocessableError
from app.modules.suppliers import service, sourcing

pytestmark = pytest.mark.db


def run_together(engine, workers, action):
    outcomes: list[str] = []
    barrier = threading.Barrier(workers)

    def worker():
        try:
            with Session(engine, expire_on_commit=False) as session:
                barrier.wait(timeout=60)
                outcomes.append(action(session))
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"error:{type(exc).__name__}")

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return outcomes


def capacity_row(engine, sp_id):
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                text("SELECT capacity, committed FROM supplier_products WHERE id = :id"),
                {"id": sp_id},
            ).one()
        )


def held_units(engine, sp_id):
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT coalesce(sum(quantity), 0) FROM preorder_commitments "
                    "WHERE supplier_product_id = :id AND status = 'HELD'"
                ),
                {"id": sp_id},
            ).scalar_one()
        )


def test_twenty_customers_cannot_exceed_the_preorder_capacity(_db_engine, make_committed_preorder):
    variant_id, sp_id = make_committed_preorder(capacity=5)

    def commit_one(session):
        try:
            service.commit_capacity(session, variant_id, 1)
            return "ok"
        except InsufficientCapacityError:
            return "insufficient"

    outcomes = run_together(_db_engine, 20, commit_one)
    assert sorted(outcomes) == ["insufficient"] * 15 + ["ok"] * 5
    assert capacity_row(_db_engine, sp_id) == (5, 5)
    assert held_units(_db_engine, sp_id) == 5


def test_simultaneous_requests_bundle_each_preorder_only_once(_db_engine, make_committed_preorder):
    variant_id, sp_id = make_committed_preorder(capacity=10)
    with Session(_db_engine, expire_on_commit=False) as setup:
        for _ in range(4):
            commitment = service.commit_capacity(setup, variant_id, 1)
            service.mark_deposit_verified(setup, commitment.id)

    def request(session):
        try:
            sourcing.create_request(session, sp_id)
            return "ok"
        except UnprocessableError:
            return "none"

    outcomes = run_together(_db_engine, 5, request)
    assert sorted(outcomes) == ["none"] * 4 + ["ok"]
    with _db_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT r.quantity, count(c.id) FROM sourcing_requests r "
                "JOIN preorder_commitments c ON c.sourcing_request_id = r.id "
                "WHERE r.supplier_product_id = :id GROUP BY r.id, r.quantity"
            ),
            {"id": sp_id},
        ).all()
    assert [tuple(r) for r in rows] == [(4, 4)]


def test_simultaneous_releases_give_capacity_back_once(_db_engine, make_committed_preorder):
    variant_id, sp_id = make_committed_preorder(capacity=10)
    with Session(_db_engine, expire_on_commit=False) as setup:
        commitment_id = service.commit_capacity(setup, variant_id, 3).id
    assert capacity_row(_db_engine, sp_id) == (10, 3)

    def release(session):
        service.release_commitment(session, commitment_id)
        return "ok"

    assert run_together(_db_engine, 6, release) == ["ok"] * 6
    assert capacity_row(_db_engine, sp_id) == (10, 0)
