# Year: 2026
import wx
import wx.adv
import wx.html2
import os
import threading
import subprocess
import logging
import speech
from utils import get_resource_path
from logger import setup_logger, log_system_info
from yandex_api import YandexMusicManager
from bass_player import BassPlayer
from config_manager import ConfigManager
from account_manager import AccountManager, AccountManagerDialog
from oauth_webview import BrowserAuthDialog, _IsolatedCookieEnv, _clear_webview_session
from exceptions import AuthError, NetworkError
from version import VERSION

logger = logging.getLogger(__name__)

def play_ui_sound(filename):
    path = get_resource_path(os.path.join('sounds', filename))
    if os.path.exists(path):
        sound = wx.adv.Sound(path)
        if sound.IsOk():
            sound.Play(wx.adv.SOUND_ASYNC)

class SimpleBrowserDialog(wx.Dialog):
    """Простой просмотрщик веб-страниц во встроенном браузере.

    Используется для показа страницы подтверждения кода устройства
    (yandex.ru/device) и других вспомогательных URL.
    Поддерживает Alt+F4 и Escape для закрытия.

    Каждый экземпляр использует изолированную временную папку для кук,
    чтобы сессия предыдущей авторизации не влияла на новую.
    """
    def __init__(self, parent, url, title="Авторизация через браузер"):
        super().__init__(
            parent, title=title, size=(800, 600),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL,
        )
        self._cookie_env = _IsolatedCookieEnv()
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.browser = wx.html2.WebView.New(self)
        _clear_webview_session(self.browser)
        self.browser.LoadURL(url)
        sizer.Add(self.browser, 1, wx.EXPAND)
        btn_close = wx.Button(self, label="Закрыть")
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        sizer.Add(btn_close, flag=wx.ALL | wx.ALIGN_CENTER, border=5)
        self.SetSizer(sizer)
        self.Centre()
        btn_close.SetFocus()

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_F4 and event.AltDown():
            self.Close()
            return
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def MSWWindowProc(self, msg, wParam, lParam):
        if msg == 0x0112 and (wParam & 0xFFF0) == 0xF060:
            self.Close()
            return 0
        return super().MSWWindowProc(msg, wParam, lParam)

    def Destroy(self):
        self._cookie_env.cleanup()
        super().Destroy()

class TrackListCtrl(wx.ListCtrl):
    def __init__(self, parent):
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_VIRTUAL | wx.LC_NO_HEADER)
        self.InsertColumn(0, "Элемент списка", width=750)
        self.data = []
        self.playing_index = -1
        self.status_text = ""

    def OnGetItemText(self, item, column):
        if item < 0 or item >= len(self.data):
            return ""
        
        obj = self.data[item]
        prefix = ""
        if item == self.playing_index and self.status_text:
            prefix = f"{self.status_text} "
            
        type_name = type(obj).__name__
        is_album = type_name == 'Album' or (hasattr(obj, 'track_count') and hasattr(obj, 'title'))
        is_track = type_name == 'Track' or (hasattr(obj, 'artists') and hasattr(obj, 'title') and not is_album)
        is_artist = type_name == 'Artist' or (hasattr(obj, 'name') and not is_album and not is_track)

        if is_album:
            year_str = f" ({obj.year})" if getattr(obj, 'year', None) else ""
            count_str = f" [Треков: {obj.track_count}]" if getattr(obj, 'track_count', None) else ""
            base_str = f"Альбом: {obj.title}{year_str}{count_str}"
        elif is_track:
            artists = ", ".join([a.name for a in (obj.artists or [])])
            base_str = f"{artists} - {obj.title}"
        elif is_artist:
            base_str = getattr(obj, 'name', str(obj))
        else:
            base_str = getattr(obj, 'title', getattr(obj, 'name', str(obj)))
            
        return prefix + base_str

