from datetime import datetime
from html import escape

from aiogram.exceptions import TelegramBadRequest

from config import EVENT_NAME
from db import db

STAGES = {
    "suggestions": "📥 Сбор предложений",
    "voting": "🗳 Голосование",
    "closed": "🔒 Завершено",
}


def esc(t) -> str:
    return escape(str(t or ""))


def bar(pct: int) -> str:
    filled = max(0, min(10, round(pct / 10)))
    return "█" * filled + "░" * (10 - filled)


async def safe_edit(message, text, reply_markup=None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass


async def build_nom_results(nomination_id: int) -> str:
    rows = await db.nomination_results(nomination_id)
    total = sum(r["cnt"] for r in rows)
    if total == 0:
        return "⏳ Голосов пока нет."
    lines = []
    for i, r in enumerate(rows[:10], 1):
        pct = round(r["cnt"] * 100 / total)
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(f"{medal} {esc(r['title'])} — <code>{bar(pct)}</code> {pct}% ({r['cnt']})")
    lines.append(f"\nВсего голосов: <b>{total}</b>")
    return "\n".join(lines)


async def build_results_text(stage: str) -> str:
    noms = await db.get_nominations(active_only=False)
    if not noms:
        return "Номинаций пока нет."
    lines = []
    for nom in noms:
        lines.append(f"\n<b>{esc(nom['emoji'])} {esc(nom['title'])}</b>")
        if stage == "suggestions":
            lines.append("⏳ Идёт сбор предложений")
            continue
        rows = await db.nomination_results(nom["id"])
        total = sum(r["cnt"] for r in rows)
        if total == 0:
            lines.append("⏳ Голосов пока нет")
            continue
        for i, r in enumerate(rows[:5], 1):
            pct = round(r["cnt"] * 100 / total)
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} {esc(r['title'])} — <code>{bar(pct)}</code> {pct}% ({r['cnt']})")
    return "\n".join(lines)


async def build_final_text() -> str:
    year = datetime.now().year
    noms = await db.get_nominations(active_only=False)
    lines = [f"🏆 <b>ИТОГИ ПРЕМИИ {esc(EVENT_NAME)} {year}</b>", ""]
    for nom in noms:
        rows = await db.nomination_results(nom["id"])
        total = sum(r["cnt"] for r in rows)
        lines.append(f"{esc(nom['emoji'])} <b>{esc(nom['title'])}</b>")
        if not rows or rows[0]["cnt"] == 0:
            lines.append("— Победитель не определён\n")
            continue
        top = rows[0]["cnt"]
        winners = [r for r in rows if r["cnt"] == top]
        if len(winners) == 1:
            pct = round(top * 100 / total)
            lines.append(f"🥇 {esc(winners[0]['title'])} — {top} гол. ({pct}%)\n")
        else:
            names = ", ".join(esc(w["title"]) for w in winners)
            lines.append(f"🥇 Ничья: {names} — по {top} гол.\n")
    lines.append(f"Спасибо всем, кто голосовал! ❤️")
    return "\n".join(lines)
