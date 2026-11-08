"""Sample news + fake AI for TEST_MODE (no network, no API keys needed).

Every sample item is clearly marked "[SAMPLE]" and links to example.com, so
test output can never be mistaken for real news.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta

from app.collectors.base import RawArticle
from app.database.models import utcnow

_SAMPLES = [
    ("india_market", "india", "RBI Press Releases", 3, True,
     "[SAMPLE] RBI keeps repo rate unchanged at 5.50%, retains neutral stance",
     "The Reserve Bank of India's Monetary Policy Committee kept the policy repo rate unchanged. The committee retained a neutral stance and said it will keep monitoring inflation and growth data."),
    ("india_market", "india", "Economic Times Markets", 2, False,
     "[SAMPLE] Sensex, Nifty end higher led by banking and IT stocks",
     "Benchmark indices closed higher on Friday. Gains in large private banks and IT services companies supported the market, while metal stocks lagged."),
    ("india_market", "india", "Moneycontrol Business", 2, False,
     "[SAMPLE] Infosys reports Q2 results; revenue guidance revised",
     "Infosys reported its quarterly results and revised its full-year revenue growth guidance. The company cited deal wins and client spending trends."),
    ("india_market", "india", "Mint Markets", 2, False,
     "[SAMPLE] SEBI proposes new framework for SME IPO disclosures",
     "The market regulator released a consultation paper proposing stricter disclosure norms for small and medium enterprise IPOs. Public comments are invited for 30 days."),
    ("global_market", "global", "CNBC Markets", 2, False,
     "[SAMPLE] Wall Street closes mixed as investors weigh Fed comments",
     "The S&P 500 ended slightly higher while the Nasdaq slipped. Investors assessed remarks from Federal Reserve officials about the path of interest rates."),
    ("global_market", "global", "BBC Business", 2, False,
     "[SAMPLE] Oil prices rise after OPEC+ signals output discipline",
     "Brent crude rose after OPEC+ members indicated they would keep production targets steady. Analysts said demand uncertainty remains."),
    ("tech", "global", "TechCrunch AI", 2, False,
     "[SAMPLE] AI lab releases new open-weight model for coding tasks",
     "A research lab released an open-weight language model focused on software engineering tasks. The release includes benchmark results and a permissive licence."),
    ("tech", "global", "The Verge", 2, False,
     "[SAMPLE] Chipmaker announces next-generation data-centre GPU",
     "A major semiconductor company announced a new data-centre GPU, saying it offers higher performance per watt. Shipments are expected next year."),
    ("tech", "global", "BBC Technology", 2, False,
     "[SAMPLE] EU opens consultation on AI system transparency rules",
     "The European Commission opened a consultation on transparency obligations for general-purpose AI systems under existing AI legislation."),
    ("india_world", "india", "The Hindu National", 2, False,
     "[SAMPLE] Union Cabinet approves scheme to boost electronics manufacturing",
     "The Union Cabinet approved a scheme aimed at increasing domestic manufacturing of electronic components. The government said the scheme will run for six years."),
    ("india_world", "global", "BBC World", 2, False,
     "[SAMPLE] G20 finance ministers meet to discuss global debt relief",
     "Finance ministers from G20 economies met to discuss debt relief for low-income countries. No final agreement was announced."),
    ("india_world", "india", "Indian Express India", 2, False,
     "[SAMPLE] Sponsored: Best credit card deals of the week",
     "Sponsored content."),
]


def sample_articles() -> list[RawArticle]:
    now = utcnow()
    out = []
    for i, (cat, region, src, w, official, title, desc) in enumerate(_SAMPLES):
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
        out.append(RawArticle(
            title=title, url=f"https://example.com/sample/{slug}", source=src,
            published_at=now - timedelta(minutes=20 + 25 * i), category=cat, region=region,
            description=desc, feed_name=src, weight=w, official=official,
        ))
    return out


def fake_llm_responder(system: str, user: str):
    """Builds a plausible, rule-based 'AI' response from the prompt items."""
    payload = json.loads(user[user.index("["):])
    if "takeaways" in system:
        heads = [p.get("headline", "") for p in payload][:3]
        return {"takeaways": [f"[TEST] {h}" for h in heads]}
    arts = []
    for it in payload:
        t = it["headline"]
        imp = 9 if "RBI" in t else 7 if any(k in t for k in ("Sensex", "Q2", "SEBI", "Fed", "GPU")) else 5
        arts.append({
            "id": it["id"],
            "headline": t,
            "summary": it.get("description") or t,
            "why_it_matters": "[TEST] Sample analysis: this type of development can influence sentiment in the related sector.",
            "affected": "[TEST] Related companies and investors in the sector.",
            "india_market_impact": "[TEST] Possible short-term sentiment effect; the actual impact is uncertain.",
            "global_market_impact": "",
            "importance": imp,
            "category": it.get("feed_category", "india_world"),
            "region": "india",
            "sentiment": "uncertain",
            "is_breaking": imp >= 9,
        })
    return {"articles": arts}
