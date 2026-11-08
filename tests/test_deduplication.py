from datetime import timedelta

from app.collectors.base import RawArticle
from app.database.models import Article, utcnow
from app.services.deduplication import Deduplicator, is_similar
from app.utils.text import article_id_for, canonical_url, title_tokens


def raw(title, url, weight=2, official=False):
    return RawArticle(title, url, "Src", utcnow() - timedelta(minutes=5), "india_market", "india", weight=weight, official=official)


def test_canonical_url_normalisation():
    a = "https://www.Example.com/news/story/?utm_source=rss&utm_medium=x&id=5#top"
    b = "http://example.com/news/story?id=5"
    assert canonical_url(a) == canonical_url(b) == "https://example.com/news/story?id=5"
    assert canonical_url("https://m.example.com/a/amp") == "https://example.com/a"
    assert article_id_for(a) == article_id_for(b)
    assert article_id_for("https://example.com/x") != article_id_for("https://example.com/y")


def test_similarity():
    a = title_tokens("Sensex jumps 800 points as RBI cuts repo rate by 25 bps")
    b = title_tokens("RBI cuts repo rate by 25 bps; Sensex jumps 800 points")
    c = title_tokens("Infosys shares fall after weak quarterly guidance")
    assert is_similar(a, b) and not is_similar(a, c)
    assert not is_similar(title_tokens("Gold rises"), title_tokens("Gold falls"))


def test_dedup_against_db_and_within_batch(db):
    d = Deduplicator(db)
    batch = [
        raw("Sensex jumps 800 points as RBI cuts repo rate by 25 bps", "https://et.example/1?utm_source=rss", weight=2),
        raw("RBI cuts repo rate by 25 bps; Sensex jumps 800 points", "https://reuters.example/rbi", weight=3, official=False),
        raw("Sensex jumps 800 points as RBI cuts repo rate by 25 bps", "https://et.example/1"),  # same URL
        raw("NVIDIA unveils new AI chip", "https://cnbc.example/nv"),
    ]
    unique, dups = d.run(batch)
    assert len(unique) == 2 and len(dups) == 1
    # higher-weight source became the original
    original = next(u for u in unique if "rbi" in u.raw.title.lower())
    assert original.raw.url == "https://reuters.example/rbi"
    assert dups[0].duplicate_of == original.id

    # store, then a later run: known URL ignored, similar headline is a duplicate
    db.add_articles([Article(id=u.id, url=u.raw.url, canonical_url=u.canonical, title=u.raw.title, title_fp=u.fingerprint,
                             source="S", category="india_market", published_at=u.raw.published_at, collected_at=utcnow())
                     for u in unique])
    unique2, dups2 = d.run([raw("NVIDIA unveils new AI chip", "https://cnbc.example/nv"),
                            raw("Nvidia unveils new AI chip for data centres", "https://verge.example/nv")])
    assert unique2 == [] and len(dups2) == 1
    db.bump_coverage(dups2[0].duplicate_of)
    assert db.get_article(dups2[0].duplicate_of).coverage_count == 2
