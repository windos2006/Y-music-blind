# Year: 2026
import wx
import threading
import logging
import keyboard

logger = logging.getLogger(__name__)


class PlaylistsDialog(wx.Dialog):
    """Диалог управления плейлистами пользователя.

    Позволяет просматривать список плейлистов, открывать их треки,
    создавать новые, переименовывать и удалять существующие.
    """

    def __init__(self, parent, api):
        super().__init__(parent, title="Мои плейлисты", size=(550, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.api = api
        self.playlists = []
        self.init_ui()
        self.load_playlists()

    def init_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER)
        self.list.InsertColumn(0, "Плейлист", width=480)
        vbox.Add(self.list, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_open)
        self.list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context_menu)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        hbox = wx.BoxSizer(wx.HORIZONTAL)
        btn_open = wx.Button(self, label="Открыть")
        btn_new = wx.Button(self, label="Создать")
        btn_rename = wx.Button(self, label="Переименовать")
        btn_personal = wx.Button(self, label="Персональные")
        btn_delete = wx.Button(self, label="Удалить")
        btn_close = wx.Button(self, label="Закрыть")
        btn_open.Bind(wx.EVT_BUTTON, self.on_open)
        btn_new.Bind(wx.EVT_BUTTON, self.on_create)
        btn_rename.Bind(wx.EVT_BUTTON, self.on_rename)
        btn_personal.Bind(wx.EVT_BUTTON, self.on_personal_menu)
        btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        hbox.Add(btn_open, flag=wx.RIGHT, border=5)
        hbox.Add(btn_new, flag=wx.RIGHT, border=5)
        hbox.Add(btn_rename, flag=wx.RIGHT, border=5)
        hbox.Add(btn_personal, flag=wx.RIGHT, border=5)
        hbox.Add(btn_delete, flag=wx.RIGHT, border=5)
        hbox.Add(btn_close)
        vbox.Add(hbox, flag=wx.ALL | wx.ALIGN_CENTER, border=5)
        self.SetSizer(vbox)

    def load_playlists(self):
        """Загружает список плейлистов в фоновом потоке."""
        def _task():
            try:
                playlists = self.api.get_users_playlists()
                wx.CallAfter(self._on_loaded, playlists)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при загрузке плейлистов", e)
        threading.Thread(target=_task, daemon=True).start()

    def _on_loaded(self, playlists):
        self.playlists = playlists or []
        self.list.DeleteAllItems()
        for pl in self.playlists:
            title = getattr(pl, 'title', 'Без названия')
            count = getattr(pl, 'track_count', '')
            label = f"{title} [Треков: {count}]" if count else title
            self.list.InsertItem(self.list.GetItemCount(), label)
        if not self.playlists:
            self.list.InsertItem(0, "Плейлистов нет")

    def _selected_playlist(self):
        sel = self.list.GetFirstSelected()
        if sel < 0 or sel >= len(self.playlists):
            return None
        return self.playlists[sel]

    def on_open(self, event):
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        self._load_playlist_tracks(pl)

    def _load_playlist_tracks(self, pl):
        def _task():
            try:
                shorts = self.api.get_playlist_tracks(pl)
                tracks = []
                for short in shorts:
                    track = getattr(short, 'track', None)
                    tracks.append(track if track is not None else short)
                wx.CallAfter(self._show_playlist_tracks, pl, tracks)
            except Exception as e:
                wx.CallAfter(self.show_error, "Не удалось загрузить треки плейлиста", e)
        threading.Thread(target=_task, daemon=True).start()

    def _show_playlist_tracks(self, pl, tracks):
        """Показывает треки плейлиста в отдельном диалоге."""
        from playlist_tracks_dialog import PlaylistTracksDialog
        dlg = PlaylistTracksDialog(self, self.api, pl, tracks)
        dlg.ShowModal()
        dlg.Destroy()

    def on_create(self, event):
        dlg = wx.TextEntryDialog(self, "Введите название нового плейлиста:", "Создание плейлиста")
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        title = dlg.GetValue().strip()
        dlg.Destroy()
        if not title:
            return
        pl = self.api.create_playlist(title)
        if pl:
            self.safe_speak("Плейлист создан", "general")
            self.load_playlists()
        else:
            self.show_error("Не удалось создать плейлист.")

    def on_rename(self, event):
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(self, "Новое название плейлиста:", "Переименование", value=pl.title)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        new_title = dlg.GetValue().strip()
        dlg.Destroy()
        if not new_title:
            return
        if self.api.rename_playlist(pl.kind, new_title):
            self.safe_speak("Плейлист переименован", "general")
            self.load_playlists()
        else:
            self.show_error("Не удалось переименовать плейлист.")

    def on_delete(self, event):
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.MessageDialog(
            self, f"Удалить плейлист «{pl.title}»?", "Удаление плейлиста",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        ok = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        if ok and self.api.delete_playlist(pl.kind):
            self.safe_speak("Плейлист удален", "general")
            self.load_playlists()

    def on_context_menu(self, event):
        pl = self._selected_playlist()
        menu = wx.Menu()
        item_open = menu.Append(wx.ID_ANY, "Открыть")
        item_zip = menu.Append(wx.ID_ANY, "Скачать плейлист в ZIP")
        menu.AppendSeparator()
        item_recommend = menu.Append(wx.ID_ANY, "Рекомендации по плейлисту")
        item_similar = menu.Append(wx.ID_ANY, "Похожие сущности")
        visibility = getattr(pl, 'visibility', None) if pl else None
        item_visibility = menu.Append(
            wx.ID_ANY,
            "Сделать приватным" if visibility == 'public' else "Сделать публичным",
        )
        item_description = menu.Append(wx.ID_ANY, "Изменить описание")
        item_trailer = menu.Append(wx.ID_ANY, "Информация о трейлере")
        menu.AppendSeparator()
        item_rename = menu.Append(wx.ID_ANY, "Переименовать")
        item_delete = menu.Append(wx.ID_ANY, "Удалить")
        menu.AppendSeparator()
        item_close = menu.Append(wx.ID_ANY, "Закрыть")
        menu.Bind(wx.EVT_MENU, self.on_open, item_open)
        menu.Bind(wx.EVT_MENU, self.on_download_zip, item_zip)
        menu.Bind(wx.EVT_MENU, self.on_recommendations, item_recommend)
        menu.Bind(wx.EVT_MENU, self.on_similar, item_similar)
        menu.Bind(wx.EVT_MENU, self.on_toggle_visibility, item_visibility)
        menu.Bind(wx.EVT_MENU, self.on_edit_description, item_description)
        menu.Bind(wx.EVT_MENU, self.on_show_trailer, item_trailer)
        menu.Bind(wx.EVT_MENU, self.on_rename, item_rename)
        menu.Bind(wx.EVT_MENU, self.on_delete, item_delete)
        menu.Bind(wx.EVT_MENU, lambda e: self.EndModal(wx.ID_CLOSE), item_close)
        self.PopupMenu(menu)
        menu.Destroy()

    def on_download_zip(self, event):
        """Скачивает выбранный плейлист целиком в ZIP-архив."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        frame = self._main_frame()
        if frame is None:
            self.show_error("Не удалось найти главное окно программы.")
            return
        from download_zip import download_playlist_to_zip
        threading.Thread(
            target=download_playlist_to_zip,
            args=(frame, self.api, pl),
            daemon=True,
        ).start()

    # ── Новые методы из документации ─────────────────────────────────────────

    PERSONAL_PLAYLISTS = [
        ("Плейлист дня", "daily"),
        ("Тайник", "missedLikes"),
        ("Премьера", "recentTracks"),
        ("Дежавю", "neverHeard"),
        ("Подкасты недели", "podcasts"),
        ("Плейлист с Алисой", "origin"),
    ]

    def on_personal_menu(self, event):
        """Показывает меню персональных плейлистов пользователя."""
        menu = wx.Menu()
        menu_items = []
        for title, pid in self.PERSONAL_PLAYLISTS:
            item = menu.Append(wx.ID_ANY, title)
            menu_items.append((item, pid))

        def _on_select(e):
            for item, pid in menu_items:
                if item.GetId() == e.GetId():
                    self._load_personal_playlist(pid)
                    break

        menu.Bind(wx.EVT_MENU, _on_select)
        self.PopupMenu(menu)
        menu.Destroy()

    def _load_personal_playlist(self, playlist_id):
        """Загружает персональный плейлист и показывает его треки."""
        title = dict(self.PERSONAL_PLAYLISTS).get(playlist_id, playlist_id)

        def _task():
            try:
                gen = self.api.get_personal_playlist(playlist_id)
                if gen is None:
                    wx.CallAfter(self.safe_speak, f"«{title}» пока недоступен или отсутствует", "general")
                    return
                data = getattr(gen, 'data', None)
                if data is None:
                    wx.CallAfter(self.safe_speak, f"«{title}» пока недоступен", "general")
                    return
                shorts = getattr(data, 'tracks', None) or []
                tracks = []
                for s in shorts:
                    t = getattr(s, 'track', None)
                    if t is not None:
                        tracks.append(t)
                    elif hasattr(s, 'fetch_track'):
                        try:
                            tracks.append(s.fetch_track())
                        except Exception:
                            pass
                if not tracks:
                    wx.CallAfter(self.safe_speak, f"В плейлисте «{title}» нет треков", "general")
                    return
                wx.CallAfter(self._show_tracks, data, tracks)
            except Exception as e:
                wx.CallAfter(self.safe_speak, f"Плейлист «{title}» временно недоступен", "general")

        threading.Thread(target=_task, daemon=True).start()

    def on_recommendations(self, event):
        """Показывает рекомендации треков для выбранного плейлиста."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return

        def _task():
            try:
                recs = self.api.get_playlist_recommendations(pl.kind)
                if recs is None:
                    wx.CallAfter(self.safe_speak, "Рекомендаций нет", "general")
                    return
                raw_tracks = getattr(recs, 'tracks', None) or []
                tracks = []
                for t in raw_tracks:
                    track = getattr(t, 'track', None)
                    tracks.append(track if track is not None else t)
                if not tracks:
                    wx.CallAfter(self.safe_speak, "Рекомендаций нет", "general")
                    return
                wx.CallAfter(self._show_tracks, pl, tracks)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при загрузке рекомендаций", e)

        threading.Thread(target=_task, daemon=True).start()

    def on_similar(self, event):
        """Показывает похожие сущности (альбомы, исполнители) для плейлиста."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        playlist_uuid = getattr(pl, 'playlist_uuid', None) or getattr(pl, 'kind', None)

        def _task():
            try:
                sim = self.api.get_playlist_similar_entities(playlist_uuid)
                if sim is None:
                    wx.CallAfter(self.safe_speak, "Похожих сущностей нет", "general")
                    return
                items = getattr(sim, 'items', None) or []
                entities = []
                for item in items:
                    data = getattr(item, 'data', None)
                    agent = getattr(data, 'agent', None) if data else None
                    entity = getattr(agent, 'entity', None) if agent else None
                    if entity is None:
                        continue
                    obj = getattr(entity, 'album', None) or getattr(entity, 'artist', None)
                    if obj is not None and obj not in entities:
                        entities.append(obj)
                wx.CallAfter(self._similar_result, entities)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при загрузке похожих сущностей", e)

        threading.Thread(target=_task, daemon=True).start()

    def _similar_result(self, entities):
        if not entities:
            self.safe_speak("Похожих сущностей нет", "general")
            return
        lines = []
        for entity in entities:
            kind = "Альбом" if type(entity).__name__ == 'Album' else "Исполнитель"
            title = getattr(entity, 'title', None) or getattr(entity, 'name', 'Без названия')
            count = getattr(entity, 'track_count', '')
            lines.append(f"{kind}: {title}" + (f" [{count} треков]" if count else ""))
        wx.MessageBox(
            "Похожие сущности плейлиста:\n\n" + "\n".join(lines),
            "Похожие сущности",
            wx.OK | wx.ICON_INFORMATION,
        )

    def _show_tracks(self, pl, tracks):
        """Показывает список треков в диалоге плейлиста."""
        if not tracks:
            self.safe_speak("В списке нет треков", "general")
            return
        from playlist_tracks_dialog import PlaylistTracksDialog
        dlg = PlaylistTracksDialog(self, self.api, pl, tracks)
        dlg.ShowModal()
        dlg.Destroy()

    def on_toggle_visibility(self, event):
        """Переключает видимость плейлиста: публичный <-> приватный."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        current = getattr(pl, 'visibility', 'public')
        new_value = 'private' if current == 'public' else 'public'
        result = self.api.set_playlist_visibility(pl.kind, new_value)
        if result is not None:
            self.safe_speak(
                "Плейлист теперь приватный" if new_value == 'private' else "Плейлист теперь публичный",
                "general",
            )
            self.load_playlists()
        else:
            self.show_error("Не удалось изменить видимость плейлиста.")

    def on_edit_description(self, event):
        """Изменяет описание плейлиста."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        dlg = wx.TextEntryDialog(
            self,
            "Новое описание плейлиста:",
            "Описание плейлиста",
            value=getattr(pl, 'description', '') or '',
        )
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        description = dlg.GetValue().strip()
        dlg.Destroy()
        result = self.api.set_playlist_description(pl.kind, description)
        if result is not None:
            self.safe_speak("Описание плейлиста обновлено", "general")
        else:
            self.show_error("Не удалось изменить описание плейлиста.")

    def on_show_trailer(self, event):
        """Показывает информацию о трейлере плейлиста."""
        pl = self._selected_playlist()
        if not pl:
            wx.MessageBox("Выберите плейлист.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return

        def _task():
            try:
                trailer = self.api.get_playlist_trailer(pl.kind)
                wx.CallAfter(self._trailer_result, trailer)
            except Exception as e:
                wx.CallAfter(self.show_error, "Ошибка при загрузке трейлера", e)

        threading.Thread(target=_task, daemon=True).start()

    def _trailer_result(self, trailer):
        if trailer is None:
            wx.MessageBox("У этого плейлиста нет трейлера.", "Трейлер", wx.OK | wx.ICON_INFORMATION)
        else:
            title = getattr(trailer, 'title', 'Трейлер плейлиста')
            wx.MessageBox(title, "Трейлер", wx.OK | wx.ICON_INFORMATION)

    def _main_frame(self):
        """Возвращает главное окно программы (владельца диалога)."""
        parent = self.GetParent()
        if parent is not None and hasattr(parent, 'change_volume'):
            return parent
        return None

    def on_key_down(self, event):
        keycode = event.GetKeyCode()
        ctrl_down = event.ControlDown()
        if keycode in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
            self.on_delete(None)
            return
        if ctrl_down and keycode == ord('S'):
            self.on_download_zip(None)
            return
        if keycode == wx.WXK_SPACE:
            pl = self._selected_playlist()
            if pl:
                self._show_playlist_info(pl)
            return
        event.Skip()

    def _show_playlist_info(self, pl):
        """Показывает информацию о плейлисте (Пробел)."""
        lines = []
        lines.append(f"Название: {getattr(pl, 'title', 'Без названия')}")
        owner = getattr(pl, 'owner', None)
        if owner:
            owner_name = getattr(owner, 'name', None) or getattr(owner, 'login', None)
            if owner_name:
                lines.append(f"Владелец: {owner_name}")
        if getattr(pl, 'track_count', None):
            lines.append(f"Количество треков: {pl.track_count}")
        if getattr(pl, 'description', None):
            lines.append(f"Описание: {pl.description}")
        if getattr(pl, 'visibility', None):
            lines.append(f"Видимость: {pl.visibility}")
        text = "\n".join(lines)
        from main import ItemInfoDialog
        dlg = ItemInfoDialog(self, text)
        dlg.ShowModal()
        dlg.Destroy()

    def on_char_hook(self, event):
        """Перехватывает клавиши: медиаклавиши работают и в этом диалоге."""
        frame = self._main_frame()
        if frame is not None and keyboard.handle_media_key_event(frame, event, allow_space=True):
            return
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()

    def show_error(self, message, exception=None):
        full_message = message
        parent = self.GetParent()
        if exception and hasattr(parent, 'config') and parent.config.get("detailed_errors"):
            full_message += f"\n\nТехническое заключение:\n{type(exception).__name__}: {str(exception)}"
        wx.MessageBox(full_message, "Ошибка", wx.OK | wx.ICON_ERROR)
        if exception:
            logger.error(message, exc_info=exception)

    def safe_speak(self, text, category="general"):
        parent = self.GetParent()
        if hasattr(parent, 'safe_speak'):
            parent.safe_speak(text, category)
