# Year: 2026
import wx
import wx.adv
import wx.html2
import os
import threading
import subprocess
import logging
import speech
import keyboard
from utils import get_resource_path, migrate_legacy_data, cleanup_temp_updater, classify_item
from logger import setup_logger, log_system_info, log_exception
from yandex_api import YandexMusicManager
from bass_player import BassPlayer
from system_player import SystemPlayerLauncher
from config_manager import ConfigManager
from account_manager import AccountManager, AccountManagerDialog
from oauth_webview import BrowserAuthDialog, _IsolatedCookieEnv, _clear_webview_session
from exceptions import AuthError, NetworkError
from version import VERSION
from app_updater import UpdateDialog, UpdateCore, get_main_exe_name
from tray import AppTaskBarIcon

logger = logging.getLogger(__name__)

def play_ui_sound(filename):
    """
    воспроизводит звукавые событие интерфейса.
    Черес bass.dll"""
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
    def __init__(self, parent, url, title="Авторизация через браузер", clear_session=True):
        super().__init__(
            parent, title=title, size=(800, 600),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.TAB_TRAVERSAL,
        )
        self._cookie_env = _IsolatedCookieEnv()
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.browser = wx.html2.WebView.New(self)
        if clear_session:
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
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_F4 and event.AltDown():
            self.Close()
            return
        if keycode == wx.WXK_ESCAPE:
            self.Close()
            return
        if keycode == wx.WXK_F5:
            try:
                self.browser.Reload()
            except Exception:
                pass
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
        is_playlist = type_name == 'Playlist' or (hasattr(obj, 'track_count') and hasattr(obj, 'title') and hasattr(obj, 'uid'))

        if is_playlist:
            count_str = f" [Треков: {obj.track_count}]" if getattr(obj, 'track_count', None) else ""
            owner = getattr(obj, 'owner', None)
            owner_name = getattr(owner, 'name', None) or getattr(owner, 'login', None) if owner else None
            base_str = f"Плейлист: {obj.title}{count_str}"
            if owner_name:
                base_str += f" — {owner_name}"
        elif is_album:
            year_str = f" ({obj.year})" if getattr(obj, 'year', None) else ""
            count_str = f" [Треков: {obj.track_count}]" if getattr(obj, 'track_count', None) else ""
            base_str = f"Альбом: {obj.title}{year_str}{count_str}"
        elif is_track:
            artists = ", ".join([a.name for a in (obj.artists or [])])
            base_str = f"{artists} - {obj.title}"
            if getattr(self, 'show_album', False):
                try:
                    albums = getattr(obj, 'albums', None)
                    if albums and getattr(albums[0], 'title', None):
                        base_str += f" — {albums[0].title}"
                except Exception:
                    pass
        elif is_artist:
            base_str = getattr(obj, 'name', str(obj))
        else:
            base_str = getattr(obj, 'title', getattr(obj, 'name', str(obj)))
            
        return prefix + base_str




