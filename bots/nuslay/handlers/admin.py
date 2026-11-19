import asyncio
import csv
import io

from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import ADMIN_IDS, CHAT_ID, CHAT_TITLE, EVENT_NAME
from db import db
from keyboards import (
    admin_cands_kb,
    admin_main_kb,
    admin_noms_kb,
    admin_nom_kb,
    admin_stage_kb,
    back_kb,
    confirm_kb,
    finish_kb,
    launch_kb,
)
from utils import STAGES, build_final_text, build_results_text, esc, safe_edit


class AdminFilter(BaseFilter):
    async def __call__(self, event) -> bool:
        return event.from_user is not None and event.from_user.id in ADMIN_IDS


router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())


class AddNom(StatesGroup):
    title = State()
    emoji = State()
    description = State()


class EditNomTitle(StatesGroup):
    title = State()


class AddCand(StatesGroup):
    title = State()


class Broadcast(StatesGroup):
    text = State()
    confirm = State()


class BanInput(StatesGroup):
    user_id = State()


async def panel_text() -> str:
    stage = await db.get_stage()
    return (
        f"🛠 <b>Админ-панель {esc(EVENT_NAME)}</b>\n\n"
        f"Этап: <b>{STAGES[stage]}</b>\n"
        f"👥 Пользователей: {await db.users_count()}\n"
        f"📋 Номинаций: {await db.nominations_count()}\n"
        f"🗳 Голосов: {await db.votes_count()}"
    )


async def nom_view(nom_id: int):
    nom = await db.get_nomination(nom_id)
    if not nom:
        return None
    cands = await db.get_nominees(nom_id, approved_only=False)
    pending = sum(1 for c in cands if not c["approved"])
    votes = await db.votes_count(nom_id)
    status = "✅ активна" if nom["active"] else "❌ скрыта"
    text = (
        f"{esc(nom['emoji'])} <b>{esc(nom['title'])}</b> <i>#{nom['id']}</i>\n\n"
        + (f"{esc(nom['description'])}\n\n" if nom["description"] else "")
        + f"Статус: {status}\n"
        f"Кандидатов: {len(cands)} (⏳ на модерации: {pending})\n"
        f"🗳 Голосов: {votes}"
    )
    return {"text": text, "reply_markup": admin_nom_kb(nom_id, nom["active"])}


async def cands_view(nom_id: int):
    nom = await db.get_nomination(nom_id)
    if not nom:
        return None
    cands = await db.get_nominees(nom_id, approved_only=False)
    rows = await db.nomination_results(nom_id)
    counts = {r["id"]: r["cnt"] for r in rows}
    lines = [f"🧑‍🤝‍🧑 <b>Кандидаты</b> — «{esc(nom['title'])}»:\n"]
    if not cands:
        lines.append("Пока пусто. Добавь кандидатов или дождись предложений.")
    for c in cands:
        mark = "⏳" if not c["approved"] else ("✍️" if c["is_custom"] else "•")
        lines.append(f"{mark} {esc(c['title'])} — {counts.get(c['id'], 0)} гол.")
    lines.append("\n⏳ — ждёт модерации, ✍️ — предложен зрителем")
    return {"text": "\n".join(lines), "reply_markup": admin_cands_kb(nom_id, cands)}


@router.message(Command("admin"))
async def cmd_admin(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(await panel_text(), reply_markup=admin_main_kb(await db.get_stage()))


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("✅ Отменено.", reply_markup=admin_main_kb(await db.get_stage()))


@router.message(Command("export"))
async def cmd_export(m: Message, bot: Bot):
    rows = await db.export_votes()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["user_id", "username", "nomination", "nominee", "updated_at"])
    writer.writerows(rows)
    await bot.send_document(
        m.chat.id,
        BufferedInputFile(buf.getvalue().encode("utf-8"), filename="nuslay_votes.csv"),
    )


@router.callback_query(F.data == "adm")
async def cb_adm(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, await panel_text(), admin_main_kb(await db.get_stage()))
    await call.answer()


@router.callback_query(F.data == "adm:noms")
async def cb_adm_noms(call: CallbackQuery):
    noms = await db.get_nominations(active_only=False)
    if not noms:
        text = "📋 Номинаций пока нет — добавь первую!"
    else:
        text = "📋 <b>Номинации:</b>"
    await safe_edit(call.message, text, admin_noms_kb(noms))
    await call.answer()


