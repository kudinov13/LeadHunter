"""Десктоп-интерфейс управления чатами: что читать, куда делать AI-рассылку.

Запуск: python main_gui.py
Внутри работает весь стек (Telethon + бот уведомлений + планировщик),
поэтому НЕЛЬЗЯ одновременно запускать python main.py — конфликт сессии и polling.

Первую авторизацию Telegram (ввод кода) делайте из консоли:
код запрашивается прямо в окне терминала, из которого запущена программа.
"""
import asyncio
import logging
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main_gui")

os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

import config
from config import NO_RULES_MARKER, OWNER_TG_ID
import database as db
from user_client import UserClient
from notification_bot import NotificationBot
from scheduler import MessageScheduler

TIMES_RE = re.compile(r"^\s*([01]?\d|2[0-3]):[0-5]\d(\s*,\s*([01]?\d|2[0-3]):[0-5]\d)*\s*$")


class ServerSync:
    """Синхронизация с сервером через HTTP API."""
    
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session = None
    
    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session
    
    async def fetch_chats(self) -> list[dict] | None:
        """Получить список чатов с сервера."""
        if not self.server_url:
            return None
        try:
            session = await self._get_session()
            async with session.get(f"{self.server_url}/api/chats") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("chats", [])
        except Exception as e:
            logger.error(f"Ошибка получения чатов с сервера: {e}")
        return None
    
    async def update_chat(self, chat_data: dict) -> bool:
        """Обновить чат на сервере."""
        if not self.server_url:
            return False
        try:
            session = await self._get_session()
            async with session.post(f"{self.server_url}/api/chats", json=chat_data) as resp:
                return resp.status == 200
        except Exception as e:
            logger.error(f"Ошибка обновления чата на сервере: {e}")
        return False
    
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


def check_config() -> list[str]:
    errors = []
    if not config.TG_API_ID or not config.TG_API_HASH:
        errors.append("TG_API_ID и TG_API_HASH не настроены")
    if not config.NOTIF_BOT_TOKEN:
        errors.append("NOTIF_BOT_TOKEN не настроен")
    if not config.OWNER_TG_ID:
        errors.append("OWNER_TG_ID не настроен")
    return errors


