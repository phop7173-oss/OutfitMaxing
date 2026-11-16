"""Inventory under real concurrency: separate connections, real commits, cleaned up afterwards."""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientStockError
from app.modules.inventory import service

pytestmark = pytest.mark.db


def run_together(engine, workers, action):
    """Run `action(session)` in `workers` threads released at the same moment."""
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


def reserve_one(variant_id):
    def action(session):
        try:
            service.reserve_stock(session, variant_id, 1, ttl_minutes=15)
            return "ok"
        except InsufficientStockError:
            return "insufficient"

    return action


def counts(engine, sku):
    with engine.connect() as conn:
        params = {"sku": sku}
        base = "FROM inventory_balances b JOIN product_variants v ON v.id = b.variant_id"
        on_hand, reserved = conn.execute(
            text(f"SELECT b.on_hand, b.reserved {base} WHERE v.sku = :sku"), params
        ).one()
        active = conn.execute(
            text(
                "SELECT coalesce(sum(r.quantity), 0) FROM inventory_reservations r "
                "JOIN product_variants v ON v.id = r.variant_id "
                "WHERE v.sku = :sku AND r.status = 'ACTIVE'"
            ),
            params,
        ).scalar_one()
    return on_hand, reserved, int(active)


def test_exactly_one_of_twenty_buyers_gets_the_last_item(_db_engine, make_committed_variant):
    variant_id, sku = make_committed_variant(stock=1)
    outcomes = run_together(_db_engine, 20, reserve_one(variant_id))
    assert sorted(outcomes) == ["insufficient"] * 19 + ["ok"]
    assert counts(_db_engine, sku) == (1, 1, 1)


def test_forty_buyers_never_reserve_more_than_the_stock(_db_engine, make_committed_variant):
    variant_id, sku = make_committed_variant(stock=10)
    outcomes = run_together(_db_engine, 40, reserve_one(variant_id))
    assert outcomes.count("ok") == 10
    assert outcomes.count("insufficient") == 30
    assert counts(_db_engine, sku) == (10, 10, 10)


def test_simultaneous_commits_of_one_reservation_deduct_once(_db_engine, make_committed_variant):
    variant_id, sku = make_committed_variant(stock=10)
    with Session(_db_engine, expire_on_commit=False) as setup:
        reservation = service.reserve_stock(setup, variant_id, 3, ttl_minutes=15)
        reservation_id = reservation.id

    def commit(session):
        service.commit_reservation(session, reservation_id)
        return "ok"

    outcomes = run_together(_db_engine, 6, commit)
    assert outcomes == ["ok"] * 6  # repeated commits are harmless
    assert counts(_db_engine, sku) == (7, 0, 0)
    with _db_engine.connect() as conn:
        commits = conn.execute(
            text("SELECT count(*) FROM inventory_movements WHERE reservation_id = :rid "
                 "AND reason = 'COMMIT'"),
            {"rid": reservation_id},
        ).scalar_one()
    assert commits == 1


def test_expiry_racing_with_new_buyers_never_oversells(_db_engine, make_committed_variant):
    variant_id, sku = make_committed_variant(stock=5)
    long_ago = datetime.now(UTC) - timedelta(minutes=60)
    with Session(_db_engine, expire_on_commit=False) as setup:
        old = service.reserve_stock(setup, variant_id, 5, ttl_minutes=1, now=long_ago)
        old_id = old.id

    outcomes = run_together(_db_engine, 10, reserve_one(variant_id))
    ok = outcomes.count("ok")
    assert 1 <= ok <= 5  # safety: never more than the stock; at least the thread that expired it
    assert set(outcomes) <= {"ok", "insufficient"}
    on_hand, reserved, active = counts(_db_engine, sku)
    assert (on_hand, reserved, active) == (5, ok, ok)
    with _db_engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM inventory_reservations WHERE id = :id"), {"id": old_id}
        ).scalar_one()
    assert status == "EXPIRED"
