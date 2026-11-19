from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import CHAT_TITLE, EVENT_NAME, chat_link, is_admin
from db import db
from keyboards import (
    back_kb,
    final_kb,
    main_menu,
    nomination_kb,
    nominations_list_kb,
)
from utils import STAGES, build_nom_results, build_results_text, esc, safe_edit

router = Router(name="user")


class Suggest(StatesGroup):
    waiting = State()


def welcome_text() -> str:
    return (
        f"👑 <b>{esc(EVENT_NAME)}</b> — премия для чата <b>{CHAT_TITLE}</b>!\n\n"
        "Голосуй за лучших по очереди в каждой номинации:\n"
        "• один голос с аккаунта в каждой номинации;\n"
        "• голос можно изменить до конца голосования;\n"
        f"• голосовать могут только участники чата {chat_link()} 🔗"
    )


def about_text() -> str:
    return (
        f"ℹ️ <b>{esc(EVENT_NAME)}</b> — премия чата <b>{CHAT_TITLE}</b>.\n\n"
        f"📍 Чат: {chat_link()}\n\n"
        "<b>Как это работает:</b>\n"
        f"1️⃣ {STAGES['suggestions']} — голосование закрыто, каждый может выдвинуть себя кандидатом кнопкой «🎖 Предложить свою кандидатуру»;\n"
        f"2️⃣ {STAGES['voting']} — голосуй за номинантов по очереди;\n"
        f"3️⃣ {STAGES['closed']} — итоги публикуются в чате.\n\n"
        "Один голос в номинации с одного аккаунта, переголосовать можно в любой момент. 🗳"
    )


def final_text() -> str:
    return (
        f"🎉 <b>Готово! Ты проголосовал во всех номинациях!</b>\n\n"
        f"Спасибо за участие в <b>{esc(EVENT_NAME)}</b> 👑\n"
        f"Итоги — в чате {chat_link()}"
    )


async def first_unvoted(user_id: int):
    noms = await db.get_nominations()
    voted = await db.voted_nominations(user_id)
    return next((n for n in noms if n["id"] not in voted), None)


async def nomination_view(user_id: int, nom_id: int, page: int = 0):
    nom = await db.get_nomination(nom_id)
    if not nom or not nom["active"]:
        return None
    stage = await db.get_stage()
    noms = await db.get_nominations()
    ids = [n["id"] for n in noms]
    counter = f"Номинация {ids.index(nom_id) + 1}/{len(noms)}" if nom_id in ids else ""
    nominees = await db.get_nominees(nom_id, approved_only=True)
    my_vote = await db.get_vote(user_id, nom_id)
    my_nominee_row = await db.my_nominee(nom_id, user_id) if stage == "suggestions" else None

    text = (f"<i>{counter}</i>\n" if counter else "") + f"<b>{esc(nom['emoji'])} {esc(nom['title'])}</b>\n\n"
    if nom["description"]:
        text += f"<i>{esc(nom['description'])}</i>\n\n"
    if stage == "suggestions" and my_nominee_row:
        status = "на модерации ⏳" if not my_nominee_row["approved"] else "подтверждён ✅"
        text += (
            f"🎖 <b>Твоя заявка: {esc(my_nominee_row['title'])}</b> ({status})\n\n"
            "Заявиться можно и в другие номинации:\n"
            f"◂ «К номинациям» → выбери следующую ({counter})"
            if counter
            else "◂ Вернись к списку номинаций"
        )
    elif not nominees:
        text += "Кандидатов пока нет — можешь стать первым! 🎖"
    elif stage == "closed":
        text += "🔒 Голосование завершено. Итоги — в разделе «Результаты»."
    elif stage == "suggestions":
        text += "⏳ Голосование ещё не началось.\nПока можно выдвинуть себя кандидатом 🎖\n\nКандидаты:\n" + "\n".join(
            f"• {esc(c['title'])}" for c in nominees
        )
    else:
        if my_vote:
            cand = await db.get_nominee(my_vote)
            if cand:
                text += f"✅ Твой голос: <b>{esc(cand['title'])}</b> ●\nМожно переголосовать 👇"
        else:
            text += "Выбери кандидата 👇"
    return {
        "text": text,
        "reply_markup": nomination_kb(nom_id, nominees, my_vote, stage, page),
    }


async def go_next_or_final(call: CallbackQuery):
    view = None
    next_nom = await first_unvoted(call.from_user.id)
    if next_nom:
        view = await nomination_view(call.from_user.id, next_nom["id"])
    if view:
        await safe_edit(call.message, **view)
    else:
        await safe_edit(call.message, final_text(), final_kb())


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    user = m.from_user
    await db.upsert_user(user.id, user.username, user.full_name)
    u = await db.get_user(user.id)
    if u and u["banned"]:
        await m.answer("🚫 Ты исключён из голосования.")
        return
    stage = await db.get_stage()
    await m.answer(welcome_text(), reply_markup=main_menu(is_admin(user.id), stage))