@router.callback_query(F.data.startswith("adm:nom:"))
async def cb_adm_nom(call: CallbackQuery):
    nom_id = int(call.data.split(":")[2])
    view = await nom_view(nom_id)
    if not view:
        await call.answer("Номинация не найдена", show_alert=True)
        return
    await safe_edit(call.message, **view)
    await call.answer()


@router.callback_query(F.data.startswith("adm:nomtoggle:"))
async def cb_adm_nomtoggle(call: CallbackQuery):
    nom_id = int(call.data.split(":")[2])
    nom = await db.get_nomination(nom_id)
    if not nom:
        await call.answer("Номинация не найдена", show_alert=True)
        return
    await db.update_nomination(nom_id, active=not nom["active"])
    view = await nom_view(nom_id)
    await safe_edit(call.message, **view)
    await call.answer("Статус изменён")


@router.callback_query(F.data.startswith("adm:nomdel:"))
async def cb_adm_nomdel(call: CallbackQuery):
    nom_id = int(call.data.split(":")[2])
    await db.delete_nomination(nom_id)
    await call.answer("🗑 Номинация удалена")
    await cb_adm_noms(call)


@router.callback_query(F.data.startswith("adm:nomtitle:"))
async def cb_adm_nomtitle(call: CallbackQuery, state: FSMContext):
    nom_id = int(call.data.split(":")[2])
    await state.set_state(EditNomTitle.title)
    await state.update_data(nom_id=nom_id)
    await call.message.answer("Введи новое название номинации:\n/cancel — отмена")
    await call.answer()


@router.message(StateFilter(EditNomTitle.title))
async def st_nomtitle(m: Message, state: FSMContext):
    title = (m.text or "").strip()[:64]
    if not title:
        await m.answer("Название пустое, попробуй ещё раз.")
        return
    data = await state.get_data()
    await state.clear()
    await db.update_nomination(data["nom_id"], title=title)
    view = await nom_view(data["nom_id"])
    if view:
        await m.answer("✅ Название обновлено.")
        await m.answer(**view)
    else:
        await m.answer("Номинация не найдена.")


@router.callback_query(F.data == "adm:addnom")
async def cb_addnom(call: CallbackQuery, state: FSMContext):
    await state.set_state(AddNom.title)
    await call.message.answer("Введи название номинации:\n/cancel — отмена")
    await call.answer()


@router.message(StateFilter(AddNom.title))
async def st_addnom_title(m: Message, state: FSMContext):
    title = (m.text or "").strip()[:64]
    if not title:
        await m.answer("Название пустое, попробуй ещё раз.")
        return
    await state.update_data(title=title)
    await state.set_state(AddNom.emoji)
    await m.answer("Отправь эмодзи для номинации или /skip")


@router.message(Command("skip"), StateFilter(AddNom.emoji))
async def st_addnom_emoji_skip(m: Message, state: FSMContext):
    await state.update_data(emoji="🏆")
    await state.set_state(AddNom.description)
    await m.answer("Введи описание номинации или /skip")


@router.message(StateFilter(AddNom.emoji))
async def st_addnom_emoji(m: Message, state: FSMContext):
    emoji = (m.text or "").strip()[:8] or "🏆"
    await state.update_data(emoji=emoji)
    await state.set_state(AddNom.description)
    await m.answer("Введи описание номинации или /skip")


@router.message(Command("skip"), StateFilter(AddNom.description))
async def st_addnom_desc_skip(m: Message, state: FSMContext):
    await finish_addnom(m, state, description="")


@router.message(StateFilter(AddNom.description))
async def st_addnom_desc(m: Message, state: FSMContext):
    await finish_addnom(m, state, description=(m.text or "").strip()[:300])


async def finish_addnom(m: Message, state: FSMContext, description: str):
    data = await state.get_data()
    await state.clear()
    nom_id = await db.create_nomination(data["title"], data.get("emoji", "🏆"), description)
    await m.answer("✅ Номинация создана!")
    view = await nom_view(nom_id)
    if view:
        await m.answer(**view)


@router.callback_query(F.data.startswith("adm:cands:"))
async def cb_adm_cands(call: CallbackQuery):
    nom_id = int(call.data.split(":")[2])
    view = await cands_view(nom_id)
    if not view:
        await call.answer("Номинация не найдена", show_alert=True)
        return
    await safe_edit(call.message, **view)
    await call.answer()


@router.callback_query(F.data.startswith("adm:cadd:"))
async def cb_adm_cadd(call: CallbackQuery, state: FSMContext):
    nom_id = int(call.data.split(":")[2])
    await state.set_state(AddCand.title)
    await state.update_data(nom_id=nom_id)
    await call.message.answer("Введи имя кандидата:\n/cancel — отмена")
    await call.answer()


