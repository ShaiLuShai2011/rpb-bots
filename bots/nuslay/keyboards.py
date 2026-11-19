from math import ceil

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils import STAGES

PER_PAGE = 5


def main_menu(admin: bool = False, stage: str = "voting") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if stage == "voting":
        b.button(text="🗳 Голосовать", callback_data="vote")
    elif stage != "closed":
        b.button(text="🎖 Номинанты", callback_data="vote")
    b.button(text="ℹ️ О премии", callback_data="about")
    if admin:
        b.button(text="🛠 Админ-панель", callback_data="adm")
    b.adjust(1)
    return b.as_markup()


def nominations_list_kb(nominations, voted: set, stage: str, suggested: set = None) -> InlineKeyboardMarkup:
    suggested = suggested or set()
    b = InlineKeyboardBuilder()
    for nom in nominations:
        if stage == "voting":
            mark = " ✅" if nom["id"] in voted else ""
        elif stage == "suggestions":
            mark = " 🎖" if nom["id"] in suggested else ""
        else:
            mark = ""
        title = f"{nom['emoji']} {nom['title']}{mark}"[:60]
        b.button(text=title, callback_data=f"nom:{nom['id']}")
    b.button(text="◂ В меню", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


def nomination_kb(nomination_id: int, nominees, my_vote, stage: str, page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    total_pages = max(1, ceil(len(nominees) / PER_PAGE))
    page = max(0, min(page, total_pages - 1))
    chunk = nominees[page * PER_PAGE : page * PER_PAGE + PER_PAGE]

    for c in chunk:
        mark = " ●" if my_vote == c["id"] else ""
        b.row(
            InlineKeyboardButton(
                text=f"{c['title']}{mark}"[:60],
                callback_data=f"vote:{nomination_id}:{c['id']}",
            )
        )

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"nom:{nomination_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"nom:{nomination_id}:{page + 1}"))
        b.row(*nav)

    if stage == "suggestions":
        b.row(
            InlineKeyboardButton(
                text="🎖 Предложить свою кандидатуру",
                callback_data=f"sugg:{nomination_id}",
            )
        )
    elif stage == "voting":
        b.row(InlineKeyboardButton(text="✍️ Свой вариант", callback_data=f"sugg:{nomination_id}"))
        b.row(InlineKeyboardButton(text="▸ Пропустить", callback_data=f"skip:{nomination_id}"))

    b.row(InlineKeyboardButton(text="◂ К номинациям", callback_data="vote"))
    return b.as_markup()


def final_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◂ В меню", callback_data="menu")
    return b.as_markup()


def back_kb(back_to: str = "menu") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="◂ Назад", callback_data=back_to)
    return b.as_markup()


def admin_main_kb(stage: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if stage == "suggestions":
        b.button(text="🚀 Запустить голосование", callback_data="adm:launch")
    if stage == "voting":
        b.button(text="🔒 Завершить голосование", callback_data="adm:finish")
    b.button(text="📋 Номинации", callback_data="adm:noms")
    b.button(text="➕ Новая номинация", callback_data="adm:addnom")
    b.button(text="⚙️ Этап голосования", callback_data="adm:stage")
    b.button(text="📊 Результаты", callback_data="adm:res")
    b.button(text="📣 Рассылка", callback_data="adm:br")
    b.button(text="🏆 Опубликовать итоги", callback_data="adm:post")
    b.button(text="🚫 Забанить", callback_data="adm:ban")
    b.button(text="✅ Разбанить", callback_data="adm:unban")
    b.button(text="◂ В меню", callback_data="menu")
    b.adjust(1, 2, 2, 2, 2, 1)
    return b.as_markup()


def launch_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📣 Запустить + объявить в чате", callback_data="adm:launch:go")
    b.button(text="🚀 Только запустить", callback_data="adm:launch:silent")
    b.button(text="✖️ Отмена", callback_data="adm")
    b.adjust(1)
    return b.as_markup()


def finish_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔒 Завершить", callback_data="adm:finish:go")
    b.button(text="✖️ Отмена", callback_data="adm")
    b.adjust(1)
    return b.as_markup()


def admin_stage_kb(stage: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in STAGES.items():
        mark = " ✅" if key == stage else ""
        b.button(text=f"{label}{mark}", callback_data=f"adm:stage:set:{key}")
    b.button(text="◂ В админку", callback_data="adm")
    b.adjust(1)
    return b.as_markup()


def admin_noms_kb(nominations) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for nom in nominations:
        b.button(text=f"{nom['emoji']} {nom['title']}"[:60], callback_data=f"adm:nom:{nom['id']}")
    b.button(text="➕ Добавить номинацию", callback_data="adm:addnom")
    b.button(text="◂ В админку", callback_data="adm")
    b.adjust(1)
    return b.as_markup()


def admin_nom_kb(nomination_id: int, active: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🧑‍🤝‍🧑 Кандидаты", callback_data=f"adm:cands:{nomination_id}")
    b.button(text="✏️ Изменить название", callback_data=f"adm:nomtitle:{nomination_id}")
    b.button(text="👁 Скрыть/показать", callback_data=f"adm:nomtoggle:{nomination_id}")
    b.button(text="🗑 Удалить номинацию", callback_data=f"adm:nomdel:{nomination_id}")
    b.button(text="◂ К номинациям", callback_data="adm:noms")
    b.adjust(1)
    return b.as_markup()


def admin_cands_kb(nomination_id: int, nominees) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in nominees:
        title = str(c["title"])[:40]
        if not c["approved"]:
            b.row(
                InlineKeyboardButton(text=f"✅ {title}", callback_data=f"adm:cok:{c['id']}"),
                InlineKeyboardButton(text="❌", callback_data=f"adm:cdel:{c['id']}"),
            )
        else:
            b.row(InlineKeyboardButton(text=f"❌ {title}", callback_data=f"adm:cdel:{c['id']}"))
    b.row(InlineKeyboardButton(text="➕ Добавить кандидата", callback_data=f"adm:cadd:{nomination_id}"))
    b.row(InlineKeyboardButton(text="◂ Назад", callback_data=f"adm:nom:{nomination_id}"))
    return b.as_markup()


def reminder_kb(bot_url: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎖 Открыть бота", url=bot_url)
    return b.as_markup()


def confirm_kb(yes_data: str, no_data: str, yes_text: str = "✅ Да", no_text: str = "✖️ Отмена") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=yes_text, callback_data=yes_data)
    b.button(text=no_text, callback_data=no_data)
    b.adjust(2)
    return b.as_markup()