@router.message(Command("help"))
async def cmd_help(m: Message):
    await m.answer(about_text(), reply_markup=main_menu(is_admin(m.from_user.id), await db.get_stage()))


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✅ Отменено.", reply_markup=main_menu(is_admin(m.from_user.id), await db.get_stage()))


@router.message(Command("vote"))
async def cmd_vote(m: Message, state: FSMContext):
    await state.clear()
    stage = await db.get_stage()
    if stage != "voting":
        await m.answer(**await nominations_list_view(m.from_user.id))
        return
    noms = await db.get_nominations()
    if not noms:
        await m.answer("Номинаций пока нет — следи за новостями в чате!", reply_markup=back_kb("menu"))
        return
    if not await first_unvoted(m.from_user.id):
        await m.answer(final_text(), reply_markup=final_kb())
        return
    view = await nomination_view(m.from_user.id, (await first_unvoted(m.from_user.id))["id"])
    await m.answer(**view)


@router.message(Command("results"))
async def cmd_results(m: Message):
    if not is_admin(m.from_user.id):
        await m.answer(
            "🔒 Результаты скрыты до конца голосования.\n"
            f"Итоги будут объявлены в чате {chat_link()} 🏆"
        )
        return
    stage = await db.get_stage()
    text = f"📊 <b>Результаты — {esc(EVENT_NAME)}</b>\n" + await build_results_text(stage)
    if stage == "voting":
        text += "\n\n⏳ <i>Голосование идёт — результаты предварительные.</i>"
    await m.answer(text, reply_markup=back_kb("menu"))


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(
        call.message,
        welcome_text(),
        main_menu(is_admin(call.from_user.id), await db.get_stage()),
    )
    await call.answer()


@router.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    await safe_edit(call.message, about_text(), back_kb("menu"))
    await call.answer()


async def nominations_list_view(user_id: int) -> dict:
    stage = await db.get_stage()
    noms = await db.get_nominations()
    if not noms:
        return {
            "text": "Номинаций пока нет — следи за новостями в чате!",
            "reply_markup": back_kb("menu"),
        }
    if stage == "closed":
        return {
            "text": (
                "🔒 <b>Голосование завершено!</b>\n\n"
                f"Итоги премии будут объявлены в чате {chat_link()} 🏆"
            ),
            "reply_markup": back_kb("menu"),
        }
    voted = await db.voted_nominations(user_id)
    suggested = await db.my_suggestions(user_id)
    header = f"🎖 <b>Номинации — {esc(EVENT_NAME)}</b>\nЭтап: <b>{STAGES[stage]}</b>\n\n"
    if stage == "suggestions":
        header += (
            "Заявись в нескольких номинациях — открой каждую и нажми "
            "«🎖 Предложить свою кандидатуру».\n"
            "🎖 — ты уже заявлен:\n\n"
        )
    else:
        header += "Выбери номинацию. ✅ — уже проголосовано:\n\n"
    return {
        "text": header,
        "reply_markup": nominations_list_kb(noms, voted, stage, suggested),
    }


@router.callback_query(F.data == "vote")
async def cb_vote(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, **await nominations_list_view(call.from_user.id))
    await call.answer()


@router.callback_query(F.data.startswith("nom:"))
async def cb_nom(call: CallbackQuery, state: FSMContext):
    await state.clear()
    parts = call.data.split(":")
    try:
        nom_id = int(parts[1])
        page = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        await call.answer()
        return
    view = await nomination_view(call.from_user.id, nom_id, page)
    if not view:
        await call.answer("Номинация недоступна", show_alert=True)
        return
    await safe_edit(call.message, **view)
    await call.answer()