@router.message(StateFilter(AddCand.title))
async def st_addcand(m: Message, state: FSMContext):
    title = (m.text or "").strip()[:64]
    if not title:
        await m.answer("Имя пустое, попробуй ещё раз.")
        return
    data = await state.get_data()
    await state.clear()
    cand_id = await db.add_nominee(data["nom_id"], title)
    if cand_id is None:
        await m.answer("⚠️ Такой кандидат уже есть в этой номинации.")
        return
    await m.answer("✅ Кандидат добавлен.")
    view = await cands_view(data["nom_id"])
    if view:
        await m.answer(**view)


@router.callback_query(F.data.startswith("adm:cok:"))
async def cb_adm_cok(call: CallbackQuery):
    cand_id = int(call.data.split(":")[2])
    cand = await db.get_nominee(cand_id)
    if not cand:
        await call.answer("Кандидат не найден", show_alert=True)
        return
    await db.set_nominee_approved(cand_id, True)
    view = await cands_view(cand["nomination_id"])
    if view:
        await safe_edit(call.message, **view)
    await call.answer("✅ Одобрен")


@router.callback_query(F.data.startswith("adm:cdel:"))
async def cb_adm_cdel(call: CallbackQuery):
    cand_id = int(call.data.split(":")[2])
    cand = await db.get_nominee(cand_id)
    if not cand:
        await call.answer("Кандидат не найден", show_alert=True)
        return
    await db.delete_nominee(cand_id)
    view = await cands_view(cand["nomination_id"])
    if view:
        await safe_edit(call.message, **view)
    await call.answer("🗑 Удалён")


@router.callback_query(F.data == "adm:launch")
async def cb_adm_launch(call: CallbackQuery):
    noms = await db.get_nominations()
    if not noms:
        await call.answer("Сначала создай хотя бы одну номинацию!", show_alert=True)
        return
    empty = [n for n in noms if not await db.get_nominees(n["id"])]
    warn = (
        f"\n\n⚠️ Пустые номинации (нет кандидатов): {', '.join(esc(n['title']) for n in empty)}"
        if empty
        else ""
    )
    text = (
        "🚀 <b>Запустить голосование?</b>\n\n"
        f"Номинаций: {len(noms)}\n"
        f"После запуска зрители смогут голосовать за номинантов.{warn}\n\n"
        f"Объявить старт в чате <b>{CHAT_TITLE}</b>?"
    )
    await safe_edit(call.message, text, launch_kb())
    await call.answer()


@router.callback_query(F.data.startswith("adm:launch:"))
async def cb_adm_launch_go(call: CallbackQuery, bot: Bot):
    announce = call.data == "adm:launch:go"
    await db.set_stage("voting")
    await call.answer("🚀 Голосование запущено!")
    announced = False
    if announce:
        try:
            me = await bot.me()
            await bot.send_message(
                CHAT_ID,
                f"🗳 <b>Голосование {esc(EVENT_NAME)} началось!</b>\n\n"
                "Выбирай лучших по очереди в каждой номинации.\n"
                f"Голосуй 👉 https://t.me/{me.username}",
            )
            announced = True
        except Exception as e:
            await call.message.answer(
                f"⚠️ Не удалось объявить в чате: {str(e)[:160]}\n"
                "Сделай рассылку через «📣 Рассылка»."
            )
    status = " + объявление в чате отправлено ✅" if announced else ""
    await safe_edit(
        call.message,
        f"🚀 <b>Голосование запущено!</b>{status}\n\n" + await panel_text(),
        admin_main_kb("voting"),
    )


@router.callback_query(F.data == "adm:finish")
async def cb_adm_finish(call: CallbackQuery):
    total_votes = await db.votes_count()
    text = (
        "🔒 <b>Завершить голосование?</b>\n\n"
        f"Всего голосов: {total_votes}\n"
        "После завершения голосовать будет нельзя, останется опубликовать итоги."
    )
    await safe_edit(call.message, text, finish_kb())
    await call.answer()


@router.callback_query(F.data == "adm:finish:go")
async def cb_adm_finish_go(call: CallbackQuery):
    await db.set_stage("closed")
    await call.answer("Голосование завершено")
    await safe_edit(
        call.message,
        "🔒 <b>Голосование завершено.</b>\n\n" + await panel_text(),
        admin_main_kb("closed"),
    )