class SettingsDialog(wx.Dialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Настройки", size=(450, 550))
        self.config = config
        self.init_ui()

    def init_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)
        notebook = wx.Notebook(self)

        panel_general = wx.Panel(notebook)
        vbox_gen = wx.BoxSizer(wx.VERTICAL)

        vbox_gen.Add(wx.StaticText(panel_general, label="Папка для загрузок:"), flag=wx.ALL, border=5)
        hbox_dir = wx.BoxSizer(wx.HORIZONTAL)
        self.dir_picker = wx.DirPickerCtrl(panel_general, path=self.config.get("download_dir", ""), message="Выберите папку для скачивания")
        hbox_dir.Add(self.dir_picker, proportion=1, flag=wx.EXPAND)
        vbox_gen.Add(hbox_dir, flag=wx.ALL | wx.EXPAND, border=5)

        self.cb_detailed_errors = wx.CheckBox(panel_general, label="Выводить подробную техническую информацию об ошибках")
        self.cb_detailed_errors.SetValue(self.config.get("detailed_errors", False))
        vbox_gen.Add(self.cb_detailed_errors, flag=wx.ALL, border=5)

        self.cb_show_dl_dialog = wx.CheckBox(panel_general, label="Показывать диалоговое окно после завершения загрузки")
        self.cb_show_dl_dialog.SetValue(self.config.get("show_download_dialog", True))
        vbox_gen.Add(self.cb_show_dl_dialog, flag=wx.ALL, border=5)
        
        self.cb_rem_cursor = wx.CheckBox(panel_general, label="Запоминать позицию курсора в списке при подгрузке")
        self.cb_rem_cursor.SetValue(self.config.get("remember_cursor", True))
        vbox_gen.Add(self.cb_rem_cursor, flag=wx.ALL, border=5)
        
        vbox_gen.Add(wx.StaticText(panel_general, label="Уровень логирования:"), flag=wx.LEFT | wx.TOP, border=5)
        self.cb_log_level = wx.ComboBox(panel_general, choices=["INFO", "DEBUG", "WARNING", "ERROR"], style=wx.CB_READONLY)
        self.cb_log_level.SetValue(self.config.get("log_level", "INFO"))
        vbox_gen.Add(self.cb_log_level, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.cb_clear_logs = wx.CheckBox(panel_general, label="Очищать логи при запуске программы")
        self.cb_clear_logs.SetValue(self.config.get("clear_logs_on_startup", True))
        vbox_gen.Add(self.cb_clear_logs, flag=wx.ALL, border=5)

        panel_general.SetSizer(vbox_gen)

        panel_speech = wx.Panel(notebook)
        vbox_speech = wx.BoxSizer(wx.VERTICAL)

        self.cb_enable_speech = wx.CheckBox(panel_speech, label="Включить программное озвучивание (речь)")
        self.cb_enable_speech.SetValue(self.config.get("enable_speech", True))
        vbox_speech.Add(self.cb_enable_speech, flag=wx.ALL, border=5)

        self.cb_enable_sapi5 = wx.CheckBox(panel_speech, label="Разрешить использование SAPI 5")
        self.cb_enable_sapi5.SetValue(self.config.get("enable_sapi5", True))
        vbox_speech.Add(self.cb_enable_sapi5, flag=wx.LEFT | wx.BOTTOM, border=20)

        self.cb_speech_media = wx.CheckBox(panel_speech, label="Озвучивать статус плеера")
        self.cb_speech_media.SetValue(self.config.get("speech_media_state", True))
        vbox_speech.Add(self.cb_speech_media, flag=wx.LEFT | wx.BOTTOM, border=20)

        self.cb_speech_general = wx.CheckBox(panel_speech, label="Озвучивать общие события")
        self.cb_speech_general.SetValue(self.config.get("speech_general", True))
        vbox_speech.Add(self.cb_speech_general, flag=wx.LEFT | wx.BOTTOM, border=20)

        self.cb_enable_speech.Bind(wx.EVT_CHECKBOX, self.on_speech_toggle)
        self.on_speech_toggle(None) 
        panel_speech.SetSizer(vbox_speech)

        notebook.AddPage(panel_general, "Общие")
        notebook.AddPage(panel_speech, "Речь")
        vbox.Add(notebook, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)

        hbox_btns = wx.BoxSizer(wx.HORIZONTAL)
        btn_save = wx.Button(self, label="Сохранить")
        btn_cancel = wx.Button(self, label="Отмена")
        
        btn_save.Bind(wx.EVT_BUTTON, self.on_save)
        btn_cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        
        hbox_btns.Add(btn_save, flag=wx.RIGHT, border=5)
        hbox_btns.Add(btn_cancel)
        vbox.Add(hbox_btns, flag=wx.ALL | wx.ALIGN_RIGHT, border=5)

        self.SetSizer(vbox)

    def on_speech_toggle(self, event):
        is_enabled = self.cb_enable_speech.GetValue()
        self.cb_enable_sapi5.Enable(is_enabled)
        self.cb_speech_media.Enable(is_enabled)
        self.cb_speech_general.Enable(is_enabled)

    def on_save(self, event):
        self.config.set("download_dir", self.dir_picker.GetPath())
        self.config.set("detailed_errors", self.cb_detailed_errors.GetValue())
        self.config.set("show_download_dialog", self.cb_show_dl_dialog.GetValue())
        self.config.set("remember_cursor", self.cb_rem_cursor.GetValue())
        self.config.set("enable_speech", self.cb_enable_speech.GetValue())
        self.config.set("enable_sapi5", self.cb_enable_sapi5.GetValue())
        self.config.set("speech_media_state", self.cb_speech_media.GetValue())
        self.config.set("speech_general", self.cb_speech_general.GetValue())
        self.config.set("log_level", self.cb_log_level.GetValue())
        self.config.set("clear_logs_on_startup", self.cb_clear_logs.GetValue())
        self.EndModal(wx.ID_OK)


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title="y-music-blind", size=(700, 650))
        
        self.config = ConfigManager()
        setup_logger(self.config)
        log_system_info()
        logger.info("Программа запущена.")
        
        # Строка состояния для визуального отображения событий
        self.statusbar = self.CreateStatusBar(1)
        self.statusbar.SetStatusText("Готово к работе")
        
        self.account_mgr = AccountManager()
        self.api = YandexMusicManager()
        self.player = BassPlayer()
        self.player.set_volume(self.config.get("volume", 0.5))
        
        self.current_page = 0
        self.view_mode = 'search'
        self.current_artist_id = None
        self.current_genre_id = None
        
        self.is_track_active = False 
        self.current_status = ""
        self.repeat_enabled = False 
        self.seen_ids = set()

        # Флаг, что фоновая загрузка жанровых треков уже идёт.
        # Защищает от двойного нажатия Enter/кнопки во время ожидания.
        self._genre_loading = False
        
        self.init_menu()
        self.init_ui()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(500) 
        
        wx.CallAfter(self.check_auth)

    def safe_speak(self, text, category="general", interrupt=True):
        # 1. Обновляем визуальный интерфейс (потокобезопасно)
        if text:
            wx.CallAfter(self.statusbar.SetStatusText, text)
            
        # 2. Логика произношения (речь)
        if not self.config.get("enable_speech", True):
            return
        if category == "media" and not self.config.get("speech_media_state", True):
            return
        if category == "general" and not self.config.get("speech_general", True):
            return
            
        speech.speak(text, interrupt, self.config)

    def init_menu(self):
        menubar = wx.MenuBar()
        
        # 1. Menu: File
        file_menu = wx.Menu()
        item_accounts = file_menu.Append(wx.ID_ANY, "Менеджер учетных записей\tCtrl+M", "Управление аккаунтами")
        item_settings = file_menu.Append(wx.ID_ANY, "Настройки\tCtrl+P", "Открыть настройки программы")
        file_menu.AppendSeparator()
        item_exit = file_menu.Append(wx.ID_EXIT, "Выход\tAlt+F4", "Закрыть программу")
        
        self.Bind(wx.EVT_MENU, self.on_open_accounts, item_accounts)
        self.Bind(wx.EVT_MENU, self.on_open_settings, item_settings)
        self.Bind(wx.EVT_MENU, self.on_exit, item_exit)
        
        # 2. Menu: Player
        player_menu = wx.Menu()
        
        menu_seek = wx.Menu()
        item_seek_fwd = menu_seek.Append(wx.ID_ANY, "Вперед на 5 секунд\tCtrl+Right")
        item_seek_bwd = menu_seek.Append(wx.ID_ANY, "Назад на 5 секунд\tCtrl+Left")
        player_menu.AppendSubMenu(menu_seek, "Перемотка")
        
        menu_vol = wx.Menu()
        item_vol_up = menu_vol.Append(wx.ID_ANY, "Громче\tCtrl+Up")
        item_vol_dn = menu_vol.Append(wx.ID_ANY, "Тише\tCtrl+Down")
        player_menu.AppendSubMenu(menu_vol, "Громкость")
        
        player_menu.AppendSeparator()
        self.item_repeat = player_menu.AppendCheckItem(wx.ID_ANY, "Повтор трека\tCtrl+R")
        
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(5.0), item_seek_fwd)
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(-5.0), item_seek_bwd)
        self.Bind(wx.EVT_MENU, lambda e: self.change_volume(0.1), item_vol_up)
        self.Bind(wx.EVT_MENU, lambda e: self.change_volume(-0.1), item_vol_dn)
        self.Bind(wx.EVT_MENU, self.on_toggle_repeat, self.item_repeat)
        
        # 3. Menu: Help
        help_menu = wx.Menu()
        item_guide = help_menu.Append(wx.ID_ANY, "Руководство пользователя\tF1")
        item_license = help_menu.Append(wx.ID_ANY, "Открыть лицензионное соглашение\tCtrl+F1")
        help_menu.AppendSeparator()
        item_about = help_menu.Append(wx.ID_ANY, "О программе")
        self.Bind(wx.EVT_MENU, self.on_open_help, item_guide)
        self.Bind(wx.EVT_MENU, self.on_open_license, item_license)
        self.Bind(wx.EVT_MENU, self.on_about, item_about)

        menubar.Append(file_menu, "&Файл")
        menubar.Append(player_menu, "&Плеер")
        menubar.Append(help_menu, "&Справка")
        self.SetMenuBar(menubar)

    def on_toggle_repeat(self, event):
        self.repeat_enabled = self.item_repeat.IsChecked()
        status = "включен" if self.repeat_enabled else "выключен"
        self.safe_speak(f"Повтор трека {status}", "general")

    def change_volume(self, delta):
        new_vol = max(0.0, min(1.0, self.player.get_volume() + delta))
        self.player.set_volume(new_vol)
        self.config.set("volume", new_vol)
        self.safe_speak(f"Громкость {int(new_vol * 100)} процентов", "media")

    def on_open_help(self, event):
        help_path = get_resource_path(os.path.join("docs", "help.txt"))
        try:
            os.startfile(help_path)
            self.safe_speak("Справка открыта", "general")
        except Exception as e:
            self.show_error("Не удалось открыть справку. Файл help.txt отсутствует в папке docs.", e)

    def on_open_license(self, event):
        license_path = get_resource_path(os.path.join("docs", "license.txt"))
        try:
            os.startfile(license_path)
            self.safe_speak("Лицензионное соглашение открыто", "general")
        except Exception as e:
            self.show_error("Не удалось открыть лицензионное соглашение. Файл license.txt отсутствует в папке docs.", e)

    def on_about(self, event):
        wx.MessageBox(
            f"y-music-blind\n\nВерсия: {VERSION}\n\n"
            "Клиент для прослушивания и скачивания музыки с Яндекс.Музыки.\n\n"
            "© 2026",
            "О программе",
            wx.OK | wx.ICON_INFORMATION,
        )
        self.safe_speak(f"О программе. Версия {VERSION}", "general")

    def show_error(self, message, exception=None):
        play_ui_sound('error.wav')
        full_message = message
        if exception and self.config.get("detailed_errors"):
            full_message += f"\n\nТехническое заключение:\n{type(exception).__name__}: {str(exception)}"
        
        self.safe_speak("Произошла ошибка", "general")
        wx.MessageBox(full_message, "Ошибка", wx.OK | wx.ICON_ERROR)

    def on_open_settings(self, event):
        dlg = SettingsDialog(self, self.config)
        dlg.ShowModal()
        dlg.Destroy()
        
    def on_open_accounts(self, event):
        dlg = AccountManagerDialog(self, self.account_mgr, self.run_account_flow, self.switch_account)
        dlg.ShowModal()
        dlg.Destroy()

    def on_exit(self, event):
        self.Close()

    def check_auth(self):
        token = self.account_mgr.get_last_used_token()
        if token:
            success, status, msg = self.api.auth(token)
            if success:
                play_ui_sound('connect.wav')
                self.safe_speak("Авторизация успешна", "general")
                self.search_input.SetFocus()
                return
            else:
                if status == "NETWORK":
                    self.show_error("Нет подключения к интернету.")
                    return
                else:
                    self.show_error(f"Ошибка токена: {msg}")

        self.run_account_flow(existing_id=None)

    def switch_account(self, token):
        if token:
            success, status, msg = self.api.auth(token)
            if success:
                self.safe_speak("Аккаунт переключен", "general")
                self.reset_list_state()
            else:
                self.show_error(f"Не удалось авторизоваться в этом аккаунте: {msg}")

    def run_account_flow(self, existing_id=None):
        dlg_name = wx.TextEntryDialog(self, "Введите имя учетной записи:", "Создание профиля")
        if dlg_name.ShowModal() != wx.ID_OK:
            dlg_name.Destroy()
            if not self.account_mgr.accounts: self.Close()
            return
            
        acc_name = dlg_name.GetValue().strip() or "Без имени"
        dlg_name.Destroy()

        dlg = wx.SingleChoiceDialog(self, "Выберите способ авторизации:", "Авторизация", 
            ["Через код устройства (Встроенный браузер)", "Через веб-перехват (Классический)", "Ввести токен вручную"])
            
        if dlg.ShowModal() == wx.ID_OK:
            choice = dlg.GetSelection()
            dlg.Destroy()
            token = None
            
            if choice == 0:
                token = self.run_device_auth()
            elif choice == 1:
                token = self.run_browser_auth()
            elif choice == 2:
                token = self.run_token_input()
                
            if token:
                if existing_id:
                    self.account_mgr.update_account(existing_id, name=acc_name, token=token)
                    self.account_mgr.set_last_used(existing_id)
                else:
                    self.account_mgr.add_account(acc_name, token)
                    
                success, status, msg = self.api.auth(token)
                if success:
                    play_ui_sound('connect.wav')
                    self.safe_speak("Авторизация успешна", "general")
                    self.search_input.SetFocus()
                else:
                    self.show_error(f"Ошибка авторизации: {msg}")
        else:
            dlg.Destroy()
            if not self.account_mgr.accounts: self.Close()

    def run_token_input(self):
        dlg = wx.TextEntryDialog(self, "Введите ваш OAuth-токен:", "Ввод токена")
        token = None
        if dlg.ShowModal() == wx.ID_OK:
            token = dlg.GetValue().strip()
        dlg.Destroy()
        return token

    def run_device_auth(self):
        from yandex_music import Client as _YClient
        from yandex_music.exceptions import DeviceAuthError as _DeviceAuthError
        result_token = [None]
        auth_error = [None]
        dialog_closed = [threading.Event()]

        device_dlg = wx.Dialog(
            self, title="Авторизация устройства", size=(550, 400),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL,
        )
        vbox = wx.BoxSizer(wx.VERTICAL)
        self._device_dlg_closing = False

        def _on_close(event):
            if result_token[0] or self._device_dlg_closing:
                event.Skip()
                return
            dlg = wx.MessageDialog(
                device_dlg, "Вы действительно хотите прервать процесс авторизации?",
                "Подтверждение", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
            )
            ok = dlg.ShowModal() == wx.ID_YES
            dlg.Destroy()
            if ok:
                self._device_dlg_closing = True
                dialog_closed[0].set()
                event.Skip()
            else:
                event.Veto()
        device_dlg.Bind(wx.EVT_CLOSE, _on_close)

        status_label = wx.StaticText(device_dlg, label="Получение кода устройства...")
        vbox.Add(status_label, flag=wx.ALL | wx.EXPAND, border=10)
        cancel_btn = wx.Button(device_dlg, label="Отмена")
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: device_dlg.Close())
        vbox.Add(cancel_btn, flag=wx.ALL | wx.ALIGN_CENTER, border=10)
        device_dlg.SetSizer(vbox)

        def _device_thread():
            try:
                temp_client = _YClient()
                def _on_code(code):
                    def _update_ui():
                        vbox.Clear(True)
                        vbox.Add(wx.StaticText(device_dlg, label="Код устройства:"),
                                 flag=wx.ALL, border=5)
                        code_input = wx.TextCtrl(
                            device_dlg, value=code.user_code,
                            style=wx.TE_READONLY | wx.TE_CENTER,
                        )
                        code_input.SetFont(
                            wx.Font(18, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
                        )
                        vbox.Add(code_input, flag=wx.ALL | wx.EXPAND, border=5)
                        vbox.Add(wx.StaticText(device_dlg, label=code.verification_url),
                                 flag=wx.ALL, border=5)
                        if wx.TheClipboard.Open():
                            wx.TheClipboard.SetData(wx.TextDataObject(code.user_code))
                            wx.TheClipboard.Close()
                        vbox.Add(wx.StaticText(device_dlg, label="Код скопирован в буфер обмена."),
                                 flag=wx.ALL, border=5)
                        open_btn = wx.Button(device_dlg, label="Открыть страницу")
                        open_btn.Bind(wx.EVT_BUTTON, lambda e: self._open_embedded_browser(code.verification_url))
                        vbox.Add(open_btn, flag=wx.ALL | wx.ALIGN_CENTER, border=5)
                        cancel_btn2 = wx.Button(device_dlg, label="Отмена")
                        cancel_btn2.Bind(wx.EVT_BUTTON, lambda e: device_dlg.Close())
                        vbox.Add(cancel_btn2, flag=wx.ALL | wx.ALIGN_CENTER, border=5)
                        device_dlg.SetSizer(vbox)
                        device_dlg.Layout()
                        open_btn.SetFocus()
                    wx.CallAfter(_update_ui)

                oauth_token = temp_client.device_auth(
                    on_code=_on_code,
                    should_cancel=lambda: dialog_closed[0].is_set(),
                )
                result_token[0] = oauth_token.access_token
            except _DeviceAuthError as e:
                if not dialog_closed[0].is_set():
                    auth_error[0] = str(e)
            except Exception as e:
                if not dialog_closed[0].is_set():
                    auth_error[0] = str(e)
            finally:
                if not dialog_closed[0].is_set():
                    wx.CallAfter(lambda: device_dlg.EndModal(wx.ID_OK if result_token[0] else wx.ID_CANCEL))

        threading.Thread(target=_device_thread, daemon=True).start()
        device_dlg.ShowModal()
        device_dlg.Destroy()

        if result_token[0]:
            return result_token[0]
        if auth_error[0]:
            self.show_error(f"Ошибка авторизации устройства: {auth_error[0]}")
        return None

    def _open_embedded_browser(self, url):
        browser = SimpleBrowserDialog(self, url)
        browser.Show()

    def run_browser_auth(self):
        dlg = BrowserAuthDialog(self)
        token = None
        if dlg.ShowModal() == wx.ID_OK:
            token = dlg.token
        dlg.Destroy()
        return token

    def init_ui(self):
        self.panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        search_box = wx.BoxSizer(wx.HORIZONTAL)
        
        self.search_input = wx.TextCtrl(self.panel)
        search_box.Add(self.search_input, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.search_type = wx.ComboBox(self.panel, choices=["Треки", "Исполнители", "Жанры"], style=wx.CB_READONLY)
        self.search_type.SetSelection(0)
        search_box.Add(self.search_type, flag=wx.ALL, border=5)
        
        btn_search = wx.Button(self.panel, label="Поиск")
        btn_search.Bind(wx.EVT_BUTTON, self.on_search)
        search_box.Add(btn_search, flag=wx.ALL, border=5)
        
        vbox.Add(search_box, flag=wx.EXPAND)
        
        self.track_list = TrackListCtrl(self.panel)
        self.track_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
        self.track_list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context_menu)
        self.track_list.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
        
        vbox.Add(self.track_list, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.dl_gauge = wx.Gauge(self.panel, range=100, size=(-1, 15))
        self.dl_gauge.Hide()
        vbox.Add(self.dl_gauge, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.btn_load_more = wx.Button(self.panel, label="Загрузить ещё")
        self.btn_load_more.Bind(wx.EVT_BUTTON, self.on_load_more)
        self.btn_load_more.Hide()
        vbox.Add(self.btn_load_more, flag=wx.ALL | wx.ALIGN_CENTER, border=5)
        
        self.panel.SetSizer(vbox)

    def focus_item(self, index=0):
        if 0 <= index < len(self.track_list.data):
            self.track_list.Select(index)
            self.track_list.Focus(index)
            self.track_list.SetFocus()

    def on_context_menu(self, event):
        selection = -1
        if event and hasattr(event, 'GetIndex') and event.GetIndex() != -1:
            selection = event.GetIndex()
            self.track_list.Select(selection)
        else:
            selection = self.track_list.GetFirstSelected()
            if selection < 0 and self.track_list.GetItemCount() > 0:
                selection = 0
                self.track_list.Select(selection)

        if selection < 0 or selection >= len(self.track_list.data): 
            return
        
        item = self.track_list.data[selection]
        type_name = type(item).__name__
        is_album = type_name == 'Album' or (hasattr(item, 'track_count') and hasattr(item, 'title'))
        is_track = type_name == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title') and not is_album)

        menu = wx.Menu()
        if is_track:
            item_down = menu.Append(wx.ID_ANY, "Скачать трек\tCtrl+S")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.start_download(it), item_down)
            menu.AppendSeparator()
            item_like = menu.Append(wx.ID_ANY, "Поставить лайк\tCtrl+L")
            item_dislike = menu.Append(wx.ID_ANY, "Убрать лайк (дизлайк)\tShift+L")
            self.Bind(wx.EVT_MENU, lambda e, it_id=item.id: self.handle_like(it_id, 'track', True), item_like)
            self.Bind(wx.EVT_MENU, lambda e, it_id=item.id: self.handle_like(it_id, 'track', False), item_dislike)
            
        elif is_album:
            item_open = menu.Append(wx.ID_ANY, "Открыть альбом (треки)")
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.load_album_tracks(alb_id), item_open)
            menu.AppendSeparator()
            item_like = menu.Append(wx.ID_ANY, "Поставить лайк альбому\tCtrl+L")
            item_dislike = menu.Append(wx.ID_ANY, "Убрать лайк альбому\tShift+L")
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.handle_like(alb_id, 'album', True), item_like)
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.handle_like(alb_id, 'album', False), item_dislike)

        item_copy = menu.Append(wx.ID_ANY, "Копировать ссылку\tCtrl+C")
        self.Bind(wx.EVT_MENU, lambda e, it=item: self.copy_link(it), item_copy)

        item_sys = menu.Append(wx.ID_ANY, "Открыть в системном плеере\tCtrl+O")
        self.Bind(wx.EVT_MENU, lambda e, it=item: self.open_in_system_player(it), item_sys)

        pos = wx.DefaultPosition
        if event and hasattr(event, 'GetPosition') and event.GetPosition() != wx.DefaultPosition:
            pos = self.track_list.ScreenToClient(event.GetPosition())
        self.track_list.PopupMenu(menu, pos)
        menu.Destroy()

    def handle_like(self, entity_id, entity_type, is_like):
        def _task():
            try:
                if entity_type == 'track':
                    self.api.like_track(entity_id) if is_like else self.api.dislike_track(entity_id)
                elif entity_type == 'album':
                    self.api.like_album(entity_id) if is_like else self.api.dislike_album(entity_id)
                action = "добавлен" if is_like else "убран"
                self.safe_speak(f"Лайк {action}", "general")
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при изменении отметки", e)
        threading.Thread(target=_task, daemon=True).start()

    def open_in_system_player(self, track):
        """Создает временный плейлист M3U и передает его ОС, чтобы трек открылся в плеере."""
        def _task():
            try:
                url = self.api.get_track_direct_url(track)
                if not url:
                    wx.CallAfter(self.show_error, "Не удалось получить ссылку на трек.")
                    return
                
                import tempfile
                fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix="yandex_track_")
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write("#EXTM3U\n")
                    f.write(f"#EXTINF:-1,{track.title}\n")
                    f.write(f"{url}\n")
                
                os.startfile(temp_path)
                self.safe_speak("Трек открыт в системном плеере", "general")
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка открытия в плеере", e)
        threading.Thread(target=_task, daemon=True).start()

    def copy_link(self, item):
        url = ""
        type_name = type(item).__name__
        is_album = type_name == 'Album' or (hasattr(item, 'track_count') and hasattr(item, 'title'))
        is_track = type_name == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title') and not is_album)

        if is_album:
            url = f"https://music.yandex.ru/album/{item.id}"
        elif is_track:
            album_id = item.albums[0].id if getattr(item, 'albums', []) else ""
            url = f"https://music.yandex.ru/album/{album_id}/track/{item.id}"
        elif type_name == 'Genre' or (hasattr(item, 'title') and not hasattr(item, 'artists') and not is_album): 
            url = f"https://music.yandex.ru/genre/{item.id}"
        else:
            url = f"https://music.yandex.ru/artist/{item.id}"
        
        if url and wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(url))
            wx.TheClipboard.Close()
            self.safe_speak("Ссылка скопирована", "general")

    def update_track_status(self, index, status_text=""):
        if 0 <= index < len(self.track_list.data):
            if status_text in ("[Воспроизводится]", "[Приостановлено]", "[Воспроизведено]", ""):
                self.current_status = status_text
                
            self.track_list.playing_index = index
            self.track_list.status_text = status_text
            self.track_list.RefreshItem(index)

    def on_timer(self, event):
        if self.track_list.playing_index != -1 and self.is_track_active:
            state = self.player.get_state()
            if state == 0:
                self.is_track_active = False
                prev_index = self.track_list.playing_index
                
                if self.repeat_enabled:
                    self.play_track(prev_index, self.track_list.data[prev_index])
                else:
                    self.update_track_status(prev_index, "[Воспроизведено]")
                    next_index = prev_index + 1
                    if next_index < len(self.track_list.data):
                        next_item = self.track_list.data[next_index]
                        type_name = type(next_item).__name__
                        is_album = type_name == 'Album' or (hasattr(next_item, 'track_count') and hasattr(next_item, 'title'))
                        is_track = type_name == 'Track' or (hasattr(next_item, 'artists') and hasattr(next_item, 'title') and not is_album)
                        if is_track:
                            self.play_track(next_index, next_item)

    def reset_list_state(self):
        self.current_page = 0
        self.seen_ids.clear()
        self.track_list.data.clear()
        self.track_list.SetItemCount(0)
        self.track_list.playing_index = -1
        self.track_list.status_text = ""
        self.is_track_active = False
        self.current_status = ""

    def append_unique_items(self, items):
        added = 0
        for item in items:
            item_id = str(getattr(item, 'id', getattr(item, 'name', str(item))))
            if item_id not in self.seen_ids:
                self.seen_ids.add(item_id)
                self.track_list.data.append(item)
                added += 1
        return added

    def resolve_focus_index(self, append, prev_len):
        if append and self.config.get("remember_cursor", True):
            sel = self.track_list.GetFirstSelected()
            return sel if sel >= 0 else prev_len
        return prev_len if append else 0

    def on_search(self, event):
        query = self.search_input.GetValue().strip()
        if not query: return
            
        self.view_mode = 'search'
        self.reset_list_state()
        self.load_search_results()

    def on_load_more(self, event):
        self.current_page += 1
        if self.view_mode == 'search':
            self.load_search_results(append=True)
        elif self.view_mode == 'artist_tracks':
            self.load_artist_tracks(self.current_artist_id, append=True)
        elif self.view_mode == 'artist_albums':
            self.load_artist_albums(self.current_artist_id, append=True)
        elif self.view_mode == 'genre_tracks':
            self.load_genre_tracks(self.current_genre_id, append=True)

    def load_search_results(self, append=False):
        query = self.search_input.GetValue().strip()
        sel = self.search_type.GetSelection()
        stype = "track" if sel == 0 else "artist" if sel == 1 else "genre"
        
        try:
            results, has_next = self.api.search(query, search_type=stype, page=self.current_page)
            if results:
                prev_len = len(self.track_list.data)
                added = self.append_unique_items(results)
                if added > 0:
                    new_len = len(self.track_list.data)
                    self.track_list.SetItemCount(new_len)
                    self.track_list.RefreshItems(0, new_len - 1)
                    self.focus_item(self.resolve_focus_index(append, prev_len))
                
                self.safe_speak(f"Загружено: {added}. Всего: {len(self.track_list.data)}", "general")
            else:
                if self.current_page == 0:
                    self.safe_speak("Ничего не найдено", "general")

            if has_next: self.btn_load_more.Show()
            else: self.btn_load_more.Hide()
            self.panel.Layout()
        except Exception as e:
            self.show_error("Ошибка при поиске", e)

    def on_item_activated(self, event):
        selection = self.track_list.GetFirstSelected()
        if selection < 0 or selection >= len(self.track_list.data): return

        item = self.track_list.data[selection]
        type_name = type(item).__name__
        is_album = type_name == 'Album' or (hasattr(item, 'track_count') and hasattr(item, 'title'))
        is_track = type_name == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title') and not is_album)
        
        if is_album:
            self.load_album_tracks(item.id)
        elif is_track:
            self.play_track(selection, item)
        elif type_name == 'Genre' or (hasattr(item, 'title') and not hasattr(item, 'artists') and not is_album):
            self.load_genre_tracks(item.id) 
        else:
            dlg = wx.SingleChoiceDialog(self, "Что загрузить?", "Выбор", ["Популярные треки", "Альбомы"])
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                if sel == 0:
                    self.load_artist_tracks(item.id, append=False)
                else:
                    self.load_artist_albums(item.id, append=False)
            dlg.Destroy()

    def load_album_tracks(self, album_id):
        self.view_mode = 'album_tracks'
        self.reset_list_state()
        try:
            album = self.api.client.albums_with_tracks(album_id)
            tracks = []
            if hasattr(album, 'volumes') and album.volumes:
                for volume in album.volumes:
                    tracks.extend(volume)
            elif hasattr(album, 'tracks') and album.tracks:
                tracks = album.tracks
                
            if tracks:
                added = self.append_unique_items(tracks)
                if added > 0:
                    new_len = len(self.track_list.data)
                    self.track_list.SetItemCount(new_len)
                    self.track_list.RefreshItems(0, new_len - 1)
                    self.focus_item(0)
                self.safe_speak(f"Загружено треков альбома: {added}", "general")
            else:
                self.safe_speak("Треки в альбоме не найдены", "general")
            self.panel.Layout()
        except Exception as e:
            self.show_error("Не удалось загрузить треки альбома", e)

    # ── Загрузка треков жанра (фоновый поток) ────────────────────────────────

    def load_genre_tracks(self, genre_id, append=False):
        """
        Запускает загрузку треков жанра в отдельном потоке, чтобы UI
        не замирал пока идут сетевые запросы к ротору.

        При append=False сбрасывает список и показывает статус «Загружаю…».
        При append=True (кнопка «Загрузить ещё») просто добавляет следующую порцию.
        """
        # Защита от двойного нажатия — пока идёт загрузка, игнорируем повторные вызовы.
        if self._genre_loading:
            logger.debug("load_genre_tracks: загрузка уже выполняется, запрос проигнорирован.")
            return

        if not append:
            self.view_mode = 'genre_tracks'
            self.current_genre_id = genre_id
            self.reset_list_state()

        self._genre_loading = True

        # Показываем кнопку в состоянии «идёт загрузка» и обновляем статусбар.
        self.btn_load_more.SetLabel("Загружаю…")
        self.btn_load_more.Disable()
        self.btn_load_more.Show()
        self.panel.Layout()
        self.safe_speak("Загружаю треки жанра, подождите", "general")

        def _task():
            try:
                count = 5 if append else 20
                existing = self.seen_ids if append else None
                tracks, has_next = self.api.get_genre_tracks_batch(
                    genre_id, min_count=count, existing_seen_ids=existing,
                )
                wx.CallAfter(self._on_genre_tracks_loaded, tracks, has_next, append)
            except Exception as e:
                wx.CallAfter(self._on_genre_load_error, e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_genre_tracks_loaded(self, tracks, has_next, append):
        """Вызывается из фонового потока через wx.CallAfter."""
        self._genre_loading = False
        self._restore_load_more_button()

        if tracks:
            prev_len = len(self.track_list.data)
            added = self.append_unique_items(tracks)
            new_len = len(self.track_list.data)
            if added > 0:
                self.track_list.SetItemCount(new_len)
                self.track_list.RefreshItems(0, new_len - 1)
                self.focus_item(self.resolve_focus_index(append, prev_len))
            self.safe_speak(
                f"Загружено треков жанра: {added}. Всего: {new_len}",
                "general",
            )
        elif not append:
            self.safe_speak("Треки не найдены", "general")

        if has_next:
            self.btn_load_more.Show()
        else:
            self.btn_load_more.Hide()
        self.panel.Layout()

    def _on_genre_load_error(self, exception):
        """Обрабатывает ошибку фоновой загрузки жанровых треков."""
        self._genre_loading = False
        self._restore_load_more_button()
        self.btn_load_more.Hide()
        self.panel.Layout()
        self.show_error("Не удалось загрузить треки жанра.", exception)

    def _restore_load_more_button(self):
        """Возвращает кнопке «Загрузить ещё» исходный вид после загрузки."""
        self.btn_load_more.SetLabel("Загрузить ещё")
        self.btn_load_more.Enable()

    # ── Загрузка треков / альбомов исполнителя ────────────────────────────────

    def load_artist_tracks(self, artist_id, append=False):
        if not append:
            self.view_mode = 'artist_tracks'
            self.current_artist_id = artist_id
            self.reset_list_state()
            
        try:
            tracks, has_next = self.api.get_artist_tracks(artist_id, page=self.current_page)
            if tracks:
                prev_len = len(self.track_list.data)
                added = self.append_unique_items(tracks)
                if added > 0:
                    new_len = len(self.track_list.data)
                    self.track_list.SetItemCount(new_len)
                    self.track_list.RefreshItems(0, new_len - 1)
                    self.focus_item(self.resolve_focus_index(append, prev_len))
                
                self.safe_speak(f"Загружено треков исполнителя: {added}. Всего: {len(self.track_list.data)}", "general")
            
            if has_next: self.btn_load_more.Show()
            else: self.btn_load_more.Hide()
            self.panel.Layout()
        except Exception as e:
            self.show_error("Не удалось загрузить треки исполнителя.", e)
            
    def load_artist_albums(self, artist_id, append=False):
        if not append:
            self.view_mode = 'artist_albums'
            self.current_artist_id = artist_id
            self.reset_list_state()
            
        try:
            albums, has_next = self.api.get_artist_albums(artist_id, page=self.current_page)
            if albums:
                prev_len = len(self.track_list.data)
                added = self.append_unique_items(albums)
                if added > 0:
                    new_len = len(self.track_list.data)
                    self.track_list.SetItemCount(new_len)
                    self.track_list.RefreshItems(0, new_len - 1)
                    self.focus_item(self.resolve_focus_index(append, prev_len))
                
                self.safe_speak(f"Загружено альбомов: {added}. Всего: {len(self.track_list.data)}", "general")
            
            if has_next: self.btn_load_more.Show()
            else: self.btn_load_more.Hide()
            self.panel.Layout()
        except Exception as e:
            self.show_error("Не удалось загрузить альбомы исполнителя.", e)

    def play_track(self, index, track):
        try:
            url = self.api.get_track_direct_url(track)
            if not url:
                self.show_error("Не удалось получить ссылку на аудиопоток. Возможно, трек недоступен в вашем регионе.")
                return

            self.player.play_url(url)
            self.is_track_active = True
            
            if self.track_list.playing_index != -1:
                old_index = self.track_list.playing_index
                self.track_list.playing_index = -1
                self.track_list.RefreshItem(old_index)
                
            self.update_track_status(index, "[Воспроизводится]")
            self.focus_item(index)
        except Exception as e:
            self.show_error("Произошла системная ошибка при попытке воспроизведения.", e)

    def start_download(self, track):
        download_dir = self.config.get("download_dir", "")
        artists = ", ".join([a.name for a in (getattr(track, 'artists') or [])])
        safe_title = "".join(c for c in f"{artists} - {track.title}" if c.isalnum() or c in " -_").strip()
        save_path = os.path.join(download_dir, f"{safe_title}.mp3")
        
        self.safe_speak("Начато скачивание трека", "general")
        self.dl_gauge.SetValue(0)
        self.dl_gauge.Show()
        self.panel.Layout()

        def _dl():
            def _progress_callback(recvd, total):
                if total > 0:
                    percent = int((recvd / total) * 100)
                    percent = min(100, max(0, percent))
                    wx.CallAfter(self.dl_gauge.SetValue, percent)

            success, err = self.api.download_track(track, save_path, progress_callback=_progress_callback)
            
            def _finish():
                self.dl_gauge.Hide()
                self.panel.Layout()

                if success:
                    self.safe_speak("Загрузка завершена", "general")
                    if self.config.get("show_download_dialog", True):
                        dlg = wx.MessageDialog(
                            self, 
                            f"Трек успешно сохранён по пути:\n{save_path}", 
                            "Загрузка завершена", 
                            wx.OK | wx.CANCEL | wx.ICON_INFORMATION
                        )
                        dlg.SetOKCancelLabels("ОК", "Показать в папке")
                        if dlg.ShowModal() == wx.ID_CANCEL: 
                            subprocess.Popen(rf'explorer /select,"{save_path.replace("/", "\\")}"')
                        dlg.Destroy()
                    else:
                        play_ui_sound('complete_download.wav')
                else:
                    self.show_error("Ошибка при скачивании трека.", Exception(err))

            wx.CallAfter(_finish)
        threading.Thread(target=_dl, daemon=True).start()

    def on_char_hook(self, event):
        try:
            keycode = event.GetKeyCode()
            ctrl_down = event.ControlDown()
            shift_down = event.ShiftDown()
            alt_down = event.AltDown()

            # Прерывание речи при нажатии клавиши Ctrl
            if keycode == wx.WXK_CONTROL:
                self.safe_speak("", interrupt=True)
                event.Skip() 
                return

            if keycode == wx.WXK_MENU:
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel < 0 and self.track_list.GetItemCount() > 0:
                        self.focus_item(0)
                        sel = 0
                    if sel >= 0:
                        self.on_context_menu(None)
                return

            if keycode == wx.WXK_F1:
                if ctrl_down:
                    self.on_open_license(None)
                else:
                    self.on_open_help(None)
                return

            if ctrl_down and keycode == ord('M'):
                self.on_open_accounts(None)
                return

            if ctrl_down and keycode == ord('S'):
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel >= 0:
                        item = self.track_list.data[sel]
                        type_name = type(item).__name__
                        is_album = type_name == 'Album' or (hasattr(item, 'track_count') and hasattr(item, 'title'))
                        is_track = type_name == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title') and not is_album)
                        if is_track:
                            self.start_download(item)
                return

            if ctrl_down and keycode == ord('O'):
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel >= 0:
                        item = self.track_list.data[sel]
                        type_name = type(item).__name__
                        is_album = type_name == 'Album' or (hasattr(item, 'track_count') and hasattr(item, 'title'))
                        is_track = type_name == 'Track' or (hasattr(item, 'artists') and hasattr(item, 'title') and not is_album)
                        if is_track:
                            self.open_in_system_player(item)
                return

            if ctrl_down and keycode == ord('L'):
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel >= 0:
                        item = self.track_list.data[sel]
                        is_album = hasattr(item, 'track_count') or type(item).__name__ == 'Album'
                        self.handle_like(item.id, 'album' if is_album else 'track', True)
                return

            if shift_down and not ctrl_down and not alt_down and keycode == ord('L'):
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel >= 0:
                        item = self.track_list.data[sel]
                        is_album = hasattr(item, 'track_count') or type(item).__name__ == 'Album'
                        self.handle_like(item.id, 'album' if is_album else 'track', False)
                return

            if ctrl_down and keycode == ord('C'):
                focus = self.FindFocus()
                if focus == self.track_list:
                    sel = self.track_list.GetFirstSelected()
                    if sel >= 0:
                        self.copy_link(self.track_list.data[sel])
                return

            if ctrl_down and keycode == ord('P'):
                self.on_open_settings(None)
                return
                
            if ctrl_down and keycode == ord('R'):
                self.item_repeat.Check(not self.item_repeat.IsChecked())
                self.on_toggle_repeat(None)
                return

            if keycode == wx.WXK_RETURN:
                focus = self.FindFocus()
                if focus == self.track_list:
                    self.on_item_activated(None)
                    return
                elif focus == self.search_input:
                    self.on_search(None)
                    return

            if keycode == wx.WXK_SPACE:
                if self.FindFocus() == self.track_list and self.track_list.playing_index != -1:
                    self.player.toggle_pause()
                    state = self.player.get_state()
                    if state == 3: 
                        self.update_track_status(self.track_list.playing_index, "[Приостановлено]")
                        self.safe_speak("Пауза", "media")
                    elif state == 1: 
                        self.update_track_status(self.track_list.playing_index, "[Воспроизводится]")
                        self.safe_speak("Воспроизведение", "media")
                    return

            if ctrl_down:
                if keycode == wx.WXK_UP:
                    self.change_volume(0.1)
                    return
                elif keycode == wx.WXK_DOWN:
                    self.change_volume(-0.1)
                    return
                elif keycode == wx.WXK_LEFT:
                    self.player.seek(-5.0)
                    self.safe_speak("Перемотка назад 5 секунд", "media")
                    return
                elif keycode == wx.WXK_RIGHT:
                    self.player.seek(5.0)
                    self.safe_speak("Перемотка вперед 5 секунд", "media")
                    return

            if shift_down and not ctrl_down and not alt_down:
                if keycode == wx.WXK_LEFT:
                    self.player.seek(-10.0)
                    self.safe_speak("Перемотка назад 10 секунд", "media")
                    return
                elif keycode == wx.WXK_RIGHT:
                    self.player.seek(10.0)
                    self.safe_speak("Перемотка вперед 10 секунд", "media")
                    return

        except Exception as ex:
            logger.error(f"Ошибка в хуке клавиатуры: {ex}")

        event.Skip()

if __name__ == '__main__':
    app = wx.App(False)
    frame = MainFrame()
    frame.Show()
    app.MainLoop()