class SettingsDialog(wx.Dialog):
    def __init__(self, parent, config):
        super().__init__(parent, title="Настройки", size=(450, 500))
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

        self.cb_check_updates_startup = wx.CheckBox(panel_general, label="Проверять наличие обновлений при старте")
        self.cb_check_updates_startup.SetValue(self.config.get("check_updates_on_startup", True))
        vbox_gen.Add(self.cb_check_updates_startup, flag=wx.ALL, border=5)

        self.cb_show_track_album = wx.CheckBox(panel_general, label="Отображать альбом в списке треков")
        self.cb_show_track_album.SetValue(self.config.get("show_track_album", True))
        vbox_gen.Add(self.cb_show_track_album, flag=wx.ALL, border=5)

        self.cb_minimize_to_tray = wx.CheckBox(panel_general, label="Сворачивать программу в системный трей")
        self.cb_minimize_to_tray.SetValue(self.config.get("minimize_to_tray", False))
        vbox_gen.Add(self.cb_minimize_to_tray, flag=wx.ALL, border=5)

        self.cb_tray_show_track = wx.CheckBox(panel_general, label="Показывать название трека в значке трея")
        self.cb_tray_show_track.SetValue(self.config.get("tray_show_track_name", False))
        vbox_gen.Add(self.cb_tray_show_track, flag=wx.ALL, border=5)
        
        self.cb_enable_logging = wx.CheckBox(panel_general, label="Включить логирование")
        self.cb_enable_logging.SetValue(self.config.get("enable_logging", True))
        vbox_gen.Add(self.cb_enable_logging, flag=wx.ALL, border=5)
        
        vbox_gen.Add(wx.StaticText(panel_general, label="Уровень логирования:"), flag=wx.LEFT | wx.TOP, border=5)
        self.cb_log_level = wx.ComboBox(panel_general, choices=["INFO", "DEBUG", "WARNING", "ERROR"], style=wx.CB_READONLY)
        self.cb_log_level.SetValue(self.config.get("log_level", "INFO"))
        vbox_gen.Add(self.cb_log_level, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.cb_clear_logs = wx.CheckBox(panel_general, label="Очищать логи при запуске программы")
        self.cb_clear_logs.SetValue(self.config.get("clear_logs_on_startup", True))
        vbox_gen.Add(self.cb_clear_logs, flag=wx.ALL, border=5)

        self.cb_enable_logging.Bind(wx.EVT_CHECKBOX, self.on_logging_toggle)
        self.on_logging_toggle(None)
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

        panel_view = wx.Panel(notebook)
        vbox_view = wx.BoxSizer(wx.VERTICAL)

        self.cb_dark_theme = wx.CheckBox(panel_view, label="Тёмная тема")
        self.cb_dark_theme.SetValue(self.config.get("dark_theme", False))
        vbox_view.Add(self.cb_dark_theme, flag=wx.ALL, border=5)

        vbox_view.Add(wx.StaticText(panel_view, label="Размер шрифта (пункты):"), flag=wx.LEFT | wx.TOP, border=5)
        self.spin_font_size = wx.SpinCtrl(panel_view, min=8, max=24, initial=self.config.get("font_size", 10))
        vbox_view.Add(self.spin_font_size, flag=wx.ALL | wx.EXPAND, border=5)

        vbox_view.Add(wx.StaticText(panel_view, label="Формат времени:"), flag=wx.LEFT | wx.TOP, border=5)
        self.cb_time_format = wx.ComboBox(panel_view, choices=["short", "full"], style=wx.CB_READONLY)
        self.cb_time_format.SetValue(self.config.get("time_format", "short"))
        vbox_view.Add(self.cb_time_format, flag=wx.ALL | wx.EXPAND, border=5)

        panel_view.SetSizer(vbox_view)

        notebook.AddPage(panel_general, "Общие")
        notebook.AddPage(panel_speech, "Речь")
        notebook.AddPage(panel_view, "Вид")
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

    def on_logging_toggle(self, event):
        is_enabled = self.cb_enable_logging.GetValue()
        self.cb_log_level.Enable(is_enabled)
        self.cb_clear_logs.Enable(is_enabled)

    def on_save(self, event):
        self.config.set("download_dir", self.dir_picker.GetPath())
        self.config.set("detailed_errors", self.cb_detailed_errors.GetValue())
        self.config.set("show_download_dialog", self.cb_show_dl_dialog.GetValue())
        self.config.set("remember_cursor", self.cb_rem_cursor.GetValue())
        self.config.set("check_updates_on_startup", self.cb_check_updates_startup.GetValue())
        self.config.set("show_track_album", self.cb_show_track_album.GetValue())
        self.config.set("minimize_to_tray", self.cb_minimize_to_tray.GetValue())
        self.config.set("tray_show_track_name", self.cb_tray_show_track.GetValue())
        self.config.set("enable_speech", self.cb_enable_speech.GetValue())
        self.config.set("enable_sapi5", self.cb_enable_sapi5.GetValue())
        self.config.set("speech_media_state", self.cb_speech_media.GetValue())
        self.config.set("speech_general", self.cb_speech_general.GetValue())
        self.config.set("log_level", self.cb_log_level.GetValue())
        self.config.set("enable_logging", self.cb_enable_logging.GetValue())
        self.config.set("clear_logs_on_startup", self.cb_clear_logs.GetValue())
        self.config.set("dark_theme", self.cb_dark_theme.GetValue())
        self.config.set("font_size", self.spin_font_size.GetValue())
        self.config.set("time_format", self.cb_time_format.GetValue())
        self.EndModal(wx.ID_OK)


class ItemInfoDialog(wx.Dialog):
    """Диалог с подробной информацией о выбранном элементе списка.

    Открывается по клавише Пробел при фокусе на списке. Содержимое
    доступно только для чтения; закрывается кнопкой «Закрыть» или Escape.
    """
    def __init__(self, parent, text):
        super().__init__(parent, title="Информация", size=(600, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.text.SetValue(text)
        vbox.Add(self.text, proportion=1, flag=wx.ALL | wx.EXPAND, border=8)
        btn_close = wx.Button(self, label="Закрыть", id=wx.ID_CLOSE)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        hbox = wx.BoxSizer(wx.HORIZONTAL)
        hbox.Add(btn_close, flag=wx.ALL, border=8)
        vbox.Add(hbox, flag=wx.EXPAND)
        self.SetSizer(vbox)
        self.Centre()
        self.text.SetFocus()

    def _on_key(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title="y-music-blind", size=(700, 650))
        
        migrate_legacy_data()
        cleanup_temp_updater()
        self.config = ConfigManager()
        setup_logger(self.config)
        log_system_info()
        logger.info("Программа запущена.")

        if self.config.get("check_updates_on_startup", False):
            threading.Thread(target=self._check_updates_startup_task, daemon=True).start()
        
        # Строка состояния для визуального отображения событий
        self.statusbar = self.CreateStatusBar(1)
        self.statusbar.SetStatusText("Готово к работе")
        
        self.account_mgr = AccountManager()
        self.api = YandexMusicManager()
        self.player = BassPlayer()
        self.system_player = SystemPlayerLauncher(self.config, frame=self)
        self.player.set_volume(self.config.get("volume", 0.5))
        
        self.current_page = 0
        self.view_mode = 'search'
        self.current_artist_id = None
        
        self.is_track_active = False 
        self.current_status = ""
        self.repeat_enabled = False 
        self.seen_ids = set()
        self._nav_stack = []
        
        # Очередь воспроизведения: список индексов элементов списка
        self.queue_indices = []
        self.queue_position = -1
        # Перемешивание: при включении видимый список перестраивается в случайном порядке
        self.shuffle_enabled = False
        self._shuffle_original_order = None
        self._shuffled_data = None

        # Системный список воспроизведения. Он не зависит от того, какой список
        # сейчас показан в интерфейсе: автовоспроизведение продолжает работать
        # даже после навигации (Escape, поиск, переход к другому исполнителю).
        self.current_playlist = []
        self.current_playlist_index = 0
        self._pending_start = False
        self._play_epoch = 0
        
        self.init_menu()
        self.init_ui()
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
        self._apply_appearance()

        self.tray_icon = AppTaskBarIcon(self)
        self._last_focused_window = None
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ICONIZE, self.on_iconize)
        
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(500) 
        
        wx.CallAfter(self.check_auth)

    def _check_updates_startup_task(self):
        try:
            core = UpdateCore(VERSION)
            has_update, latest_tag, notes, url, filename = core.check_for_updates()
            if has_update and url:
                wx.CallAfter(self._show_startup_update_dialog, VERSION)
        except Exception:
            logger.exception("Ошибка проверки обновлений при старте")

    def _show_startup_update_dialog(self, version_str):
        dlg = UpdateDialog(self, current_version=version_str,
                           main_exe_name=get_main_exe_name())
        dlg.ShowModal()
        dlg.Destroy()

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
            
        speech_cfg = dict(self.config.config)
        speech_cfg["_category"] = category
        speech.speak(text, interrupt, speech_cfg)

    def _apply_appearance(self):
        dark = self.config.get("dark_theme", False)
        font_size = self.config.get("font_size", 10)
        if dark:
            bg = wx.Colour(30, 30, 30)
            fg = wx.Colour(220, 220, 220)
            self.SetBackgroundColour(bg)
            self.SetForegroundColour(fg)
            self.panel.SetBackgroundColour(bg)
            self.panel.SetForegroundColour(fg)
            self.track_list.SetBackgroundColour(wx.Colour(25, 25, 25))
            self.track_list.SetForegroundColour(fg)
            self.time_label.SetForegroundColour(fg)
            for child in self.panel.GetChildren():
                if isinstance(child, (wx.Button, wx.StaticText, wx.ComboBox)):
                    child.SetBackgroundColour(bg)
                    child.SetForegroundColour(fg)
        else:
            bg = wx.NullColour
            fg = wx.NullColour
            self.SetBackgroundColour(wx.NullColour)
            self.SetForegroundColour(wx.NullColour)
            self.panel.SetBackgroundColour(wx.NullColour)
            self.panel.SetForegroundColour(wx.NullColour)
            self.track_list.SetBackgroundColour(wx.NullColour)
            self.track_list.SetForegroundColour(wx.NullColour)
            self.time_label.SetForegroundColour(wx.NullColour)
        font = self.track_list.GetFont()
        font.SetPointSize(font_size)
        self.track_list.SetFont(font)
        self.time_label.SetFont(font)
        self.Refresh()

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
        item_seek_fwd = menu_seek.Append(wx.ID_ANY, "Вперед на 5 секунд\tCtrl+F8")
        item_seek_fwd_10 = menu_seek.Append(wx.ID_ANY, "Вперед на 10 секунд\tShift+F8")
        menu_seek.AppendSeparator()
        item_seek_bwd = menu_seek.Append(wx.ID_ANY, "Назад на 5 секунд\tCtrl+F7")
        item_seek_bwd_10 = menu_seek.Append(wx.ID_ANY, "Назад на 10 секунд\tShift+F7")
        player_menu.AppendSubMenu(menu_seek, "Перемотка")

        menu_vol = wx.Menu()
        item_vol_up = menu_vol.Append(wx.ID_ANY, "Громче\tF6")
        item_vol_dn = menu_vol.Append(wx.ID_ANY, "Тише\tF5")
        player_menu.AppendSubMenu(menu_vol, "Громкость")

        menu_devices = wx.Menu()
        devices = self.player.get_devices()
        for dev_idx, dev_name in devices:
            item_dev = menu_devices.Append(wx.ID_ANY, dev_name)
            self.Bind(wx.EVT_MENU, lambda e, idx=dev_idx: self.switch_audio_device(idx), item_dev)
        player_menu.AppendSubMenu(menu_devices, "Устройство вывода")
        self.menu_devices = menu_devices

        menu_eq = wx.Menu()
        self.item_eq_enable = menu_eq.AppendCheckItem(wx.ID_ANY, "Включить эквалайзер")
        menu_eq.AppendSeparator()
        self.eq_presets = ["Поп", "Рок", "Техно", "Софт-рок", "Классика", "Электронная", "Клубная вечеринка"]
        self.eq_preset_items = []
        for preset in self.eq_presets:
            item_p = menu_eq.AppendRadioItem(wx.ID_ANY, preset)
            self.eq_preset_items.append(item_p)
            self.Bind(wx.EVT_MENU, lambda e, p=preset: self.on_eq_preset_select(p), item_p)
            item_p.Enable(False)
        self.Bind(wx.EVT_MENU, self.on_toggle_eq, self.item_eq_enable)
        player_menu.AppendSubMenu(menu_eq, "Эквалайзер")
        self.menu_eq = menu_eq

        player_menu.AppendSeparator()
        item_pause = player_menu.Append(wx.ID_ANY, "Пауза и воспроизведение\tF7")
        item_stop = player_menu.Append(wx.ID_ANY, "Остановить\tF8")
        self.Bind(wx.EVT_MENU, lambda e: self.toggle_pause_speak(), item_pause)
        self.Bind(wx.EVT_MENU, lambda e: self.stop_playback(), item_stop)
        self.item_repeat = player_menu.AppendCheckItem(wx.ID_ANY, "Повтор трека\tCtrl+R")

        menu_speed = wx.Menu()
        item_speed_slower = menu_speed.Append(wx.ID_ANY, "Замедлить\tShift+F5")
        item_speed_faster = menu_speed.Append(wx.ID_ANY, "Ускорить\tShift+F6")
        item_speed_reset = menu_speed.Append(wx.ID_ANY, "Сбросить скорость\tShift+R")
        player_menu.AppendSubMenu(menu_speed, "Скорость воспроизведения")

        self.Bind(wx.EVT_MENU, self.on_toggle_repeat, self.item_repeat)
        self.Bind(wx.EVT_MENU, lambda e: self.change_speed(-0.05), item_speed_slower)
        self.Bind(wx.EVT_MENU, lambda e: self.change_speed(0.05), item_speed_faster)
        self.Bind(wx.EVT_MENU, lambda e: self.reset_speed(), item_speed_reset)
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(5.0), item_seek_fwd)
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(10.0), item_seek_fwd_10)
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(-5.0), item_seek_bwd)
        self.Bind(wx.EVT_MENU, lambda e: self.player.seek(-10.0), item_seek_bwd_10)
        self.Bind(wx.EVT_MENU, lambda e: self.change_volume(0.1), item_vol_up)
        self.Bind(wx.EVT_MENU, lambda e: self.change_volume(-0.1), item_vol_dn)

        # 3. Menu: Service
        service_menu = wx.Menu()
        menu_playlists = wx.Menu()
        item_playlists = menu_playlists.Append(wx.ID_ANY, "Мои плейлисты", "Просмотр и управление плейлистами")
        service_menu.AppendSubMenu(menu_playlists, "Плейлисты")
        self.Bind(wx.EVT_MENU, self.on_playlists_manager, item_playlists)

        # 4. Menu: Help
        help_menu = wx.Menu()
        item_guide = help_menu.Append(wx.ID_ANY, "Руководство пользователя\tF1")
        item_license = help_menu.Append(wx.ID_ANY, "Открыть лицензионное соглашение\tCtrl+F1")
        item_keyboard = help_menu.Append(wx.ID_ANY, "Горячие клавиши\tAlt+F1")
        help_menu.AppendSeparator()
        item_update = help_menu.Append(wx.ID_ANY, "Проверить наличие обновлений")
        help_menu.AppendSeparator()
        item_changelog = help_menu.Append(wx.ID_ANY, "Журнал изменений")
        help_menu.AppendSeparator()
        item_about = help_menu.Append(wx.ID_ANY, "О программе")
        self.Bind(wx.EVT_MENU, self.on_open_help, item_guide)
        self.Bind(wx.EVT_MENU, self.on_open_license, item_license)
        self.Bind(wx.EVT_MENU, self.on_open_keyboard, item_keyboard)
        self.Bind(wx.EVT_MENU, self.on_check_updates, item_update)
        self.Bind(wx.EVT_MENU, self.on_open_changelog, item_changelog)
        self.Bind(wx.EVT_MENU, self.on_about, item_about)

        menubar.Append(file_menu, "&Файл")
        menubar.Append(player_menu, "&Плеер")
        menubar.Append(service_menu, "&Сервис")
        menubar.Append(help_menu, "&Справка")
        self.SetMenuBar(menubar)

    def on_toggle_repeat(self, event):
        self.repeat_enabled = self.item_repeat.IsChecked()
        status = "включен" if self.repeat_enabled else "выключен"
        self.safe_speak(f"Повтор трека {status}", "general")

    def toggle_shuffle(self):
        """Включает или выключает режим перемешивания (из меню и клавиатуры).

        При включении видимый список перестраивается в случайном порядке
        (текущий трек или выделенный элемент остаётся первым), а очередь
        воспроизведения следует за новым порядком списка.
        """
        self.shuffle_enabled = not self.shuffle_enabled
        anchor = self._current_anchor()
        if self.shuffle_enabled:
            self._shuffle_original_order = list(self.track_list.data)
            self._apply_order(self._build_shuffled_order(anchor), anchor)
            self._shuffled_data = self.track_list.data
            status = "включено"
        else:
            original = getattr(self, '_shuffle_original_order', None)
            shuffled = getattr(self, '_shuffled_data', None)
            if original and self.track_list.data is shuffled:
                self._apply_order(original, anchor)
            self._shuffle_original_order = None
            self._shuffled_data = None
            status = "выключено"
        self.safe_speak(f"Перемешивание {status}", "general")

    def _index_of(self, items, obj):
        """Индекс элемента по идентичности объекта (не по равенству)."""
        for i, it in enumerate(items):
            if it is obj:
                return i
        return -1

    def _current_anchor(self):
        """Возвращает текущий трек/выделенный элемент, чтобы сохранить его место."""
        if self.current_playlist and 0 <= self.current_playlist_index < len(self.current_playlist):
            playing = self.current_playlist[self.current_playlist_index]
            if self._index_of(self.track_list.data, playing) >= 0:
                return playing
        sel = self.track_list.GetFirstSelected()
        if 0 <= sel < len(self.track_list.data):
            return self.track_list.data[sel]
        return None

    def _build_shuffled_order(self, anchor):
        """Строит случайный порядок видимого списка с якорем первым."""
        import random
        items = list(self.track_list.data)
        if anchor is not None:
            i = self._index_of(items, anchor)
            if i >= 0:
                items.pop(i)
        random.shuffle(items)
        if anchor is not None:
            items.insert(0, anchor)
        return items

    def _apply_order(self, new_order, focus_obj=None):
        """Применяет новый порядок к видимому списку и очереди воспроизведения."""
        if not new_order:
            return
        self.track_list.data = list(new_order)
        self.track_list.SetItemCount(len(new_order))
        if new_order:
            self.track_list.RefreshItems(0, len(new_order) - 1)
        focus_idx = self._index_of(new_order, focus_obj) if focus_obj is not None else -1
        self.focus_item(focus_idx if focus_idx >= 0 else 0)

        if self.current_playlist and 0 <= self.current_playlist_index < len(self.current_playlist):
            playing = self.current_playlist[self.current_playlist_index]
            if self._index_of(new_order, playing) >= 0:
                self.current_playlist = list(new_order)
                self.current_playlist_index = self._index_of(new_order, playing)
                self.queue_indices = [i for i, it in enumerate(new_order) if classify_item(it) == 'track']
                if self.current_playlist_index in self.queue_indices:
                    self.queue_position = self.queue_indices.index(self.current_playlist_index)

    def change_speed(self, delta):
        """Изменяет скорость воспроизведения на delta (множитель)."""
        new_speed = round(self.player.change_speed(delta), 2)
        self.safe_speak(f"Скорость воспроизведения {int(new_speed * 100)} процентов", "media")

    def reset_speed(self):
        """Сбрасывает скорость воспроизведения к нормальной (1.0)."""
        self.player.reset_speed()
        self.safe_speak("Скорость воспроизведения сброшена", "media")

    def play_previous(self):
        """Переходит к предыдущему треку в очереди воспроизведения."""
        if self.queue_position > 0:
            self.queue_position -= 1
            self._play_from_queue()
        else:
            self.player.seek(-5.0)
            self.safe_speak("Это первый трек очереди", "media")

    def play_next(self, auto=False):
        """Переходит к следующему треку в очереди воспроизведения.

        auto=True означает автоматический переход после окончания трека;
        в этом случае пустую очередь просто не озвучиваем.
        """
        if self.queue_position + 1 < len(self.queue_indices):
            self.queue_position += 1
            self._play_from_queue()
        elif not auto:
            self.player.seek(5.0)
            self.safe_speak("Это последний трек очереди", "media")

    def speak_queue_info(self):
        """Озвучивает номер текущего трека и размер очереди."""
        total = len(self.queue_indices)
        current = self.queue_position + 1 if self.queue_position >= 0 else 0
        self.safe_speak(f"Трек {current} из {total}", "general")

    def speak_download_progress(self):
        """Shift+S: озвучивает текущий прогресс загрузки или открытия в системном плеере."""
        val = getattr(self, '_last_download_percent', 0)
        if hasattr(self, '_active_download_name') and self._active_download_name:
            self.safe_speak(f"Загрузка «{self._active_download_name}»: {val} процентов", "general")
        else:
            self.safe_speak("Активных загрузок нет", "general")

    def speak_elapsed_time(self):
        """Shift+T: озвучивает сколько проиграно."""
        pos = self.player.get_position()
        dur = self.player.get_duration()
        if dur <= 0:
            self.safe_speak("Нет активного трека", "general")
            return
        self.safe_speak(f"Проиграно: {self._format_time(pos)} из {self._format_time(dur)}", "general")

    def speak_remaining_time(self):
        """Ctrl+Shift+T: озвучивает сколько осталось."""
        pos = self.player.get_position()
        dur = self.player.get_duration()
        if dur <= 0:
            self.safe_speak("Нет активного трека", "general")
            return
        remaining = max(0.0, dur - pos)
        self.safe_speak(f"Осталось: {self._format_time(remaining)}", "general")

    def _play_from_queue(self):
        """Воспроизводит трек по индексу текущей очереди.

        Перемешивание заранее перестраивает видимый список, поэтому очередь
        всегда следует за порядком списка.
        """
        if self.queue_position < 0 or self.queue_position >= len(self.queue_indices):
            self.safe_speak("В очереди больше нет треков", "general")
            return
        idx = self.queue_indices[self.queue_position]
        source = self.current_playlist if self.current_playlist else self.track_list.data
        if 0 <= idx < len(source):
            self.play_track_list(source, idx, source[idx])
            self.focus_item(idx)

    def on_playlists_manager(self, event):
        """Открывает диалог управления плейлистами пользователя."""
        from playlist_dialog import PlaylistsDialog
        dlg = PlaylistsDialog(self, self.api)
        dlg.ShowModal()
        dlg.Destroy()

    def on_create_playlist(self, event):
        """Создаёт новый плейлист: запрашивает название и создаёт его."""
        dlg = wx.TextEntryDialog(self, "Введите название нового плейлиста:", "Создание плейлиста")
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        title = dlg.GetValue().strip()
        dlg.Destroy()
        if not title:
            return

        def _task():
            ok = bool(self.api.create_playlist(title))
            if ok:
                wx.CallAfter(self.safe_speak, f"Плейлист «{title}» создан", "general")
            else:
                wx.CallAfter(self.safe_speak, "Не удалось создать плейлист", "general")
        threading.Thread(target=_task, daemon=True).start()

    def change_volume(self, delta):
        new_vol = max(0.0, min(1.0, self.player.get_volume() + delta))
        self.player.set_volume(new_vol)
        self.config.set("volume", new_vol)
        self.safe_speak(f"Громкость {int(new_vol * 100)} процентов", "media")

    def toggle_pause_speak(self):
        """Переключает паузу и озвучивает новое состояние плеера."""
        self.player.toggle_pause()
        state = self.player.get_state()
        if state == 3:
            if self.track_list.playing_index != -1:
                self.update_track_status(self.track_list.playing_index, "[Приостановлено]")
            self.safe_speak("Пауза", "media")
        elif state == 1:
            if self.track_list.playing_index != -1:
                self.update_track_status(self.track_list.playing_index, "[Воспроизводится]")
            self.safe_speak("Воспроизведение", "media")

    def stop_playback(self):
        """Полностью останавливает воспроизведение."""
        self.is_track_active = False
        self.player.stop()
        if self.track_list.playing_index != -1:
            self.update_track_status(self.track_list.playing_index, "")
        self.safe_speak("Остановлено", "media")

    def switch_audio_device(self, device_index):
        try:
            self.player.set_device(device_index)
            self.safe_speak("Устройство вывода изменено", "media")
        except Exception as e:
            self.show_error("Не удалось переключить устройство вывода", e)

    def _popup_submenu(self, menu):
        """Показывает подменю рядом со списком (для Ctrl+U и Ctrl+D)."""
        try:
            rect = self.track_list.GetScreenRect()
            point = wx.Point(rect.x + 20, rect.y + 20)
            self.PopupMenu(menu, self.ScreenToClient(point))
        except Exception:
            logger.exception("Не удалось открыть подменю.")

    def show_eq_menu(self):
        """Открывает подменю эквалайзера (Ctrl+U)."""
        self._popup_submenu(self.menu_eq)

    def show_devices_menu(self):
        """Открывает подменю устройств вывода (Ctrl+D)."""
        self._popup_submenu(self.menu_devices)

    def on_toggle_eq(self, event):
        enabled = self.item_eq_enable.IsChecked()
        for item in self.eq_preset_items:
            item.Enable(enabled)
        status = "включен" if enabled else "выключен"
        self.safe_speak(f"Эквалайзер {status}", "general")
        if enabled:
            if self.eq_preset_items:
                self.eq_preset_items[0].Check(True)
            self.player.set_equalizer_enabled(True)
            self.player.set_equalizer_preset(self.eq_presets[0])
        else:
            self.player.set_equalizer_enabled(False)

    def on_eq_preset_select(self, preset):
        self.player.set_equalizer_preset(preset)
        self.safe_speak(f"Пресет эквалайзера: {preset}", "general")

    def on_open_help(self, event):
        help_path = get_resource_path(os.path.join("docs", "help.txt"))
        try:
            os.startfile(help_path)
            self.safe_speak("Справка открыта", "general")
        except Exception as e:
            self.show_error("Не удалось открыть справку. Файл help.txt отсутствует в папке docs.", e)

    def on_open_keyboard(self, event):
        kb_path = get_resource_path(os.path.join("docs", "keyboard.txt"))
        try:
            os.startfile(kb_path)
            self.safe_speak("Список горячих клавиш открыт", "general")
        except Exception as e:
            self.show_error("Не удалось открыть список горячих клавиш. Файл keyboard.txt отсутствует в папке docs.", e)

    def on_open_license(self, event):
        license_path = get_resource_path(os.path.join("docs", "license.txt"))
        try:
            os.startfile(license_path)
            self.safe_speak("Лицензионное соглашение открыто", "general")
        except Exception as e:
            self.show_error("Не удалось открыть лицензионное соглашение. Файл license.txt отсутствует в папке docs.", e)

    def on_open_changelog(self, event):
        changelog_path = get_resource_path(os.path.join("docs", "docs_version.txt"))
        try:
            os.startfile(changelog_path)
            self.safe_speak("Журнал изменений открыт", "general")
        except Exception as e:
            self.show_error("Не удалось открыть журнал изменений. Файл docs_version.txt отсутствует в папке docs.", e)

    def on_about(self, event):
        wx.MessageBox(
            f"y-music-blind\n\nВерсия: {VERSION}\n\n"
            "Клиент для прослушивания и скачивания музыки с Яндекс.Музыки.\n\n"
            "© 2026",
            "О программе",
            wx.OK | wx.ICON_INFORMATION,
        )
        self.safe_speak(f"О программе. Версия {VERSION}", "general")

    def on_check_updates(self, event):
        dlg = UpdateDialog(self, current_version=VERSION,
                           main_exe_name=get_main_exe_name())
        dlg.ShowModal()
        dlg.Destroy()

    def show_error(self, message, exception=None):
        play_ui_sound('error.wav')
        if exception is not None:
            log_exception(message, exception)
        else:
            logger.error(message)
        full_message = message
        if exception and self.config.get("detailed_errors"):
            full_message += f"\n\nТехническое заключение:\n{type(exception).__name__}: {str(exception)}"
        
        self.safe_speak("Произошла ошибка", "general")
        wx.MessageBox(full_message, "Ошибка", wx.OK | wx.ICON_ERROR)

    def on_open_settings(self, event):
        dlg = SettingsDialog(self, self.config)
        if dlg.ShowModal() == wx.ID_OK:
            self.track_list.show_album = self.config.get("show_track_album", True)
            self.track_list.RefreshItems(0, len(self.track_list.data) - 1)
            self._apply_appearance()
            if self.config.get("tray_show_track_name", False) and self.is_track_active:
                # Обновляем название трека в трее сразу
                source = self.current_playlist if self.current_playlist else self.track_list.data
                if 0 <= self.current_playlist_index < len(source):
                    title = self.track_list.OnGetItemText(self.current_playlist_index, 0)
                    self.tray_icon.update_tooltip(title)
            else:
                self.tray_icon.update_tooltip("")
        dlg.Destroy()
        
    def on_open_accounts(self, event):
        dlg = AccountManagerDialog(self, self.account_mgr, self.run_account_flow, self.switch_account)
        dlg.ShowModal()
        dlg.Destroy()

    def on_exit(self, event):
        self.Close()

    def on_close(self, event):
        """При закрытии: если трей включён — прячем, иначе выходим."""
        if self.config.get("minimize_to_tray", False) and event.CanVeto():
            self.Hide()
            event.Veto()
            return
        try:
            self.tray_icon.RemoveIcon()
            self.tray_icon.Destroy()
        except Exception:
            pass
        try:
            self.player.stop()
        except Exception:
            pass
        event.Skip()

    def on_iconize(self, event):
        """Сворачивание: если включена опция — прячем в трей."""
        if self.config.get("minimize_to_tray", False):
            if event.IsIconized():
                self.Hide()
        event.Skip()

    def on_activate(self, event):
        """Запоминаем последний фокус при деактивации окна."""
        if not event.GetActive():
            focus = wx.Window.FindFocus()
            if focus and focus != self:
                self._last_focused_window = focus
        event.Skip()

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
        code_count = [0]
        self._device_browser_dlg = None

        device_dlg = wx.Dialog(
            self, title="Авторизация устройства", size=(550, 420),
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
                if hasattr(self, '_device_browser_dlg') and self._device_browser_dlg and not self._device_browser_dlg.IsBeingDeleted():
                    try:
                        self._device_browser_dlg.Close()
                    except Exception:
                        pass
                event.Skip()
            else:
                event.Veto()
        device_dlg.Bind(wx.EVT_CLOSE, _on_close)

        def _on_key(event):
            if event.GetKeyCode() == wx.WXK_F5:
                self._request_new_device_code(device_dlg, status_label, result_token, auth_error, dialog_closed, code_count)
                return
            event.Skip()
        device_dlg.Bind(wx.EVT_CHAR_HOOK, _on_key)

        status_label = wx.StaticText(device_dlg, label="Получение кода устройства...\n(Нажмите F5 для обновления кода и страницы)")
        vbox.Add(status_label, flag=wx.ALL | wx.EXPAND, border=10)
        cancel_btn = wx.Button(device_dlg, label="Отмена")
        cancel_btn.Bind(wx.EVT_BUTTON, lambda e: device_dlg.Close())
        vbox.Add(cancel_btn, flag=wx.ALL | wx.ALIGN_CENTER, border=10)
        device_dlg.SetSizer(vbox)

        def _device_thread():
            try:
                temp_client = _YClient()

                def _on_code(code):
                    code_count[0] += 1
                    def _update_ui():
                        vbox.Clear(True)
                        vbox.Add(wx.StaticText(device_dlg, label="Код устройства (нажмите F5 для обновления):"),
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
                        vbox.Add(wx.StaticText(device_dlg, label="Код скопирован в буфер обмена. (Нажмите F5 для обновления кода/страницы)"),
                                 flag=wx.ALL, border=5)
                        open_btn = wx.Button(device_dlg, label="Открыть страницу в браузере")
                        open_btn.Bind(wx.EVT_BUTTON, lambda e: self._open_embedded_browser(code.verification_url))
                        vbox.Add(open_btn, flag=wx.ALL | wx.ALIGN_CENTER, border=5)

                        # Кнопка повторного запроса кода (если прежний истёк)
                        refresh_btn = wx.Button(device_dlg, label="Обновить код устройства (F5)")
                        refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._request_new_device_code(
                            device_dlg, status_label, result_token, auth_error, dialog_closed, code_count))
                        vbox.Add(refresh_btn, flag=wx.ALL | wx.ALIGN_CENTER, border=5)

                        cancel_btn2 = wx.Button(device_dlg, label="Отмена")
                        cancel_btn2.Bind(wx.EVT_BUTTON, lambda e: self.Close())
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

        if hasattr(self, '_device_browser_dlg') and self._device_browser_dlg and not self._device_browser_dlg.IsBeingDeleted():
            try:
                self._device_browser_dlg.Close()
            except Exception:
                pass
        self._device_browser_dlg = None

        if result_token[0]:
            return result_token[0]
        if auth_error[0]:
            self.show_error(f"Ошибка авторизации устройства: {auth_error[0]}")
        return None

    def _request_new_device_code(self, device_dlg, status_label, result_token, auth_error, dialog_closed, code_count):
        """Запрашивает новый код устройства по F5 или кнопке без сброса кук."""
        from yandex_music import Client as _YClient
        from yandex_music.exceptions import DeviceAuthError as _DeviceAuthError

        status_label.SetLabel("Получение нового кода устройства...")

        def _task():
            try:
                temp_client = _YClient()

                def _on_code(code):
                    code_count[0] += 1
                    def _update_ui():
                        status_label.SetLabel("Новый код устройства скопирован в буфер обмена.")
                        if wx.TheClipboard.Open():
                            wx.TheClipboard.SetData(wx.TextDataObject(code.user_code))
                            wx.TheClipboard.Close()
                        if hasattr(self, '_device_browser_dlg') and self._device_browser_dlg and not self._device_browser_dlg.IsBeingDeleted():
                            try:
                                self._device_browser_dlg.browser.LoadURL(code.verification_url)
                            except Exception:
                                pass
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

        threading.Thread(target=_task, daemon=True).start()

    def _open_embedded_browser(self, url):
        if hasattr(self, '_device_browser_dlg') and self._device_browser_dlg and not self._device_browser_dlg.IsBeingDeleted():
            try:
                self._device_browser_dlg.Close()
            except Exception:
                pass
        browser = SimpleBrowserDialog(self, url, clear_session=False)
        self._device_browser_dlg = browser
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
        
        self.search_type = wx.ComboBox(self.panel, choices=["Треки", "Исполнители", "Альбомы", "Подкасты", "Выпуски подкастов"], style=wx.CB_READONLY)
        self.search_type.SetSelection(0)
        search_box.Add(self.search_type, flag=wx.ALL, border=5)
        
        btn_search = wx.Button(self.panel, label="Поиск")
        btn_search.Bind(wx.EVT_BUTTON, self.on_search)
        search_box.Add(btn_search, flag=wx.ALL, border=5)
        
        vbox.Add(search_box, flag=wx.EXPAND)
        
        self.track_list = TrackListCtrl(self.panel)
        self.track_list.show_album = self.config.get("show_track_album", True)
        self.track_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_item_activated)
        self.track_list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context_menu)
        self.track_list.Bind(wx.EVT_CONTEXT_MENU, self.on_context_menu)
        
        vbox.Add(self.track_list, proportion=1, flag=wx.ALL | wx.EXPAND, border=5)
        
        self.time_label = wx.StaticText(self.panel, label="")
        vbox.Add(self.time_label, flag=wx.LEFT | wx.RIGHT | wx.BOTTOM, border=5)
        
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
        obj_type = classify_item(item)
        is_album = obj_type == 'album'
        is_track = obj_type == 'track'
        is_artist = obj_type == 'artist'
        is_playlist = obj_type == 'playlist'

        menu = wx.Menu()
        item_shuffle = menu.AppendCheckItem(wx.ID_ANY, "Перемешивание\tCtrl+J")
        item_shuffle.Check(self.shuffle_enabled)
        self.Bind(wx.EVT_MENU, lambda e: self.toggle_shuffle(), item_shuffle)
        menu.AppendSeparator()

        if is_track:
            item_down = menu.Append(wx.ID_ANY, "Скачать трек\tCtrl+S")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.start_download(it), item_down)
            album = self.get_track_album(item)
            if album is not None:
                menu.AppendSeparator()
                item_open_album = menu.Append(wx.ID_ANY, f"Открыть альбом: {album.title}")
                self.Bind(wx.EVT_MENU, lambda e, alb_id=album.id: self.load_album_tracks(alb_id), item_open_album)
            menu.AppendSeparator()
            item_like = menu.Append(wx.ID_ANY, "Поставить лайк\tCtrl+L")
            item_dislike = menu.Append(wx.ID_ANY, "Убрать лайк (дизлайк)\tShift+L")
            self.Bind(wx.EVT_MENU, lambda e, it_id=item.id: self.handle_like(it_id, 'track', True), item_like)
            self.Bind(wx.EVT_MENU, lambda e, it_id=item.id: self.handle_like(it_id, 'track', False), item_dislike)
            menu.AppendSeparator()
            item_add_playlist = menu.Append(wx.ID_ANY, "Добавить в плейлист...")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.add_track_to_playlist(it), item_add_playlist)
            
        elif is_playlist:
            item_open = menu.Append(wx.ID_ANY, "Открыть плейлист (треки)")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.load_playlist_tracks(it), item_open)
            menu.AppendSeparator()
            item_zip = menu.Append(wx.ID_ANY, "Скачать плейлист в ZIP\tCtrl+S")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.download_playlist_zip(it), item_zip)
            menu.AppendSeparator()
            item_like = menu.Append(wx.ID_ANY, "Поставить лайк плейлисту\tCtrl+L")
            item_dislike = menu.Append(wx.ID_ANY, "Убрать лайк плейлисту\tShift+L")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.handle_like(it, 'playlist', True), item_like)
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.handle_like(it, 'playlist', False), item_dislike)

        elif is_album:
            item_open = menu.Append(wx.ID_ANY, "Открыть альбом (треки)")
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.load_album_tracks(alb_id), item_open)
            menu.AppendSeparator()
            item_zip = menu.Append(wx.ID_ANY, "Скачать альбом в ZIP")
            self.Bind(wx.EVT_MENU, lambda e, it=item: self.download_album_zip(it), item_zip)
            menu.AppendSeparator()
            item_like = menu.Append(wx.ID_ANY, "Поставить лайк альбому\tCtrl+L")
            item_dislike = menu.Append(wx.ID_ANY, "Убрать лайк альбому\tShift+L")
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.handle_like(alb_id, 'album', True), item_like)
            self.Bind(wx.EVT_MENU, lambda e, alb_id=item.id: self.handle_like(alb_id, 'album', False), item_dislike)

        elif is_artist:
            item_artist_playlists = menu.Append(wx.ID_ANY, "Плейлисты исполнителя")
            self.Bind(wx.EVT_MENU, lambda e, art_id=item.id: self.load_artist_playlists(art_id), item_artist_playlists)

        item_copy = menu.Append(wx.ID_ANY, "Копировать ссылку\tCtrl+C")
        self.Bind(wx.EVT_MENU, lambda e, it=item: self.copy_link(it), item_copy)

        item_sys = menu.Append(wx.ID_ANY, "Открыть в системном плеере\tCtrl+O")
        self.Bind(wx.EVT_MENU, lambda e, it=item: self.open_in_system_player(it), item_sys)

        pos = wx.DefaultPosition
        if event and hasattr(event, 'GetPosition') and event.GetPosition() != wx.DefaultPosition:
            pos = self.track_list.ScreenToClient(event.GetPosition())
        self.track_list.PopupMenu(menu, pos)
        menu.Destroy()

    def on_show_item_info(self, event):
        """Показывает диалог с информацией о выбранном элементе списка (Пробел)."""
        sel = self.track_list.GetFirstSelected()
        if sel < 0 or sel >= len(self.track_list.data):
            self.safe_speak("Ничего не выбрано", "general")
            return
        item = self.track_list.data[sel]
        dlg = ItemInfoDialog(self, self._describe_item(item))
        dlg.ShowModal()
        dlg.Destroy()

    def _describe_item(self, item):
        """Собирает текстовое описание элемента списка (трек/альбом/плейлист/исполнитель)."""
        lines = []
        obj_type = classify_item(item)

        if obj_type == 'track':
            lines.append("Тип: Трек")
            lines.append(f"Название: {getattr(item, 'title', '')}")
            artists = ", ".join(a.name for a in (getattr(item, 'artists', None) or []))
            if artists:
                lines.append(f"Исполнители: {artists}")
            albums = getattr(item, 'albums', None) or []
            if albums and getattr(albums[0], 'title', None):
                lines.append(f"Альбом: {albums[0].title}")
                if getattr(albums[0], 'year', None):
                    lines.append(f"Год: {albums[0].year}")
            duration = getattr(item, 'duration_ms', None)
            if duration:
                lines.append(f"Длительность: {duration // 60000}:{(duration % 60000) // 1000:02d}")
        elif obj_type == 'album':
            lines.append("Тип: Альбом")
            lines.append(f"Название: {getattr(item, 'title', '')}")
            artists = ", ".join(a.name for a in (getattr(item, 'artists', None) or []))
            if artists:
                lines.append(f"Исполнители: {artists}")
            if getattr(item, 'year', None):
                lines.append(f"Год: {item.year}")
            if getattr(item, 'track_count', None):
                lines.append(f"Количество треков: {item.track_count}")
            if getattr(item, 'genre', None):
                lines.append(f"Жанр: {item.genre}")
            if getattr(item, 'description', None):
                lines.append(f"Описание: {item.description}")
        elif obj_type == 'playlist':
            lines.append("Тип: Плейлист")
            lines.append(f"Название: {getattr(item, 'title', '')}")
            owner = getattr(item, 'owner', None)
            if owner:
                owner_name = getattr(owner, 'name', None) or getattr(owner, 'login', None)
                if owner_name:
                    lines.append(f"Владелец: {owner_name}")
            if getattr(item, 'track_count', None):
                lines.append(f"Количество треков: {item.track_count}")
            if getattr(item, 'description', None):
                lines.append(f"Описание: {item.description}")
        elif obj_type == 'artist':
            lines.append("Тип: Исполнитель")
            lines.append(f"Имя: {getattr(item, 'name', '')}")
            counts = getattr(item, 'counts', None)
            if counts is not None:
                tracks_count = getattr(counts, 'tracks', None)
                if tracks_count:
                    lines.append(f"Треков: {tracks_count}")
        else:
            lines.append(f"Тип: {type(item).__name__}")
            lines.append(f"Название: {getattr(item, 'title', getattr(item, 'name', str(item)))}")
            for attr in ('description', 'genre'):
                value = getattr(item, attr, None)
                if value:
                    lines.append(f"{attr.capitalize()}: {value}")

        return "\n".join(lines)

    def handle_like(self, entity_id, entity_type, is_like):
        def _task():
            try:
                if entity_type == 'track':
                    self.api.like_track(entity_id) if is_like else self.api.dislike_track(entity_id)
                elif entity_type == 'album':
                    self.api.like_album(entity_id) if is_like else self.api.dislike_album(entity_id)
                elif entity_type == 'playlist':
                    self.api.like_playlist(entity_id) if is_like else self.api.dislike_playlist(entity_id)
                action = "добавлен" if is_like else "убран"
                self.safe_speak(f"Лайк {action}", "general")
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при изменении отметки", e)
        threading.Thread(target=_task, daemon=True).start()

    def open_in_system_player(self, item):
        """Открывает объект (трек, альбом, плейлист, исполнитель) в системном плеере."""
        obj_type = classify_item(item)
        title_str = getattr(item, 'title', None) or getattr(item, 'name', 'объект')
        self.safe_speak(f"Подготовка к открытию «{title_str}»", "general")
        self._active_download_name = title_str
        self._last_download_percent = 0
        self.dl_gauge.SetValue(0)
        self.dl_gauge.Show()
        self.panel.Layout()

        def _task():
            try:
                if obj_type == 'track':
                    wx.CallAfter(lambda: setattr(self, '_last_download_percent', 50))
                    wx.CallAfter(self.dl_gauge.SetValue, 50)
                    url = self.api.get_track_direct_url(item)
                    if not url:
                        wx.CallAfter(self.show_error, "Не удалось получить ссылку на трек.")
                        wx.CallAfter(self.dl_gauge.Hide)
                        wx.CallAfter(self.panel.Layout)
                        return
                    wx.CallAfter(lambda: setattr(self, '_last_download_percent', 100))
                    wx.CallAfter(self.dl_gauge.SetValue, 100)
                    self._play_url_in_system_player(item.title, url)
                    self.safe_speak("Трек открыт в системном плеере", "general")
                elif obj_type == 'album':
                    self.system_player.play_album(item, api=self.api)
                    self.safe_speak("Альбом открыт в системном плеере", "general")
                elif obj_type == 'playlist':
                    self.system_player.play_playlist(item, api=self.api)
                    self.safe_speak("Плейлист открыт в системном плеере", "general")
                elif obj_type == 'artist':
                    wx.CallAfter(lambda: setattr(self, '_last_download_percent', 10))
                    wx.CallAfter(self.dl_gauge.SetValue, 10)
                    all_tracks = []
                    page = 0
                    while True:
                        tracks, has_next = self.api.get_artist_tracks(item.id, page=page)
                        if tracks:
                            all_tracks.extend(tracks)
                            pct = min(90, 10 + page * 15)
                            wx.CallAfter(lambda p=pct: setattr(self, '_last_download_percent', p))
                            wx.CallAfter(lambda p=pct: self.dl_gauge.SetValue(p))
                        if not has_next or len(tracks) == 0 or page > 10:
                            break
                        page += 1

                    if not all_tracks:
                        wx.CallAfter(self.show_error, "У исполнителя не найдено треков.")
                        return

                    wx.CallAfter(lambda: setattr(self, '_last_download_percent', 95))
                    wx.CallAfter(self.dl_gauge.SetValue, 95)

                    import tempfile
                    fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix=f"yandex_artist_{item.id}_")
                    with os.fdopen(fd, 'w', encoding='utf-8') as f:
                        f.write("#EXTM3U\n")
                        total = len(all_tracks)
                        for i, track in enumerate(all_tracks):
                            url = self.api.get_track_direct_url(track)
                            if url:
                                artists = ", ".join([a.name for a in (getattr(track, 'artists') or [])])
                                title = f"{artists} - {track.title}" if artists else track.title
                                f.write(f"#EXTINF:-1,{title}\n")
                                f.write(f"{url}\n")
                            pct = 95 + int(((i + 1) / max(1, total)) * 5)
                            wx.CallAfter(lambda p=min(100, pct): setattr(self, '_last_download_percent', p))
                            wx.CallAfter(lambda p=min(100, pct): self.dl_gauge.SetValue(p))

                    os.startfile(temp_path)
                    wx.CallAfter(lambda: setattr(self, '_last_download_percent', 100))
                    wx.CallAfter(self.dl_gauge.SetValue, 100)
                    self.safe_speak("Исполнитель открыт в системном плеере", "general")
                else:
                    wx.CallAfter(self.show_error, "Этот тип объектов нельзя открыть в системном плеере.")
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка открытия в плеере", e)
            finally:
                wx.CallAfter(self.dl_gauge.Hide)
                wx.CallAfter(lambda: setattr(self, '_active_download_name', None))
                wx.CallAfter(lambda: setattr(self, '_last_download_percent', 100))
                wx.CallAfter(self.panel.Layout)
        threading.Thread(target=_task, daemon=True).start()

    def _play_url_in_system_player(self, title, url):
        """Создаёт временный M3U-плейлист с одним треком и открывает его системным плеером."""
        import tempfile
        fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix="yandex_track_")
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write("#EXTM3U\n")
            f.write(f"#EXTINF:-1,{title}\n")
            f.write(f"{url}\n")
        os.startfile(temp_path)
        self.safe_speak("Трек открыт в системном плеере", "general")

    def open_artist_in_system_player(self, artist_id):
        def _task():
            try:
                self.safe_speak("Загружаю треки исполнителя для плеера...", "general")
                all_tracks = []
                page = 0
                while True:
                    tracks, has_next = self.api.get_artist_tracks(artist_id, page=page)
                    if tracks:
                        all_tracks.extend(tracks)
                    if not has_next or len(tracks) == 0 or page > 10:
                        break
                    page += 1

                if not all_tracks:
                    wx.CallAfter(self.show_error, "У исполнителя не найдено треков.")
                    return

                import tempfile
                fd, temp_path = tempfile.mkstemp(suffix=".m3u", prefix=f"yandex_artist_{artist_id}_")
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write("#EXTM3U\n")
                    for track in all_tracks:
                        url = self.api.get_track_direct_url(track)
                        if url:
                            artists = ", ".join([a.name for a in (getattr(track, 'artists') or [])])
                            title = f"{artists} - {track.title}" if artists else track.title
                            f.write(f"#EXTINF:-1,{title}\n")
                            f.write(f"{url}\n")

                os.startfile(temp_path)
                self.safe_speak("Исполнитель открыт в системном плеере", "general")
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка открытия исполнителя в плеере", e)
        threading.Thread(target=_task, daemon=True).start()

    def get_track_album(self, track):
        """Возвращает альбом трека или None, если получить его невозможно."""
        try:
            albums = getattr(track, 'albums', None)
            if albums and len(albums) > 0 and getattr(albums[0], 'title', None):
                return albums[0]
        except Exception:
            logger.debug("Не удалось определить альбом трека %s.", getattr(track, 'id', track))
        return None

    def copy_link(self, item):
        url = ""
        obj_type = classify_item(item)
        is_album = obj_type == 'album'
        is_track = obj_type == 'track'

        if is_album:
            url = f"https://music.yandex.ru/album/{item.id}"
        elif is_track:
            album_id = item.albums[0].id if getattr(item, 'albums', []) else ""
            url = f"https://music.yandex.ru/album/{album_id}/track/{item.id}"
        elif obj_type == 'playlist':
            owner = getattr(item, 'owner', None)
            uid = getattr(owner, 'uid', None) if owner else getattr(item, 'uid', None)
            kind = getattr(item, 'kind', '')
            url = f"https://music.yandex.ru/users/{uid}/playlists/{kind}"
        elif obj_type == 'genre' or (hasattr(item, 'title') and not hasattr(item, 'artists') and not is_album and not obj_type == 'playlist'): 
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
        if self._pending_start:
            return
        if self.is_track_active:
            state = self.player.get_state()
            if state == 0:
                self.is_track_active = False
                prev_index = self.current_playlist_index

                if self.repeat_enabled:
                    source = self.current_playlist
                    if 0 <= prev_index < len(source):
                        self.play_track_list(source, prev_index, source[prev_index])
                        return

                self.play_next(auto=True)
        self._update_time_label()

    def _format_time(self, seconds):
        if seconds < 0:
            seconds = 0
        fmt = self.config.get("time_format", "short")
        m = int(seconds) // 60
        s = int(seconds) % 60
        if fmt == "full":
            return f"{m} мин {s} сек"
        return f"{m}:{s:02d}"

    def _update_time_label(self):
        if not self.is_track_active or self.player.get_state() == 0:
            self.time_label.SetLabel("")
            return
        pos = self.player.get_position()
        dur = self.player.get_duration()
        remaining = max(0.0, dur - pos)
        self.time_label.SetLabel(
            f"{self._format_time(pos)} / {self._format_time(dur)}  (осталось {self._format_time(remaining)})"
        )

    def reset_list_state(self):
        self.current_page = 0
        self.seen_ids.clear()
        self.track_list.data.clear()
        self.track_list.SetItemCount(0)
        self.track_list.playing_index = -1
        self.track_list.status_text = ""
        self.current_status = ""

    def _push_nav(self):
        """Сохраняет состояние списка для возврата по Escape.

        Состояние воспроизведения (очередь) сюда не попадает — системный
        список не зависит от навигации по интерфейсу.
        """
        self._nav_stack.append({
            'view_mode': self.view_mode,
            'current_page': self.current_page,
            'current_artist_id': self.current_artist_id,
            'data': list(self.track_list.data),
            'seen_ids': set(self.seen_ids),
            'focus': self.track_list.GetFirstSelected(),
        })

    def go_back(self):
        """Возвращает список на уровень выше (по Escape).

        Воспроизведение при этом не прерывается: очередь (системный список)
        продолжает работать независимо от видимого списка.
        """
        if not self._nav_stack:
            self.safe_speak("Вы уже в корне списка", "general")
            return

        state = self._nav_stack.pop()
        self.view_mode = state['view_mode']
        self.current_page = state['current_page']
        self.current_artist_id = state['current_artist_id']
        self.track_list.data = state['data']
        self.seen_ids = state['seen_ids']
        # В вернувшемся списке метку воспроизводимого трека не показываем:
        # играющий трек может не находиться в этом списке.
        self.track_list.playing_index = -1
        self.track_list.status_text = ""

        count = len(self.track_list.data)
        self.track_list.SetItemCount(count)
        if count > 0:
            self.track_list.RefreshItems(0, count - 1)

        idx = state['focus']
        self.focus_item(idx if idx >= 0 else 0)
        self._update_load_more_button()
        self.safe_speak("Возврат на уровень выше", "general")

    def _update_load_more_button(self):
        """Показывает/скрывает кнопку «Загрузить ещё» по текущему виду списка."""
        if self.view_mode == 'search':
            self.btn_load_more.Hide()
        elif self.view_mode in ('artist_tracks', 'artist_albums'):
            self.btn_load_more.Show()
        else:
            self.btn_load_more.Hide()
        self.panel.Layout()
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
        self._nav_stack.clear()
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
        elif self.view_mode == 'album_tracks' and hasattr(self, 'current_album'):
            self.load_more_album(self.current_album, append=True)

    def load_search_results(self, append=False):
        query = self.search_input.GetValue().strip()
        sel = self.search_type.GetSelection()
        # 0 — треки, 1 — исполнители, 2 — альбомы, 3 — подкасты, 4 — выпуски подкастов
        stype_map = {0: "track", 1: "artist", 2: "album", 3: "podcast", 4: "podcast_episode"}
        stype = stype_map.get(sel, "track")

        def _task():
            try:
                results, has_next = self._api_search(query, stype, self.current_page)
                wx.CallAfter(self._on_search_results_loaded, results, has_next, append)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при поиске", e)

        threading.Thread(target=_task, daemon=True).start()

    def _api_search(self, query, stype, page):
        """Выполняет поиск с учётом типа контента.

        Подкасты и выпуски подкастов обрабатываются отдельными методами API,
        альбомы — через универсальный поиск с типом "album".
        """
        if stype == "podcast":
            return self.api.search_podcasts(query, page=page)
        if stype == "podcast_episode":
            return self.api.search_podcast_episodes(query, page=page)
        return self.api.search(query, search_type=stype, page=page)

    def _on_search_results_loaded(self, results, has_next, append):
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

    def on_item_activated(self, event):
        selection = self.track_list.GetFirstSelected()
        if selection < 0 or selection >= len(self.track_list.data): return

        item = self.track_list.data[selection]
        obj_type = classify_item(item)
        is_album = obj_type == 'album'
        is_track = obj_type == 'track'
        
        if is_album:
            self.load_album_tracks(item.id)
        elif is_track:
            self.play_track(selection, item)
        elif obj_type == 'playlist':
            self.load_playlist_tracks(item)
        else:
            dlg = wx.SingleChoiceDialog(self, "Что загрузить?", "Выбор", ["Популярные треки", "Альбомы", "Плейлисты"])
            if dlg.ShowModal() == wx.ID_OK:
                sel = dlg.GetSelection()
                if sel == 0:
                    self.load_artist_tracks(item.id, append=False)
                elif sel == 1:
                    self.load_artist_albums(item.id, append=False)
                else:
                    self.load_artist_playlists(item.id)
            dlg.Destroy()

    def load_album_tracks(self, album_id):
        self._push_nav()
        self.view_mode = 'album_tracks'
        self.current_page = 0
        self.reset_list_state()
        self.current_album = None

        def _task():
            try:
                album = self.api.client.albums_with_tracks(album_id)
                wx.CallAfter(self._on_album_loaded, album)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить треки альбома", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_album_loaded(self, album):
        if album is None:
            self.safe_speak("Альбом не найден", "general")
            return
        self.current_album = album
        self._append_album_volumes(album)

    def _append_album_volumes(self, album):
        """Добавляет в список треки из volumes альбома.

        volumes — список дисков; каждый диск — список треков.
        На небольших альбомах весь список загружается сразу.
        """
        volumes = getattr(album, 'volumes', None)
        if not volumes:
            tracks = getattr(album, 'tracks', None) or []
            added = self.append_unique_items(tracks)
            count = getattr(album, 'track_count', len(tracks))
        else:
            flat = [t for vol in volumes for t in vol]
            added = self.append_unique_items(flat)
            count = getattr(album, 'track_count', len(flat))

        if added > 0:
            new_len = len(self.track_list.data)
            self.track_list.SetItemCount(new_len)
            self.track_list.RefreshItems(0, new_len - 1)
            self.focus_item(0)
        track_count = getattr(album, 'track_count', count)
        title = getattr(album, 'title', 'Альбом')
        self.safe_speak(f"Альбом: {title}. Треков: {track_count}", "general")

        # Если дисков несколько — показываем кнопку дозагрузки
        if volumes and len(volumes) > 1:
            self.btn_load_more.Show()
        else:
            self.btn_load_more.Hide()
        self.panel.Layout()

    def load_more_album(self, album, append=False):
        """Дозагружает следующий диск альбома (умная пагинация)."""
        self.current_page += 1
        volumes = getattr(album, 'volumes', None) or []

        def _task():
            try:
                full = self.api.client.albums_with_tracks(album.id)
                wx.CallAfter(self._on_more_album_loaded, full)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось дозагрузить треки альбома", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_more_album_loaded(self, full):
        if full is None:
            self.btn_load_more.Hide()
            self.panel.Layout()
            return
        volumes = getattr(full, 'volumes', None) or []
        flat = [t for vol in volumes for t in vol]
        added = self.append_unique_items(flat)
        if added > 0:
            prev_len = len(self.track_list.data) - added
            new_len = len(self.track_list.data)
            self.track_list.SetItemCount(new_len)
            self.track_list.RefreshItems(0, new_len - 1)
            self.focus_item(self.resolve_focus_index(True, prev_len))
        self.safe_speak(f"Дозагружено треков: {added}. Всего: {len(self.track_list.data)}", "general")
        self.btn_load_more.Hide()
        self.panel.Layout()



    # ── Загрузка треков / альбомов исполнителя ────────────────────────────────

    def load_playlist_tracks(self, playlist):
        """Загружает и отображает треки плейлиста.

        Треки плейлиста приходят как TrackShort — они содержат полный
        объект трека в поле track. Извлекаем его для корректной работы
        списка (иконки, воспроизведение, лайки).
        """
        self._push_nav()
        self.view_mode = 'playlist_tracks'
        self.reset_list_state()

        def _task():
            try:
                shorts = self.api.get_playlist_tracks(playlist)
                tracks = []
                for short in shorts:
                    track = getattr(short, 'track', None)
                    if track is not None:
                        tracks.append(track)
                    else:
                        tracks.append(short)
                wx.CallAfter(self._on_playlist_tracks_loaded, tracks, playlist)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить треки плейлиста", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_playlist_tracks_loaded(self, tracks, playlist):
        if tracks:
            added = self.append_unique_items(tracks)
            new_len = len(self.track_list.data)
            self.track_list.SetItemCount(new_len)
            self.track_list.RefreshItems(0, new_len - 1)
            self.focus_item(0)
            title = getattr(playlist, 'title', 'Плейлист')
            self.safe_speak(f"Плейлист: {title}. Треков: {added}", "general")
        else:
            self.safe_speak("Треки в плейлисте не найдены", "general")
        self.btn_load_more.Hide()
        self.panel.Layout()

    def load_artist_playlists(self, artist_id):
        """Загружает плейлисты исполнителя.

        Плейлисты исполнителя ищутся через поиск: этот приём позволяет
        показать коллекцию плейлистов, связанных с исполнителем.
        """
        self._push_nav()
        self.view_mode = 'artist_playlists'
        self.current_artist_id = artist_id
        self.reset_list_state()

        def _task():
            try:
                brief = self.api.client.artists_brief_info(artist_id)
                playlists = []
                if brief is not None and getattr(brief, 'playlists', None):
                    playlists = brief.playlists
                wx.CallAfter(self._on_artist_playlists_loaded, playlists)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить плейлисты исполнителя", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_artist_playlists_loaded(self, playlists):
        if playlists:
            added = self.append_unique_items(playlists)
            new_len = len(self.track_list.data)
            self.track_list.SetItemCount(new_len)
            self.track_list.RefreshItems(0, new_len - 1)
            self.focus_item(0)
            self.safe_speak(f"Плейлистов исполнителя: {added}", "general")
        else:
            self.safe_speak("Плейлисты не найдены", "general")
        self.btn_load_more.Hide()
        self.panel.Layout()

    def add_track_to_playlist(self, track):
        """Добавляет выбранный трек в один из плейлистов пользователя."""
        def _task():
            try:
                playlists = self.api.get_users_playlists()
                if not playlists:
                    wx.CallAfter(self.show_error, "У вас нет плейлистов. Создайте его в меню «Сервис → Мои плейлисты».")
                    return
                names = [f"{p.title}" for p in playlists]

                def _choose():
                    dlg = wx.SingleChoiceDialog(self, "В какой плейлист добавить трек?", "Добавление в плейлист", names)
                    if dlg.ShowModal() == wx.ID_OK:
                        idx = dlg.GetSelection()
                        dlg.Destroy()
                        kind = playlists[idx].kind
                        album = self.get_track_album(track)
                        album_id = album.id if album else 0
                        try:
                            ok = self.api.add_tracks_to_playlist(kind, [(album_id, track.id)])
                        except Exception as e:
                            wx.CallAfter(self.show_error, "Не удалось добавить трек в плейлист.", e)
                            return
                        if ok:
                            self.safe_speak("Трек добавлен в плейлист", "general")
                        else:
                            self.show_error("Не удалось добавить трек в плейлист.")
                    else:
                        dlg.Destroy()

                wx.CallAfter(_choose)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при добавлении в плейлист", e)

        threading.Thread(target=_task, daemon=True).start()

    def download_playlist_zip(self, playlist):
        """Скачивает плейлист в один ZIP-архив."""
        from download_zip import download_playlist_to_zip
        threading.Thread(
            target=download_playlist_to_zip,
            args=(self, self.api, playlist),
            daemon=True,
        ).start()

    def download_album_zip(self, album):
        """Скачивает альбом в один ZIP-архив."""
        from download_zip import download_album_to_zip
        threading.Thread(
            target=download_album_to_zip,
            args=(self, self.api, album),
            daemon=True,
        ).start()

    def load_artist_tracks(self, artist_id, append=False):
        if not append:
            self._push_nav()
            self.view_mode = 'artist_tracks'
            self.current_artist_id = artist_id
            self.reset_list_state()
            
        def _task():
            try:
                tracks, has_next = self.api.get_artist_tracks(artist_id, page=self.current_page)
                wx.CallAfter(self._on_artist_tracks_loaded, tracks, has_next, append)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить треки исполнителя.", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_artist_tracks_loaded(self, tracks, has_next, append):
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
            
    def load_artist_albums(self, artist_id, append=False):
        if not append:
            self._push_nav()
            self.view_mode = 'artist_albums'
            self.current_artist_id = artist_id
            self.reset_list_state()
            
        def _task():
            try:
                albums, has_next = self.api.get_artist_albums(artist_id, page=self.current_page)
                wx.CallAfter(self._on_artist_albums_loaded, albums, has_next, append)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить альбомы исполнителя.", e)

        threading.Thread(target=_task, daemon=True).start()

    def _on_artist_albums_loaded(self, albums, has_next, append):
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

    def play_track(self, index, track):
        """Начинает воспроизведение трека из основного списка интерфейса."""
        self.play_track_list(self.track_list.data, index, track)

    def play_track_list(self, tracks, index, track=None):
        """Начинает воспроизведение трека с индексом index в списке tracks.

        Список может не совпадать с основным списком интерфейса (например,
        треки плейлиста из диалога) — тогда строки основного списка не отмечаем.
        """
        try:
            if self.track_list.playing_index != -1:
                old_index = self.track_list.playing_index
                self.track_list.playing_index = -1
                self.track_list.RefreshItem(old_index)

            if 0 <= index < len(self.track_list.data):
                self.update_track_status(index, "[Воспроизводится]")
                self.focus_item(index)

            self.current_playlist = list(tracks)
            self.current_playlist_index = index

            # Строим очередь воспроизведения из треков текущего списка
            self.queue_indices = [i for i, it in enumerate(tracks) if classify_item(it) == 'track']
            if index not in self.queue_indices:
                self.queue_indices.append(index)
            self.queue_position = self.queue_indices.index(index) if index in self.queue_indices else 0

            if track is None:
                if not (0 <= index < len(tracks)):
                    raise ValueError("В списке нет трека для воспроизведения")
                track = tracks[index]

            # Пока ссылка готовится, автопереход в on_timer заблокирован.
            self._pending_start = True
            self.is_track_active = False
            self._play_epoch += 1
            epoch = self._play_epoch

            # Получаем прямую ссылку в фоне, чтобы не замораживать интерфейс
            self.api.get_track_direct_url_async(
                track, lambda url: self._start_playing(index, track, url, epoch)
            )
        except Exception as e:
            self.show_error("Произошла системная ошибка при попытке воспроизведения.", e)

    def _start_playing(self, index, track, url, epoch):
        """Начинает воспроизведение трека после получения прямой ссылки.

        Вызывается из фонового потока: BASS можно запускать оттуда,
        но обновление списка — только через wx.CallAfter.
        """
        if epoch != self._play_epoch:
            return
        if not url:
            self._pending_start = False
            wx.CallAfter(self.show_error, "Не удалось получить ссылку на аудиопоток. Возможно, трек недоступен в вашем регионе.")
            return
        try:
            ok = self.player.play_url(url)
            self._pending_start = False
            if ok:
                self.is_track_active = True
                wx.CallAfter(self._mark_playing, index)
            else:
                wx.CallAfter(self.show_error, "Не удалось начать воспроизведение потока.")
        except Exception as e:
            self._pending_start = False
            wx.CallAfter(self.show_error, "Ошибка при начале воспроизведения.", e)

    def _mark_playing(self, index):
        """Помечает трек как воспроизводимый в списке (вызывается из главного потока)."""
        if self.track_list.playing_index != index:
            self.update_track_status(index, "[Воспроизводится]")
        if self.config.get("tray_show_track_name", False):
            title = self.track_list.OnGetItemText(index, 0)
            self.tray_icon.update_tooltip(title)

    def start_download(self, track):
        download_dir = self.config.get("download_dir", "")
        artists = ", ".join([a.name for a in (getattr(track, 'artists') or [])])
        safe_title = "".join(c for c in f"{artists} - {track.title}" if c.isalnum() or c in " -_").strip()
        save_path = os.path.join(download_dir, f"{safe_title}.mp3")
        
        self._active_download_name = f"{artists} - {track.title}" if artists else track.title
        self._last_download_percent = 0
        self.safe_speak("Начато скачивание трека", "general")
        self.dl_gauge.SetValue(0)
        self.dl_gauge.Show()
        self.panel.Layout()

        def _dl():
            def _progress_callback(recvd, total):
                if total > 0:
                    percent = int((recvd / total) * 100)
                    percent = min(100, max(0, percent))
                    self._last_download_percent = percent
                    wx.CallAfter(self.dl_gauge.SetValue, percent)

            success, err = self.api.download_track(track, save_path, progress_callback=_progress_callback)
            
            def _finish():
                self.dl_gauge.Hide()
                self._active_download_name = None
                self._last_download_percent = 100
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
        if keyboard.handle_key_event(self, event):
            return
        event.Skip()

    def MSWWindowProc(self, msg, wParam, lParam):
        if hasattr(self, 'bluetooth_manager') and self.bluetooth_manager.handle_window_message(msg, wParam, lParam):
            if msg == 0x0312:  # WM_HOTKEY
                return 0
            elif msg == 0x0319:  # WM_APPCOMMAND
                return 1
            elif msg in (0x0100, 0x0104):
                return 0
            return 0
        return super().MSWWindowProc(msg, wParam, lParam)

if __name__ == '__main__':
    app = wx.App(False)
    frame = MainFrame()
    frame.Show()
    app.MainLoop()