@router.callback_query(F.data == "adm:stage")
async def cb_adm_stage(call: CallbackQuery):
    stage = await db.get_stage()
    text = f"⚙️ <b>Этап голосования</b>\n\nТекущий: <b>{STAGES[stage]}</b>\n\nВыбери новый этап:"
    await safe_edit(call.message, text, admin_stage_kb(stage))
    await call.answer()


@router.callback_query(F.data.startswith("adm:stage:set:"))
async def cb_adm_stage_set(call: CallbackQuery):
    stage = call.data.split(":")[3]
    if stage not in STAGES:
        await call.answer()
        return
    await db.set_stage(stage)
    await call.answer(f"Этап: {STAGES[stage]}", show_alert=True)
    await safe_edit(call.message, await panel_text(), admin_main_kb(stage))


@router.callback_query(F.data == "adm:res")
async def cb_adm_res(call: CallbackQuery):
    stage = await db.get_stage()
    text = "📊 <b>Результаты</b>\n" + await build_results_text(stage)
    await safe_edit(call.message, text, back_kb("adm"))
    await call.answer()


@router.callback_query(F.data == "adm:br")
async def cb_adm_br(call: CallbackQuery, state: FSMContext):
    await state.set_state(Broadcast.text)
    await call.message.answer(
        "📣 Отправь текст рассылки (обычное сообщение):\n/cancel — отмена"
    )
    await call.answer()


@router.message(StateFilter(Broadcast.text))
async def st_broadcast_text(m: Message, state: FSMContext):
    if not m.text:
        await m.answer("Отправь текстовое сообщение.")
        return
    await state.update_data(text=m.text)
    await state.set_state(Broadcast.confirm)
    await m.answer(
        "👁 <b>Превью рассылки:</b>\n\n" + esc(m.text) + "\n\nОтправить всем?",
        reply_markup=confirm_kb("adm:brgo", "adm:brcancel", "📨 Отправить"),
    )


@router.callback_query(F.data == "adm:brgo")
async def cb_adm_brgo(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    text = data.get("text")
    await state.clear()
    if not text:
        await call.answer("Нет текста", show_alert=True)
        return
    await safe_edit(call.message, "⏳ Рассылка идёт...")
    ids = await db.all_user_ids()
    ok = fail = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            fail += 1
    await safe_edit(
        call.message,
        f"📨 Рассылка завершена.\n✅ Доставлено: {ok}\n⚠️ Ошибок: {fail}",
    )
    await call.answer()


@router.callback_query(F.data == "adm:brcancel")
async def cb_adm_brcancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(call.message, await panel_text(), admin_main_kb(await db.get_stage()))
    await call.answer("Отменено")


@router.callback_query(F.data == "adm:post")
async def cb_adm_post(call: CallbackQuery):
    text = await build_final_text()
    await safe_edit(
        call.message,
        text + f"\n\nОпубликовать в <b>{CHAT_ID}</b>?",
        confirm_kb("adm:postgo", "adm", "📨 Опубликовать"),
    )
    await call.answer()


@router.callback_query(F.data == "adm:postgo")
async def cb_adm_postgo(call: CallbackQuery, bot: Bot):
    text = await build_final_text()
    try:
        await bot.send_message(CHAT_ID, text)
        await safe_edit(call.message, f"✅ Итоги опубликованы в {CHAT_ID}")
    except Exception as e:
        await call.answer(f"Ошибка: {str(e)[:180]}", show_alert=True)
    await call.answer()


@router.callback_query(F.data.in_({"adm:ban", "adm:unban"}))
async def cb_adm_ban(call: CallbackQuery, state: FSMContext):
    action = call.data.split(":")[1]
    await state.set_state(BanInput.user_id)
    await state.update_data(action=action)
    await call.message.answer("Отправь ID пользователя (число):\n/cancel — отмена")
    await call.answer()


@router.message(StateFilter(BanInput.user_id))
async def st_ban_input(m: Message, state: FSMContext):
    raw = (m.text or "").strip()
    try:
        uid = int(raw)
    except ValueError:
        await m.answer("Нужен числовой ID. Попробуй ещё раз или /cancel.")
        return
    data = await state.get_data()
    action = data.get("action", "ban")
    await state.clear()
    user = await db.get_user(uid)
    if not user:
        await m.answer("⚠️ Пользователь не найден (он должен нажать /start).")
        return
    await db.set_banned(uid, action == "ban")
    status = "🚫 забанен" if action == "ban" else "✅ разбанен"
    await m.answer(f"Пользователь <code>{uid}</code> {status}.")
    await m.answer(await panel_text(), reply_markup=admin_main_kb(await db.get_stage()))
