"""Builds Telegram messages (HTML parse mode) for briefings and alerts.

Two layouts (user picks in /settings):
  cards   - one message per story, each with its own "Read Full Article" button
  compact - one message per category, numbered buttons "Read Full Article 1..N"

Only the header/alert message makes a notification sound; the rest are sent
silently so a briefing doesn't buzz your phone 15 times.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from app.ai.classifier import importance_level
from app.collectors.market_collector import Quote
from app.config import CATEGORY_TITLES
from app.database.models import Article

DIVIDER = "━━━━━━━━━━━━━━━━━━"
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3900
DISCLAIMER = ("⚠️ <i>Disclaimer: This is an AI-generated news summary for informational and educational "
              "purposes. It is not financial or investment advice.</i>")
SENTIMENT_ICON = {"positive": "🟢 Positive", "negative": "🔴 Negative", "mixed": "🟡 Mixed", "uncertain": "⚪ Uncertain"}
LEVEL_ICON = {"HIGH": "🔥", "MEDIUM": "📌", "LOW": "▫️"}


@dataclass
class OutMessage:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (label, url)
    silent: bool = True


def esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def fmt_local(dt: datetime, tz, fmt: str = "%d %b, %H:%M") -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(tz)
    return f"{local.strftime(fmt)} {local.tzname() or ''}".strip()


def _is_ai(a: Article) -> bool:
    return bool(a.ai_provider) and a.ai_provider not in ("none",)


def _source_line(a: Article, tz) -> str:
    line = f"📰 <b>Source:</b> {esc(a.source)} · {fmt_local(a.published_at, tz)}"
    if (a.coverage_count or 1) > 1:
        n = a.coverage_count - 1
        line += f" <i>(+{n} other source{'s' if n > 1 else ''})</i>"
    if a.is_official:
        line += " · 🏛️ Official"
    return line


def format_card(a: Article, index: int | None, tz, *, section_header: str | None = None) -> OutMessage:
    parts: list[str] = []
    if section_header:
        parts += [DIVIDER, f"<b>{esc(section_header)}</b>", ""]
    num = f"{index}. " if index is not None else ""
    parts.append(f"<b>{num}{esc(a.display_headline)}</b>")
    parts += ["", "📝 <b>Summary:</b>", esc(a.summary or a.description or "")]
    if _is_ai(a):
        if a.why_it_matters:
            parts += ["", "💡 <b>Why it matters</b> <i>(analysis)</i>:", esc(a.why_it_matters)]
        if a.affected:
            parts += ["", f"👥 <b>Who's affected:</b> {esc(a.affected)}"]
        impact = []
        if a.india_impact:
            impact.append(f"🇮🇳 India: {esc(a.india_impact)}")
        if a.global_impact:
            impact.append(f"🌍 Global: {esc(a.global_impact)}")
        if impact:
            parts += ["", "📊 <b>Market Impact</b> <i>(analysis, not certain)</i>:", *impact]
        parts += ["", f"🧭 <b>Sentiment:</b> {SENTIMENT_ICON.get(a.sentiment or 'uncertain', '⚪ Uncertain')}"]
    else:
        parts += ["", "ℹ️ <i>AI analysis unavailable for this item - summary is taken from the source feed.</i>"]
    level = a.importance_level or importance_level(a.importance)
    parts.append(f"{LEVEL_ICON.get(level, '🔥')} <b>Importance:</b> {a.importance or '?'}/10 ({level})")
    parts.append(_source_line(a, tz))
    return OutMessage("\n".join(parts), [("🔗 Read Full Article", a.url)])


def format_compact(category_title: str, items: Sequence[Article], tz, start: int = 1) -> list[OutMessage]:
    """One message per category; splits into several if Telegram's limit is reached."""
    messages: list[OutMessage] = []
    header = f"{DIVIDER}\n<b>{esc(category_title)}</b>\n"
    cur_text, cur_buttons = header, []
    for i, a in enumerate(items, start):
        level = a.importance_level or importance_level(a.importance)
        block = [f"\n<b>{i}. {esc(a.display_headline)}</b>", esc(a.summary or a.description or "")]
        if _is_ai(a) and a.why_it_matters:
            block.append(f"💡 <i>Why it matters:</i> {esc(a.why_it_matters)}")
        if _is_ai(a) and a.india_impact:
            block.append(f"📊 <i>Impact (analysis):</i> {esc(a.india_impact)}")
        block.append(f"{LEVEL_ICON.get(level, '🔥')} {a.importance or '?'}/10 · 📰 {esc(a.source)} · {fmt_local(a.published_at, tz)}")
        text = "\n".join(block) + "\n"
        if len(cur_text) + len(text) > SAFE_LIMIT and cur_buttons:
            messages.append(OutMessage(cur_text, cur_buttons))
            cur_text, cur_buttons = "", []
        cur_text += text
        cur_buttons.append((f"🔗 Read Full Article {i}", a.url))
    if cur_buttons:
        messages.append(OutMessage(cur_text, cur_buttons))
    return messages


