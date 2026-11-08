from datetime import timedelta

from sqlalchemy import update

from app.database.models import Article, utcnow
from tests.conftest import make_article


def test_users_and_preferences(db):
    u = db.get_or_create_user(111, "alice", ["TCS.NS"])
    assert u.watchlist_list == ["TCS.NS"] and not u.is_paused
    db.update_user(111, is_paused=True, disabled_slots="midday")
    assert db.get_user(111).disabled_slot_set == {"midday"}
    assert db.active_users([111]) == []
    db.get_or_create_user(222)
    assert [x.chat_id for x in db.active_users([111, 222])] == [222]
    assert db.active_users([333]) == []  # not allowed -> never returned


def test_slot_claim_is_exclusive(db):
    assert db.claim_slot_run(1, "morning", "2026-11-08") is True
    assert db.claim_slot_run(1, "morning", "2026-11-08") is False  # restart / double fire
    assert db.claim_slot_run(1, "morning", "2026-11-09") is True
    assert db.claim_slot_run(2, "morning", "2026-11-08") is True
    db.finish_slot_run(1, "morning", "2026-11-08", "sent")
    assert db.slot_run_status(1, "morning", "2026-11-08") == "sent"
    db.release_slot_run(2, "morning", "2026-11-08")
    assert db.claim_slot_run(2, "morning", "2026-11-08") is True


def test_processed_articles_excludes_delivered(db):
    db.add_articles([make_article(1, importance=9), make_article(2, importance=5), make_article(3, importance=2),
                     make_article(4, processing_status="new")])
    since = utcnow() - timedelta(hours=2)
    got = db.processed_articles(since=since, min_importance=3)
    assert [a.id for a in got] == ["a1", "a2"]
    db.record_deliveries(111, ["a1"], "morning")
    db.record_deliveries(111, ["a1"], "morning")  # idempotent
    assert [a.id for a in db.processed_articles(since=since, min_importance=3, exclude_delivered_to=111)] == ["a2"]
    assert db.get_article("a1").delivery_status == "delivered"
    db.record_deliveries(222, ["a2"], "command")  # on-demand views don't block scheduled delivery
    assert [a.id for a in db.processed_articles(since=since, min_importance=3, exclude_delivered_to=222)] == ["a1", "a2"]


def test_pending_and_status_updates(db):
    db.add_articles([make_article(1, processing_status="new", pre_score=3), make_article(2, processing_status="new", pre_score=8)])
    pend = db.pending_for_ai(utcnow() - timedelta(hours=5), limit=10)
    assert [a.id for a in pend] == ["a2", "a1"]
    db.mark_failed(["a2"], "boom")
    assert db.get_article("a2").attempts == 1 and db.get_article("a2").processing_status == "failed"
    db.save_analysis("a2", provider="gemini", summary="S", importance=7)
    a = db.get_article("a2")
    assert a.processing_status == "processed" and a.ai_provider == "gemini" and a.ai_error is None


def test_kv_feed_status_purge(db):
    db.kv_set("x", "1"); db.kv_set("x", "2")
    assert db.kv_get("x") == "2" and db.kv_get("missing", "d") == "d"
    db.record_feed_result("F", "u", False, error="HTTP 500")
    db.record_feed_result("F", "u", False, error="HTTP 500")
    assert db.feed_statuses()[0].consecutive_failures == 2
    db.record_feed_result("F", "u", True, 5)
    assert db.feed_statuses()[0].consecutive_failures == 0
    db.add_articles([make_article(1), make_article(2)])
    with db.session() as s:
        s.execute(update(Article).where(Article.id == "a1").values(collected_at=utcnow() - timedelta(days=40)))
    assert db.purge_older_than(30) == 1 and db.get_article("a1") is None and db.ping()