@router.callback_query(F.data.startswith("vote:"))
async def cb_do_vote(call: CallbackQuery):
    try:
        _, nom_id, cand_id = call.data.split(":")
        nom_id, cand_id = int(nom_id), int(cand_id)
    except ValueError:
        await call.answer()
        return
    stage = await db.get_stage()
    if stage != "voting":
        await call.answer("⏳ Голосование ещё не началось — старт объявит организатор!", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    if not u or u["banned"]:
        await call.answer("🚫 Ты исключён из голосования", show_alert=True)
        return
    nom = await db.get_nomination(nom_id)
    if not nom or not nom["active"]:
        await call.answer("Номинация недоступна", show_alert=True)
        return
    cand = await db.get_nominee(cand_id)
    if not cand or cand["nomination_id"] != nom_id or not cand["approved"]:
        await call.answer("Кандидат недоступен", show_alert=True)
        return
    await db.vote(call.from_user.id, nom_id, cand_id)
    await call.answer(f"Голос за «{cand['title'][:30]}» засчитан 🗳")
    await go_next_or_final(call)


@router.callback_query(F.data.startswith("skip:"))
async def cb_skip(call: CallbackQuery):
    await call.answer("Пропускаем ▸")
    await go_next_or_final(call)


@router.callback_query(F.data.startswith("sugg:"))
async def cb_sugg(call: CallbackQuery, state: FSMContext):
    try:
        nom_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    nom = await db.get_nomination(nom_id)
    if not nom or not nom["active"]:
        await call.answer("Номинация недоступна", show_alert=True)
        return
    u = await db.get_user(call.from_user.id)
    if not u or u["banned"]:
        await call.answer("🚫 Ты исключён из голосования", show_alert=True)
        return
    stage = await db.get_stage()
    if stage == "closed":
        await call.answer("Голосование завершено 🔒", show_alert=True)
        return
    if stage == "suggestions":
        prompt = (
            f"🎖 <b>Заявка в номинацию «{esc(nom['title'])}»!</b>\n\n"
            "Как записать тебя в списке кандидатов?\n"
            "Отправь имя или /skip — возьму ник из профиля.\n\n"
            "/cancel — отмена"
        )
    else:
        prompt = (
            f"✍️ Напиши имя кандидата для номинации «{esc(nom['title'])}»:\n\n"
            "/cancel — отмена"
        )
    await state.set_state(Suggest.waiting)
    await state.update_data(nom_id=nom_id, mode=stage)
    await call.message.answer(prompt)
    await call.answer()


@router.message(Command("skip"), StateFilter(Suggest.waiting))
async def st_suggest_skip(m: Message, state: FSMContext):
    data = await state.get_data()
    if data.get("mode") != "suggestions":
        await m.answer("Напиши имя кандидата текстом или /cancel.")
        return
    user = m.from_user
    name = f"@{user.username}" if user.username else (user.full_name or "Кандидат")
    await apply_suggestion(m, state, name)


@router.message(StateFilter(Suggest.waiting))
async def st_suggest_input(m: Message, state: FSMContext):
    data = await state.get_data()
    title = (m.text or "").strip()[:64]
    if not title:
        await m.answer("Отправь имя текстом или /cancel.")
        return
    await apply_suggestion(m, state, title)


async def apply_suggestion(m: Message, state: FSMContext, title: str):
    data = await state.get_data()
    nom_id = data.get("nom_id")
    mode = data.get("mode")
    await state.clear()
    nom = await db.get_nomination(nom_id)
    if not nom or not nom["active"]:
        await m.answer("Номинация больше не доступна.")
        return
    if mode == "voting":
        cand_id = await db.add_nominee(
            nom_id, title, added_by=m.from_user.id, is_custom=True, approved=True
        )
        if cand_id is None:
            await m.answer("Такой кандидат уже есть в списке — проголосуй за него в меню. 🗳")
            return
        await db.vote(m.from_user.id, nom_id, cand_id)
        await m.answer(f"✅ Кандидат добавлен, голос за <b>{esc(title)}</b> засчитан! 🗳")
    else:
        cand_id = await db.add_nominee(
            nom_id, title, added_by=m.from_user.id, is_custom=True, approved=False
        )
        if cand_id is None:
            await m.answer("✅ Ты уже заявлен в этой номинации — ждём подтверждения организаторов.")
            return
        await m.answer(
            f"✅ Ты заявлен в номинации «{esc(nom['title'])}»!\n"
            "Организаторы подтвердят участие."
        )
    noms = await db.get_nominations()
    if mode == "voting":
        next_nom = await first_unvoted(m.from_user.id)
        if next_nom:
            view = await nomination_view(m.from_user.id, next_nom["id"])
            if view:
                await m.answer("▸ Следующая номинация:", **view)
                return
        await m.answer(final_text(), reply_markup=final_kb())
    else:
        await m.answer("▸ Выбери следующую номинацию:", **await nominations_list_view(m.from_user.id))


@router.callback_query(F.data == "res")
async def cb_res(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🔒 Результаты будут объявлены в чате после финала 🏆", show_alert=True)
        return
    stage = await db.get_stage()
    text = f"📊 <b>Результаты — {esc(EVENT_NAME)}</b>\n" + await build_results_text(stage)
    if stage == "voting":
        text += "\n\n⏳ <i>Голосование идёт — результаты предварительные.</i>"
    await safe_edit(call.message, text, back_kb("menu"))
    await call.answer()


@router.callback_query(F.data.startswith("resnom:"))
async def cb_resnom(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🔒 Результаты будут объявлены в чате после финала 🏆", show_alert=True)
        return
    try:
        nom_id = int(call.data.split(":")[1])
    except (ValueError, IndexError):
        await call.answer()
        return
    nom = await db.get_nomination(nom_id)
    if not nom:
        await call.answer("Номинация не найдена", show_alert=True)
        return
    stage = await db.get_stage()
    text = (
        f"📊 <b>{esc(nom['emoji'])} {esc(nom['title'])} — результаты</b>\n\n"
        + await build_nom_results(nom_id)
    )
    if stage == "voting":
        text += "\n\n⏳ <i>Голосование идёт — результаты предварительные.</i>"
    await safe_edit(call.message, text, back_kb("vote"))
    await call.answer()


@router.callback_query(F.data.startswith("adm"))
async def cb_adm_deny(call: CallbackQuery):
    await call.answer("⛔ Только для организаторов", show_alert=True)
