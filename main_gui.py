"""Десктоп-интерфейс управления чатами (тонкий клиент).

Вся работа (Telethon, бот уведомлений, планировщик, AI) идёт на СЕРВЕРЕ.
Эта программа только управляет настройками через HTTP API сервера,
поэтому все изменения сохраняются на сервере и не слетают при перезапуске.

Настройка: в .env рядом с exe укажите
    SERVER_URL=http://82.202.170.14:8080
    API_TOKEN=<токен с сервера>

Запуск: python main_gui.py  или  LeadHunter.exe
"""
import json
import logging
import os
import re
import sys
import threading
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("gui.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main_gui")

# .env лежит рядом с exe (или со скриптом)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(BASE_DIR, ".env"))

SERVER_URL = os.getenv("SERVER_URL", "").rstrip("/")
API_TOKEN = os.getenv("API_TOKEN", "")
NO_RULES_MARKER = "NO_RULES"

TIMES_RE = re.compile(r"^\s*([01]?\d|2[0-3]):[0-5]\d(\s*,\s*([01]?\d|2[0-3]):[0-5]\d)*\s*$")


class ServerAPI:
    """HTTP-клиент к API сервера (urllib, без внешних зависимостей)."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token

    def _request(self, method: str, path: str, payload: dict | None = None,
                 timeout: int = 90) -> dict:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_chats(self) -> list[dict]:
        return self._request("GET", "/api/chats")["chats"]

    def update_chat(self, chat_data: dict) -> bool:
        return self._request("POST", "/api/chats", chat_data).get("success", False)

    def sync_dialogs(self) -> int:
        return self._request("POST", "/api/sync_dialogs", {}, timeout=180).get("count", 0)

    def preview_broadcast(self, chat_id: int) -> dict | None:
        return self._request("POST", "/api/preview_broadcast",
                             {"chat_id": chat_id}, timeout=180).get("result")

    def get_settings(self) -> dict:
        return self._request("GET", "/api/settings")

    def set_settings(self, settings: dict) -> bool:
        return self._request("POST", "/api/settings", settings).get("success", False)

    def get_status(self) -> dict:
        return self._request("GET", "/api/status", timeout=15)


class App:
    def __init__(self, root: tk.Tk, api: ServerAPI):
        self.root = root
        self.api = api
        self.chats: dict[int, dict] = {}
        self.current_chat_id: int | None = None
        self._all_chats: list[dict] = []

        root.title("Lead Hunter — управление чатами (сервер)")
        root.geometry("1000x620")

        self._build_ui()
        self._connect_to_server()

    # === UI ===

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # Левая часть: список чатов
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

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
                        variable=self.bcast_var).pack(anchor=tk.W, pady=(2, 4))

        # Пол разработчика — общая настройка, хранится на сервере
        self.gender_var = tk.StringVar(value="male")
        gender_frame = ttk.Frame(right)
        gender_frame.pack(anchor=tk.W, pady=(0, 8))
        ttk.Label(gender_frame, text="Пол разработчика:").pack(side=tk.LEFT)
        ttk.Radiobutton(gender_frame, text="Мужской", variable=self.gender_var,
                        value="male", command=self.save_gender).pack(side=tk.LEFT, padx=(8, 4))
        ttk.Radiobutton(gender_frame, text="Женский", variable=self.gender_var,
                        value="female", command=self.save_gender).pack(side=tk.LEFT)

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

        self.rules_text.bind("<Button-3>", self._show_context_menu)
        self.rules_text.bind("<Button-2>", self._show_context_menu)

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

        self.reload_btn = ttk.Button(right, text="⟳ Перечитать данные с сервера",
                                     command=self.reload_chat_list)
        self.reload_btn.pack(anchor=tk.W, pady=(6, 0))

        # Статус-бар
        self.status_var = tk.StringVar(value=f"Подключение к серверу {SERVER_URL}...")
        status = ttk.Label(self.root, textvariable=self.status_var,
                           relief=tk.SUNKEN, anchor=tk.W, padding=(6, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)

        self._set_controls_enabled(False)

    def _set_controls_enabled(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in (self.save_btn, self.test_btn, self.sync_btn, self.reload_btn):
            btn.configure(state=state)

    def _toggle_no_rules(self):
        if self.no_rules_var.get():
            self.rules_text.configure(state=tk.DISABLED, bg="#f0f0f0")
        else:
            self.rules_text.configure(state=tk.NORMAL, bg="white")

    def _show_context_menu(self, event):
        self.context_menu.post(event.x_root, event.y_root)

    def _copy_text(self):
        try:
            selected = self.rules_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
        except tk.TclError:
            pass

    def _paste_text(self):
        try:
            text = self.root.clipboard_get()
            self.rules_text.insert(tk.INSERT, text)
        except tk.TclError:
            pass

    def _cut_text(self):
        try:
            selected = self.rules_text.get("sel.first", "sel.last")
            self.root.clipboard_clear()
            self.root.clipboard_append(selected)
            self.rules_text.delete("sel.first", "sel.last")
        except tk.TclError:
            pass

    def _clear_text(self):
        self.rules_text.delete("1.0", tk.END)

    def _filter_chats(self, _event=None):
        search_text = self.search_var.get().lower()
        filtered = self._all_chats
        if search_text:
            filtered = [c for c in self._all_chats
                        if search_text in (c.get("chat_name") or "").lower()]
        self._fill_tree(filtered)

    def _fill_tree(self, chats: list[dict]):
        selected = self.current_chat_id
        self.tree.delete(*self.tree.get_children())
        for c in chats:
            read_mark = "✓" if c.get("is_monitored") else "–"
            bcast_mark = "✓" if c.get("is_broadcast") else "–"
            times = c.get("broadcast_times") or ""
            self.tree.insert("", tk.END, iid=str(c["chat_id"]),
                             text=c.get("chat_name") or str(c["chat_id"]),
                             values=(read_mark, bcast_mark, times))
        if selected and str(selected) in self.tree.get_children():
            self.tree.selection_set(str(selected))

    # === Работа с сервером (фоновые потоки, GUI не блокируется) ===

    def run_bg(self, func, on_done=None, on_error=None):
        """Выполняет func() в фоне, результат возвращает в GUI-поток."""
        def worker():
            try:
                result = func()
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    err = Exception("Сервер отклонил доступ (401): проверьте API_TOKEN в .env")
                else:
                    err = Exception(f"Ошибка сервера: HTTP {e.code}")
                logger.error(f"HTTP ошибка: {e}")
                self.root.after(0, lambda: (on_error or self._default_error)(err))
                return
            except urllib.error.URLError as e:
                err = Exception(f"Нет связи с сервером {SERVER_URL}:\n{e.reason}")
                logger.error(f"Сервер недоступен: {e}")
                self.root.after(0, lambda: (on_error or self._default_error)(err))
                return
            except Exception as e:
                logger.error(f"Ошибка фоновой операции: {e}", exc_info=True)
                self.root.after(0, lambda: (on_error or self._default_error)(e))
                return
            if on_done:
                self.root.after(0, lambda: on_done(result))

        threading.Thread(target=worker, daemon=True).start()

    def _default_error(self, e: Exception):
        self.status_var.set("❌ Ошибка связи с сервером")
        messagebox.showerror("Ошибка", str(e))

    def _connect_to_server(self):
        """Первое подключение: статус + настройки + список чатов."""
        def load():
            status = self.api.get_status()
            settings = self.api.get_settings()
            chats = self.api.get_chats()
            return status, settings, chats

        def done(result):
            status, settings, chats = result
            self.gender_var.set(settings.get("developer_gender", "male"))
            self._apply_chats(chats)
            self._set_controls_enabled(True)
            running = "✅ работает" if status.get("running") else "⚠️ Telegram-клиент остановлен"
            self.status_var.set(
                f"Сервер {SERVER_URL}: {running} | чатов отслеживается: "
                f"{status.get('monitored_chats', 0)} | действий сегодня: "
                f"{status.get('daily_actions', 0)}"
            )

        def err(e):
            self.status_var.set(f"❌ Сервер {SERVER_URL} недоступен")
            messagebox.showerror(
                "Нет связи с сервером",
                f"{e}\n\nПроверьте:\n"
                f"• SERVER_URL и API_TOKEN в файле .env рядом с программой\n"
                f"• что сервис lead-hunter запущен на сервере\n"
                f"• интернет-соединение"
            )
            # Разрешаем повторить попытку кнопкой
            self.reload_btn.configure(state="normal")

        self.run_bg(load, on_done=done, on_error=err)

    def _apply_chats(self, chats: list[dict]):
        self.chats = {c["chat_id"]: c for c in chats}
        self._all_chats = list(chats)
        if self.search_var.get():
            self._filter_chats()
        else:
            self._fill_tree(chats)

    def reload_chat_list(self):
        self.status_var.set("⟳ Загрузка данных с сервера...")
        def done(chats):
            self._apply_chats(chats)
            self._set_controls_enabled(True)
            self.status_var.set(f"✅ Данные загружены с сервера ({len(chats)} чатов)")
        self.run_bg(self.api.get_chats, on_done=done)

    def _on_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        chat_id = int(sel[0])
        chat = self.chats.get(chat_id)
        if not chat:
            return
        self.current_chat_id = chat_id
        self.chat_title_var.set(chat.get("chat_name") or str(chat_id))
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

    def save_gender(self):
        gender = self.gender_var.get()
        def done(_):
            label = "мужской" if gender == "male" else "женский"
            self.status_var.set(f"💾 Пол разработчика сохранён на сервере: {label}")
        self.run_bg(lambda: self.api.set_settings({"developer_gender": gender}),
                    on_done=done)

    def save_chat(self):
        if not self.current_chat_id:
            messagebox.showwarning("Нет выбора", "Сначала выберите чат в списке слева.")
            return
        chat_id = self.current_chat_id

        if self.no_rules_var.get():
            rules = NO_RULES_MARKER
        else:
            rules = self.rules_text.get("1.0", tk.END).strip()

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

        chat_data = {
            "chat_id": chat_id,
            "chat_name": self.chats[chat_id].get("chat_name"),
            "is_monitored": self.read_var.get(),
            "is_broadcast": self.bcast_var.get(),
            "chat_rules": rules,
            "broadcast_times": times,
        }

        self.status_var.set("💾 Сохранение на сервере...")
        def done(success):
            if success:
                self.status_var.set(f"💾 Сохранено на сервере: {self.chats[chat_id]['chat_name']}")
                self.reload_chat_list()
            else:
                messagebox.showerror("Ошибка", "Сервер не подтвердил сохранение.")

        self.run_bg(lambda: self.api.update_chat(chat_data), on_done=done)

    def test_broadcast(self):
        if not self.current_chat_id:
            messagebox.showwarning("Нет выбора", "Сначала выберите чат в списке слева.")
            return
        chat_id = self.current_chat_id
        self.status_var.set("🧪 Генерация тестового сообщения на сервере (AI)...")
        self.test_btn.configure(state="disabled")

        def done(result):
            self.test_btn.configure(state="normal")
            self.status_var.set("✅ Готово")
            if result is None:
                messagebox.showerror("Ошибка", "AI не смог сгенерировать сообщение.\n"
                                               "Проверьте, что AI-провайдер на сервере доступен.")
                return
            if result.get("skip"):
                messagebox.showinfo(
                    "AI пропустил бы отправку",
                    f"Сообщение НЕ было бы отправлено.\n\nПричина: {result.get('reason')}"
                )
            else:
                messagebox.showinfo(
                    "Тестовое сообщение (НЕ отправлено)",
                    f"AI сгенерировал такой текст:\n\n{result.get('message')}"
                )

        def err(e):
            self.test_btn.configure(state="normal")
            self.status_var.set("❌ Ошибка генерации")
            messagebox.showerror("Ошибка", str(e))

        self.run_bg(lambda: self.api.preview_broadcast(chat_id), on_done=done, on_error=err)

    def sync_dialogs(self):
        self.status_var.set("🔄 Сервер загружает список чатов из Telegram...")
        self.sync_btn.configure(state="disabled")

        def done(count):
            self.sync_btn.configure(state="normal")
            self.status_var.set(f"✅ Загружено чатов: {count}")
            self.reload_chat_list()

        def err(e):
            self.sync_btn.configure(state="normal")
            self.status_var.set("❌ Ошибка загрузки чатов")
            messagebox.showerror("Ошибка", str(e))

        self.run_bg(self.api.sync_dialogs, on_done=done, on_error=err)


def main():
    logger.info("Запуск GUI (тонкий клиент)")
    logger.info(f"Сервер: {SERVER_URL or 'НЕ НАСТРОЕН'}")

    root = tk.Tk()

    if not SERVER_URL:
        root.withdraw()
        messagebox.showerror(
            "Ошибка конфигурации",
            "Не настроен адрес сервера.\n\n"
            "Создайте файл .env рядом с программой и укажите:\n\n"
            "SERVER_URL=http://82.202.170.14:8080\n"
            "API_TOKEN=<токен с сервера>"
        )
        return

    App(root, ServerAPI(SERVER_URL, API_TOKEN))
    root.mainloop()
    logger.info("GUI закрыт")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Фатальная ошибка GUI: {e}", exc_info=True)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Фатальная ошибка", str(e))
        except Exception:
            pass
        input("Нажмите Enter для выхода...")
