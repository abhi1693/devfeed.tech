"""Retained scheduler history without inference payloads or live feed activity."""

import uuid

from sqlalchemy import text


def retained_analysis_jobs(factory, count=100_000):
    if not (factory.kw["bind"].url.database or "").endswith("_test"):
        raise ValueError("Scheduler history requires a disposable *_test database")
    article = uuid.uuid4()
    with factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO articles(id,canonical_url,url_hash,title,summary,content_type,"
                "feed_at,discovered_at,review_status,publication_status,published_to_feed_at,"
                "language,metadata_source_type) VALUES (:id,:url,:hash,'Retained article',"
                "'Scheduler fixture','article',now(),now(),'approved','published',now(),"
                "'en','publisher')"
            ),
            {"id": article, "url": f"https://scheduler.invalid/{article}", "hash": article.hex},
        )
        # The disposable fixture owns this transaction. Restore USER triggers
        # before commit; internal foreign-key triggers always remain active.
        session.execute(text("ALTER TABLE article_analysis_jobs DISABLE TRIGGER USER"))
        session.execute(
            text(
                "INSERT INTO article_analysis_jobs(id,article_id,status,attempts,available_at,"
                "created_at,finished_at,input_snapshot,catalog_snapshot,result) "
                "SELECT md5(:prefix||g::text)::uuid,:article,'succeeded',1,now(),now(),now(),"
                "'{}','{}','{}' FROM generate_series(1,:count) g"
            ),
            {"prefix": article.hex, "article": article, "count": count},
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        session.execute(text("ALTER TABLE article_analysis_jobs ENABLE TRIGGER USER"))
        session.execute(text("ANALYZE article_analysis_jobs"))
    return article
