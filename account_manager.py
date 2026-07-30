# Year: 2026
import wx
import json
import os
import logging
from typing import Optional
from utils import get_resource_path

logger = logging.getLogger(__name__)

ACCOUNTS_FILE = get_resource_path("accounts.json")
AUTH_DATA_FILE = get_resource_path("auth_data.json")

class AccountManager:
    """Управляет учётными записями Яндекс.Музыки.

    Хранит список аккаунтов в accounts.json. Каждый аккаунт содержит
    имя и OAuth-токен. Также отслеживает последний использованный аккаунт.
    При удалении аккаунта чистит auth_data.json, если токен совпадает.
    """

    def __init__(self):
        self.accounts = {}
        self.last_used = None
        self.load()

    def load(self):
        """Загружает список аккаунтов из accounts.json."""
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.accounts = data.get("accounts", {})
                    self.last_used = data.get("last_used")
                logger.debug("Файл аккаунтов загружен (%d записей).", len(self.accounts))
            except Exception:
                logger.exception("Ошибка загрузки файла аккаунтов '%s'.", ACCOUNTS_FILE)

    def save(self):
        """Сохраняет список аккаунтов в accounts.json."""
        try:
            with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
                json.dump({"accounts": self.accounts, "last_used": self.last_used}, f, ensure_ascii=False, indent=4)
            logger.debug("Файл аккаунтов сохранён.")
        except Exception:
            logger.exception("Ошибка сохранения файла аккаунтов '%s'.", ACCOUNTS_FILE)

    def add_account(self, name: str, token: str) -> str:
        """Добавляет новую учётную запись. Возвращает её ID."""
        account_id = os.urandom(8).hex()
        self.accounts[account_id] = {"name": name, "token": token}
        if not self.last_used:
            self.last_used = account_id
        self.save()
        logger.info("Аккаунт '%s' добавлен (id=%s).", name, account_id)
        return account_id

    def update_account(self, account_id: str, name: str = None, token: str = None):
        """Обновляет имя и/или токен существующей учётной записи."""
        if account_id in self.accounts:
            if name:
                self.accounts[account_id]["name"] = name
            if token:
                self.accounts[account_id]["token"] = token
            self.save()
            logger.info("Аккаунт id=%s обновлён.", account_id)

    def delete_account(self, account_id: str):
        """Удаляет учётную запись. Если аккаунтов не осталось — удаляет оба файла."""
        if account_id in self.accounts:
            name = self.accounts[account_id].get("name", account_id)
            deleted_token = self.accounts[account_id].get("token")
            del self.accounts[account_id]
            if self.last_used == account_id:
                self.last_used = list(self.accounts.keys())[0] if self.accounts else None
            if self.accounts:
                self.save()
            else:
                self._remove_accounts_file()
            self._clean_auth_data(deleted_token)
            logger.info("Аккаунт '%s' (id=%s) удалён.", name, account_id)

    def _remove_accounts_file(self):
        """Удаляет accounts.json, если в нём нет записей."""
        try:
            if os.path.exists(ACCOUNTS_FILE):
                os.remove(ACCOUNTS_FILE)
                logger.debug("Файл аккаунтов '%s' удалён (нет записей).", ACCOUNTS_FILE)
        except Exception:
            logger.exception("Ошибка удаления файла аккаунтов '%s'.", ACCOUNTS_FILE)

    def _clean_auth_data(self, token=None):
        """Удаляет auth_data.json, если токен совпадает или аккаунтов не осталось."""
        try:
            if not os.path.exists(AUTH_DATA_FILE):
                return
            if token:
                with open(AUTH_DATA_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if data.get("token") == token:
                    os.remove(AUTH_DATA_FILE)
                    logger.debug("Файл токена '%s' удалён (токен совпал с удалённым аккаунтом).", AUTH_DATA_FILE)
                    return
            if not self.accounts:
                if os.path.exists(AUTH_DATA_FILE):
                    os.remove(AUTH_DATA_FILE)
                    logger.debug("Файл токена '%s' удалён (нет аккаунтов).", AUTH_DATA_FILE)
        except Exception:
            logger.exception("Ошибка обработки файла токена '%s'.", AUTH_DATA_FILE)

    def set_last_used(self, account_id: str):
        """Устанавливает аккаунт, использованный последним."""
        if account_id in self.accounts:
            self.last_used = account_id
            self.save()
            logger.debug("Последний использованный аккаунт: id=%s.", account_id)

    def get_last_used_token(self) -> Optional[str]:
        """Возвращает токен последнего использованного аккаунта или None."""
        if self.last_used and self.last_used in self.accounts:
            return self.accounts[self.last_used].get("token")
        return None


class AccountManagerDialog(wx.Dialog):
    def __init__(self, parent, account_mgr, auth_callback, switch_callback):
        super().__init__(parent, title="Менеджер учетных записей", size=(500, 400))
        self.account_mgr = account_mgr
        self.auth_callback = auth_callback
        self.switch_callback = switch_callback
        self.account_ids = []
        self.init_ui()

    def init_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)

        self.list_ctrl = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.list_ctrl.InsertColumn(0, "Имя учетной записи", width=450)
        self.list_ctrl.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context_menu)
        self.list_ctrl.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
        vbox.Add(self.list_ctrl, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        btn_add = wx.Button(self, label="Добавить аккаунт")
        btn_close = wx.Button(self, label="Закрыть")

        btn_add.Bind(wx.EVT_BUTTON, self.on_add)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))

        hbox.Add(btn_add, flag=wx.RIGHT, border=5)
        hbox.Add(btn_close)
        vbox.Add(hbox, flag=wx.ALL | wx.ALIGN_RIGHT, border=5)

        self.SetSizer(vbox)
        self.refresh_list()

    def refresh_list(self):
        self.list_ctrl.DeleteAllItems()
        self.account_ids.clear()

        for idx, (acc_id, data) in enumerate(self.account_mgr.accounts.items()):
            self.account_ids.append(acc_id)
            name = data.get("name", "Неизвестно")
            if acc_id == self.account_mgr.last_used:
                name += " (Активна сейчас)"
            self.list_ctrl.InsertItem(idx, name)

        if self.account_ids:
            self.list_ctrl.Select(0)
            self.list_ctrl.SetFocus()

    def on_add(self, event):
        self.auth_callback(existing_id=None)
        self.refresh_list()

    def on_context_menu(self, event):
        sel = self.list_ctrl.GetFirstSelected()
        if sel < 0 or sel >= len(self.account_ids):
            return

        acc_id = self.account_ids[sel]
        menu = wx.Menu()

        item_switch = menu.Append(wx.ID_ANY, "Переключиться на эту учетную запись")
        item_edit = menu.Append(wx.ID_ANY, "Изменить данные (переавторизация)")
        item_copy = menu.Append(wx.ID_ANY, "Скопировать токен доступа")
        menu.AppendSeparator()
        item_delete = menu.Append(wx.ID_ANY, "Удалить учетную запись")

        self.Bind(wx.EVT_MENU, lambda e: self.do_switch(acc_id), item_switch)
        self.Bind(wx.EVT_MENU, lambda e: self.do_edit(acc_id), item_edit)
        self.Bind(wx.EVT_MENU, lambda e: self.do_copy(acc_id), item_copy)
        self.Bind(wx.EVT_MENU, lambda e: self.do_delete(acc_id), item_delete)

        self.PopupMenu(menu)
        menu.Destroy()

    def do_switch(self, acc_id):
        self.account_mgr.set_last_used(acc_id)
        self.switch_callback(self.account_mgr.get_last_used_token())
        self.refresh_list()

    def do_edit(self, acc_id):
        self.auth_callback(existing_id=acc_id)
        self.refresh_list()

    def do_copy(self, acc_id):
        token = self.account_mgr.accounts[acc_id].get("token", "")
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(token))
            wx.TheClipboard.Close()
            wx.MessageBox("Токен скопирован в буфер обмена.", "Успешно")

    def do_delete(self, acc_id):
        dlg = wx.MessageDialog(self, "Точно удалить эту учетную запись?", "Подтверждение", wx.YES_NO)
        if dlg.ShowModal() == wx.ID_YES:
            self.account_mgr.delete_account(acc_id)
            self.refresh_list()