def _arrow(pct: float | None) -> str:
    if pct is None:
        return ""
    return "▲" if pct > 0.005 else "▼" if pct < -0.005 else "▬"


def format_quote(q: Quote, tz) -> str:
    pct = q.change_pct
    change = f" {_arrow(pct)} {pct:+.2f}%" if pct is not None else ""
    prev = f" (prev close {q.previous_close:,.2f})" if q.previous_close else ""
    return f"• <b>{esc(q.name)}</b>: {q.price:,.2f}{change}{prev} · <i>{fmt_local(q.as_of, tz, '%d %b %H:%M')}</i>"


def format_snapshot(groups: Sequence[tuple[str, Sequence[Quote]]], tz, provider_name: str) -> str:
    if not groups:
        return ""
    lines = ["📊 <b>MARKET SNAPSHOT</b>"]
    any_quote = False
    for title, quotes in groups:
        lines.append(f"\n<b>{esc(title)}</b>")
        if not quotes:
            lines.append("• <i>Data currently unavailable.</i>")
            continue
        any_quote = True
        lines += [format_quote(q, tz) for q in quotes]
    if any_quote:
        lines.append(f"\n🕒 <i>Times show when each price was recorded. Source: {esc(provider_name)}. "
                     "Prices may be delayed and are not real-time.</i>")
    return "\n".join(lines)


def format_header(title: str, now: datetime, tz, counts: dict[str, int], snapshot: str = "") -> OutMessage:
    local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=timezone.utc).astimezone(tz)
    lines = [f"<b>{esc(title)}</b>", f"📅 Date: {local.strftime('%A, %d %b %Y · %H:%M')} {local.tzname() or ''}".rstrip(), DIVIDER]
    if snapshot:
        lines += [snapshot, DIVIDER]
    if counts:
        summary = " · ".join(f"{CATEGORY_TITLES[c].split(' ')[0]} {n}" for c, n in counts.items())
        lines.append(f"In this briefing: {summary} stories")
    else:
        lines.append("<i>No significant new stories since the last update.</i>")
    return OutMessage("\n".join(lines), silent=False)


def format_footer(takeaways: Sequence[str]) -> OutMessage:
    lines = [DIVIDER]
    if takeaways:
        lines.append("🧠 <b>TODAY'S KEY TAKEAWAY</b>")
        lines += [f"{i}. {esc(t)}" for i, t in enumerate(takeaways, 1)]
        lines.append("")
    lines.append(DISCLAIMER)
    return OutMessage("\n".join(lines))


def build_briefing(*, title: str, now: datetime, tz, grouped: dict[str, list[Article]], takeaways: Sequence[str],
                   snapshot: str = "", fmt: str = "cards") -> list[OutMessage]:
    counts = {c: len(v) for c, v in grouped.items()}
    msgs = [format_header(title, now, tz, counts, snapshot)]
    for cat, items in grouped.items():
        title_c = CATEGORY_TITLES.get(cat, cat)
        if fmt == "compact":
            msgs += format_compact(title_c, items, tz)
        else:
            for i, a in enumerate(items, 1):
                msgs.append(format_card(a, i, tz, section_header=title_c if i == 1 else None))
    msgs.append(format_footer(takeaways))
    return msgs


def build_list(title: str, items: Sequence[Article], tz, fmt: str = "cards", empty_text: str | None = None) -> list[OutMessage]:
    """For on-demand commands like /tech, /trending."""
    if not items:
        return [OutMessage(f"<b>{esc(title)}</b>\n\n{empty_text or 'No processed stories yet. Try /now in a few minutes.'}", silent=False)]
    if fmt == "compact":
        msgs = format_compact(title, items, tz)
        msgs[0].silent = False
    else:
        msgs = [format_card(a, i, tz, section_header=title if i == 1 else None) for i, a in enumerate(items, 1)]
        msgs[0].silent = False
    msgs.append(OutMessage(DISCLAIMER))
    return msgs


def build_breaking(a: Article, tz) -> list[OutMessage]:
    card = format_card(a, None, tz)
    card.text = f"🚨 <b>BREAKING | HIGH IMPORTANCE</b>\n{DIVIDER}\n" + card.text + "\n\n" + DISCLAIMER
    card.silent = False
    return [card]