class Backend:
    """Фоновый поток с asyncio: Telethon + бот + планировщик."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.user_client: UserClient | None = None
        self.notif_bot: NotificationBot | None = None
        self.scheduler: MessageScheduler | None = None
        self.started = threading.Event()
        self.error: Exception | None = None
        self._bot_task = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="backend")

    def start(self):
        self.thread.start()

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._startup())
        except Exception as e:
            logger.error(f"Ошибка запуска бэкенда: {e}", exc_info=True)
            self.error = e
            self.started.set()
            return
        self.started.set()
        try:
            self.loop.run_forever()
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            except Exception:
                pass
            self.loop.close()
            logger.info("Цикл бэкенда завершён")

    async def _startup(self):
        await db.init_db()

        self.notif_bot = NotificationBot()

        async def notification_router(**kwargs):
            category = kwargs.get("category", "")
            if category in ("HOT_COLD", "WARM_COLD"):
                await self.notif_bot.notify_cold_lead(**kwargs)
            else:
                await self.notif_bot.notify_lead(**kwargs)

        self.user_client = UserClient(notification_callback=notification_router)
        self.notif_bot.user_client = self.user_client

        async def broadcast_notify(text: str):
            await self.notif_bot.bot.send_message(OWNER_TG_ID, text)

        self.scheduler = MessageScheduler(
            user_client=self.user_client,
            broadcast_notify_callback=broadcast_notify,
        )

        await self.user_client.start()
        await self.scheduler.start()
        self._bot_task = asyncio.ensure_future(self.notif_bot.start())
        logger.info("Бэкенд запущен")

    async def _shutdown(self):
        logger.info("Остановка бэкенда...")
        try:
            if self.scheduler:
                await self.scheduler.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки планировщика: {e}")
        try:
            if self.user_client:
                await self.user_client.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки Telethon: {e}")
        try:
            if self._bot_task:
                self._bot_task.cancel()
            if self.notif_bot:
                await self.notif_bot.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки бота: {e}")

    def shutdown(self, timeout: float = 15.0):
        """Синхронная остановка из GUI-потока."""
        if not self.loop or not self.loop.is_running():
            return
        fut = asyncio.run_coroutine_threadsafe(self._shutdown(), self.loop)
        try:
            fut.result(timeout=timeout)
        except Exception as e:
            logger.error(f"Остановка бэкенда не завершилась чисто: {e}")
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)


class App:
    def __init__(self, root: tk.Tk, backend: Backend):
        self.root = root
        self.backend = backend
        self.chats: dict[int, dict] = {}
        self.current_chat_id: int | None = None
        self.server_sync = ServerSync(config.SERVER_URL)

        root.title("Lead Hunter — управление чатами")
        root.geometry("1000x600")
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_ui()
        self._wait_backend()

    # === UI ===

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # Левая часть: список чатов
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Поле поиска
        search_frame = ttk.Frame(left)
        search_frame.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(search_frame, text="🔍 Поиск чата:").pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=30)
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_entry.bind("<KeyRelease>", self._filter_chats)

        columns = ("read", "broadcast", "times")
        self.tree = ttk.Treeview(left, columns=columns, show="tree headings",
                                 selectmode="browse")
        self.tree.heading("#0", text="Чат")
        self.tree.heading("read", text="Читать")
        self.tree.heading("broadcast", text="Рассылка")
        self.tree.heading("times", text="Время")
        self.tree.column("#0", width=340, anchor=tk.W)
        self.tree.column("read", width=70, anchor=tk.CENTER)
        self.tree.column("broadcast", width=80, anchor=tk.CENTER)
        self.tree.column("times", width=110, anchor=tk.CENTER)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.LEFT, fill=tk.Y)

        # Правая часть: настройки выбранного чата
        right = ttk.Frame(main, padding=(12, 0, 0, 0), width=380)
        right.pack(side=tk.LEFT, fill=tk.BOTH)
        right.pack_propagate(False)

        self.chat_title_var = tk.StringVar(value="Выберите чат слева")
        ttk.Label(right, textvariable=self.chat_title_var,
                  font=("", 11, "bold"), wraplength=360).pack(anchor=tk.W, pady=(0, 8))

        self.read_var = tk.BooleanVar()
        self.bcast_var = tk.BooleanVar()
        ttk.Checkbutton(right, text="Читать (поиск лидов в этом чате)",
                        variable=self.read_var).pack(anchor=tk.W)
        ttk.Checkbutton(right, text="Рассылка (AI пишет рекламу в этот чат)",
                        variable=self.bcast_var).pack(anchor=tk.W, pady=(2, 8))

        ttk.Label(right, text="Правила чата (вставьте текст правил вручную):").pack(anchor=tk.W)
        self.rules_text = tk.Text(right, height=10, width=44, wrap=tk.WORD)
        self.rules_text.pack(fill=tk.X, pady=(2, 4))
        
        # Контекстное меню для копирования/вставки
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Копировать", command=self._copy_text)
        self.context_menu.add_command(label="Вставить", command=self._paste_text)
        self.context_menu.add_command(label="Вырезать", command=self._cut_text)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Очистить", command=self._clear_text)
        
        self.rules_text.bind("<Button-3>", self._show_context_menu)  # Правая кнопка мыши
        self.rules_text.bind("<Button-2>", self._show_context_menu)  # Для macOS
        
        # Горячие клавиши
        self.root.bind("<Control-v>", lambda e: self._paste_text())
        self.root.bind("<Control-c>", lambda e: self._copy_text())
        self.root.bind("<Control-x>", lambda e: self._cut_text())
        self.rules_text.bind("<Control-v>", lambda e: self._paste_text())
        self.rules_text.bind("<Control-c>", lambda e: self._copy_text())
        self.rules_text.bind("<Control-x>", lambda e: self._cut_text())

        self.no_rules_var = tk.BooleanVar()
        ttk.Checkbutton(right, text="Правил нет (я проверил — в чате нет правил)",
                        variable=self.no_rules_var,
                        command=self._toggle_no_rules).pack(anchor=tk.W)

        ttk.Label(right, text="Время рассылки (например: 10:00, 19:30):").pack(
            anchor=tk.W, pady=(8, 0))
        self.times_var = tk.StringVar()
        ttk.Entry(right, textvariable=self.times_var, width=30).pack(anchor=tk.W, pady=(2, 12))

        btns = ttk.Frame(right)
        btns.pack(fill=tk.X)
        self.save_btn = ttk.Button(btns, text="💾 Сохранить", command=self.save_chat)
        self.save_btn.pack(side=tk.LEFT)
        self.test_btn = ttk.Button(btns, text="🧪 Тест: сгенерировать сообщение",
                                   command=self.test_broadcast)
        self.test_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.sync_btn = ttk.Button(right, text="🔄 Обновить список чатов из Telegram",
                                   command=self.sync_dialogs)
        self.sync_btn.pack(anchor=tk.W, pady=(16, 0))

        # Статус-бар
        self.status_var = tk.StringVar(value="Запуск бэкенда (Telethon + бот)...")
        status = ttk.Label(self.root, textvariable=self.status_var,
                           relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in (self.save_btn, self.test_btn, self.sync_btn):
            btn.configure(state=state)

    def _toggle_no_rules(self):
        if self.no_rules_var.get():
            self.rules_text.configure(state=tk.DISABLED, bg="#f0f0f0")
        else:
            self.rules_text.configure(state=tk.NORMAL, bg="white")

    def _show_context_menu(self, event):
        """Показывает контекстное меню при правом клике."""
        self.context_menu.post(event.x_root, event.y_root)

    def _copy_text(self):
        """Копирует выделенный текст."""
        try:
            selected = self.rules_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        except:
            pass  # Ничего не выделено

    def _paste_text(self):
        """Вставляет текст из буфера обмена."""
        try:
            text = self.root.clipboard_get()
            self.rules_text.insert(tk.INSERT, text)
        except:
            pass  # Буфер пуст или недоступен

    def _cut_text(self):
        """Вырезает выделенный текст."""
        try:
            selected = self.rules_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
            self.rules_text.delete("sel.first", "sel.last")
        except:
            pass  # Ничего не выделено

    def _clear_text(self):
        """Очищает поле."""
        self.rules_text.delete("1.0", tk.END)

    def _filter_chats(self, _event=None):
        """Фильтрует список чатов по строке поиска."""
        search_text = self.search_var.get().lower()
        selected = self.current_chat_id
        
        # Сохраняем все чаты
        if not hasattr(self, '_all_chats'):
            self._all_chats = list(self.chats.values())
        
        # Фильтруем
        filtered_chats = self._all_chats
        if search_text:
            filtered_chats = [c for c in self._all_chats 
                            if search_text in c["chat_name"].lower()]
        
        # Обновляем дерево
        self.tree.delete(*self.tree.get_children())
        for c in filtered_chats:
            read_mark = "✓" if c.get("is_monitored") else "–"
            bcast_mark = "✓" if c.get("is_broadcast") else "–"
            times = c.get("broadcast_times") or ""
            self.tree.insert("", tk.END, iid=str(c["chat_id"]),
                            text=c["chat_name"],
                            values=(read_mark, bcast_mark, times))
        
        # Восстанавливаем выделение если возможно
        if selected and str(selected) in self.tree.get_children():
            self.tree.selection_set(str(selected))

    # === Взаимодействие с бэкендом ===

    def run_async(self, coro, on_done=None, on_error=None):
        """Запускает корутину в фоновом цикле, результат — в GUI-поток."""
        fut = asyncio.run_coroutine_threadsafe(coro, self.backend.loop)

        def poll():
            if not fut.done():
                self.root.after(150, poll)
                return
            try:
                result = fut.result()
            except Exception as e:
                logger.error(f"Ошибка фоновой операции: {e}", exc_info=True)
                if on_error:
                    on_error(e)
                else:
                    messagebox.showerror("Ошибка", str(e))
                return
            if on_done:
                on_done(result)

        poll()

    def _wait_backend(self):
        if not self.backend.started.is_set():
            self.root.after(300, self._wait_backend)
            return
        if self.backend.error:
            self.status_var.set(f"❌ Ошибка запуска: {self.backend.error}")
            messagebox.showerror(
                "Ошибка запуска",
                f"Бэкенд не запустился:\n{self.backend.error}\n\n"
                f"Проверьте .env и подключение к интернету.\n"
                f"Если требуется авторизация Telegram — запустите из консоли."
            )
            return
        self.status_var.set("✅ Работает: мониторинг чатов и рассылки активны")
        self._set_controls_enabled(True)
        
        # Синхронизируем чаты из Telegram при первом запуске
        self._sync_chats_from_telegram()
        self.reload_chat_list()
    
    def _sync_chats_from_telegram(self):
        """Синхронизирует чаты из Telegram с локальной БД."""
        async def do_sync():
            try:
                dialogs = await self.backend.user_client.fetch_dialogs()
                for d in dialogs:
                    await db.upsert_dialog_chat(d["chat_id"], d["chat_name"])
                logger.info(f"Синхронизировано {len(dialogs)} чатов из Telegram")
            except Exception as e:
                logger.error(f"Ошибка синхронизации чатов: {e}")
        
        def done(_):
            pass
        
        self.run_async(do_sync(), on_done=done)

    # === Данные ===

    def reload_chat_list(self):
        def done(chats):
            self.chats = {c["chat_id"]: c for c in chats}
            self._all_chats = list(chats)  # Сохраняем для фильтрации
            selected = self.current_chat_id
            self.tree.delete(*self.tree.get_children())
            for c in chats:
                read_mark = "✓" if c.get("is_monitored") else "–"
                bcast_mark = "✓" if c.get("is_broadcast") else "–"
                times = c.get("broadcast_times") or ""
                self.tree.insert("", tk.END, iid=str(c["chat_id"]),
                                 text=c["chat_name"],
                                 values=(read_mark, bcast_mark, times))
            if selected and str(selected) in self.tree.get_children():
                self.tree.selection_set(str(selected))
            # Применяем фильтр если есть текст поиска
            if self.search_var.get():
                self._filter_chats()

        # Если настроен SERVER_URL, загружаем с сервера, иначе из локальной БД
        if config.SERVER_URL:
            self.run_async(self.server_sync.fetch_chats(), on_done=done)
        else:
            self.run_async(db.get_all_chats(), on_done=done)

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        chat_id = int(sel[0])
        chat = self.chats.get(chat_id)
        if not chat:
            return
        self.current_chat_id = chat_id
        self.chat_title_var.set(chat["chat_name"])
        self.read_var.set(bool(chat.get("is_monitored")))
        self.bcast_var.set(bool(chat.get("is_broadcast")))

        rules = chat.get("chat_rules") or ""
        self.no_rules_var.set(rules == NO_RULES_MARKER)
        self.rules_text.configure(state=tk.NORMAL, bg="white")
        self.rules_text.delete("1.0", tk.END)
        if rules and rules != NO_RULES_MARKER:
            self.rules_text.insert("1.0", rules)
        self._toggle_no_rules()

        self.times_var.set(chat.get("broadcast_times") or "")

    # === Кнопки ===

    def save_chat(self):
        if not self.current_chat_id:
            messagebox.showwarning("Нет выбора", "Сначала выберите чат в списке слева.")
            return
        chat_id = self.current_chat_id

        if self.no_rules_var.get():
            rules = NO_RULES_MARKER
        else:
            rules = self.rules_text.get("1.0", tk.END).strip() or None

        times = self.times_var.get().strip()
        if self.bcast_var.get():
            if not times:
                messagebox.showwarning(
                    "Нет времени рассылки",
                    "Включена рассылка, но не указано время.\n"
                    "Впишите время в формате: 10:00, 19:30"
                )
                return
            if not TIMES_RE.match(times):
                messagebox.showwarning(
                    "Неверный формат времени",
                    "Время должно быть в формате ЧЧ:ММ через запятую.\n"
                    "Пример: 10:00, 19:30"
                )
                return
            if not rules:
                messagebox.showwarning(
                    "Нет правил чата",
                    "Включена рассылка, но правила чата не заполнены.\n"
                    "Вставьте правила чата или отметьте «Правил нет».\n"
                    "Без этого рассылка отправляться НЕ будет (защита от бана)."
                )
                return

        async def do_save():
            # Сохраняем в локальную БД
            await db.update_chat_flags(
                chat_id,
                int(self.read_var.get()),
                int(self.bcast_var.get()),
                rules,
                times or None,
            )
            await self.backend.user_client.reload_chats()
            await self.backend.scheduler.reload_jobs()
            
            # Синхронизируем с сервером если настроен SERVER_URL
            if config.SERVER_URL:
                chat_data = {
                    "chat_id": chat_id,
                    "chat_name": self.chats[chat_id].get("chat_name"),
                    "is_monitored": self.read_var.get(),
                    "is_broadcast": self.bcast_var.get(),
                    "chat_rules": rules,
                    "broadcast_times": times or None,
                }
                await self.server_sync.update_chat(chat_data)

        def done(_):
            self.status_var.set(f"💾 Сохранено: {self.chats[chat_id]['chat_name']}")
            self.reload_chat_list()

        self.run_async(do_save(), on_done=done)

    def test_broadcast(self):
        if not self.current_chat_id:
            messagebox.showwarning("Нет выбора", "Сначала выберите чат в списке слева.")
            return
        chat_id = self.current_chat_id
        self.status_var.set("🧪 Генерация тестового сообщения (AI)...")
        self.test_btn.configure(state="disabled")

        def done(result):
            self.test_btn.configure(state="normal")
            self.status_var.set("✅ Работает")
            if result is None:
                messagebox.showerror("Ошибка", "AI не смог сгенерировать сообщение.\n"
                                               "Проверьте, что AI-провайдер доступен.")
                return
            if result["skip"]:
                messagebox.showinfo(
                    "AI пропустил бы отправку",
                    f"Сообщение НЕ было бы отправлено.\n\nПричина: {result['reason']}"
                )
            else:
                messagebox.showinfo(
                    "Тестовое сообщение (НЕ отправлено)",
                    f"AI сгенерировал такой текст:\n\n{result['message']}"
                )

        def err(e):
            self.test_btn.configure(state="normal")
            self.status_var.set("✅ Работает")
            messagebox.showerror("Ошибка", str(e))

        self.run_async(self.backend.scheduler.preview_broadcast(chat_id),
                       on_done=done, on_error=err)

    def sync_dialogs(self):
        self.status_var.set("🔄 Загрузка списка чатов из Telegram...")
        self.sync_btn.configure(state="disabled")

        async def do_sync():
            dialogs = await self.backend.user_client.fetch_dialogs()
            for d in dialogs:
                await db.upsert_dialog_chat(d["chat_id"], d["chat_name"])
            return len(dialogs)

        def done(count):
            self.sync_btn.configure(state="normal")
            self.status_var.set(f"✅ Загружено чатов: {count}")
            self.reload_chat_list()

        def err(e):
            self.sync_btn.configure(state="normal")
            self.status_var.set("❌ Ошибка загрузки чатов")
            messagebox.showerror("Ошибка", str(e))

        self.run_async(do_sync(), on_done=done, on_error=err)

    # === Закрытие ===

    def on_close(self):
        self.status_var.set("Остановка... (закрытие Telegram-сессии)")
        self._set_controls_enabled(False)
        self.root.update_idletasks()

        done_flag = threading.Event()

        def stop_backend():
            self.backend.shutdown()
            done_flag.set()

        # shutdown блокирует до 15с — выполняем в отдельном потоке;
        # destroy() вызываем только из GUI-потока (Tkinter не потокобезопасен)
        threading.Thread(target=stop_backend, daemon=True).start()

        def poll():
            if done_flag.is_set():
                self.root.destroy()
            else:
                self.root.after(200, poll)

        poll()


def main():
    errors = check_config()
    if errors:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Ошибка конфигурации",
            "Исправьте .env:\n\n" + "\n".join(f"• {e}" for e in errors)
        )
        return

    backend = Backend()
    backend.start()

    root = tk.Tk()
    App(root, backend)
    root.mainloop()


if __name__ == "__main__":
    main()
