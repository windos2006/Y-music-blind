# Year: 2026
import wx
import logging
import keyboard

logger = logging.getLogger(__name__)


class PlaylistTracksDialog(wx.Dialog):
    """Диалог со списком треков плейлиста.

    Управление — через контекстное меню и клавиатуру: Enter слушает трек,
    Delete удаляет его из плейлиста. Горячие клавиши воспроизведения
    (громкость, скорость, перемотка и т.п.) работают и здесь.
    """

    def __init__(self, parent, api, playlist, tracks):
        super().__init__(parent, title="Треки плейлиста", size=(600, 450),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.api = api
        self.playlist = playlist
        self.tracks = tracks or []
        self.init_ui()
        self.refresh_list()

    def init_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)
        self.list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER)
        self.list.InsertColumn(0, "Трек", width=560)
        vbox.Add(self.list, proportion=1, flag=wx.EXPAND | wx.ALL, border=5)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_play)
        self.list.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_context_menu)
        self.list.Bind(wx.EVT_KEY_DOWN, self.on_key_down)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)

        hbox_btns = wx.BoxSizer(wx.HORIZONTAL)
        btn_load_more = wx.Button(self, label="Загрузить ещё")
        btn_close = wx.Button(self, label="Закрыть")
        btn_load_more.Bind(wx.EVT_BUTTON, self.on_load_more)
        btn_close.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        hbox_btns.Add(btn_load_more, flag=wx.RIGHT, border=5)
        hbox_btns.Add(btn_close)
        vbox.Add(hbox_btns, flag=wx.ALL | wx.ALIGN_CENTER, border=5)

        self.SetSizer(vbox)

    def on_load_more(self, event):
        """Дозагружает треки плейлиста, если они есть."""
        kind = getattr(self.playlist, 'kind', None)
        if kind is None:
            wx.MessageBox("Невозможно дозагрузить треки для этого плейлиста.", "Информация", wx.OK | wx.ICON_INFORMATION)
            return
        def _task():
            try:
                full = self.api.client.users_playlists(kind, getattr(self.playlist, 'uid', None))
                if full and hasattr(full, 'tracks') and full.tracks:
                    new_tracks = []
                    for short in full.tracks:
                        t = getattr(short, 'track', None)
                        if t is not None:
                            new_tracks.append(t)
                        elif hasattr(short, 'fetch_track'):
                            try:
                                new_tracks.append(short.fetch_track())
                            except Exception:
                                pass
                    if len(new_tracks) > len(self.tracks):
                        self.tracks = new_tracks
                        wx.CallAfter(self.refresh_list)
                        wx.CallAfter(self.safe_speak, f"Загружено треков: {len(self.tracks)}", "general")
                    else:
                        wx.CallAfter(self.safe_speak, "Все треки уже загружены", "general")
                else:
                    wx.CallAfter(self.safe_speak, "Новых треков не найдено", "general")
            except Exception as e:
                wx.CallAfter(self.safe_speak, "Ошибка при дозагрузке треков", "general")
        import threading
        threading.Thread(target=_task, daemon=True).start()

    def refresh_list(self):
        self.list.DeleteAllItems()
        for track in self.tracks:
            artists = ", ".join(a.name for a in (getattr(track, 'artists', None) or []))
            title = getattr(track, 'title', '')
            label = f"{artists} - {title}" if artists else title
            self.list.InsertItem(self.list.GetItemCount(), label)

    def _selected(self):
        sel = self.list.GetFirstSelected()
        if sel < 0 or sel >= len(self.tracks):
            return None
        return sel

    def on_play(self, event):
        sel = self._selected()
        if sel is None:
            return
        track = self.tracks[sel]
        parent = self.GetParent()
        if hasattr(parent, 'GetParent'):
            frame = parent.GetParent()
            if hasattr(frame, 'play_track_list'):
                frame.play_track_list(self.tracks, sel, track)

    def on_remove(self, event):
        sel = self._selected()
        if sel is None:
            return
        track = self.tracks[sel]
        dlg = wx.MessageDialog(
            self, "Удалить выбранный трек из плейлиста?", "Удаление из плейлиста",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        ok = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        if not ok:
            return
        kind = self.playlist.kind
        ok = self.api.remove_track_from_playlist(kind, sel)
        if ok:
            del self.tracks[sel]
            self.refresh_list()
            self.safe_speak("Трек удален из плейлиста", "general")
        else:
            wx.MessageBox("Не удалось удалить трек из плейлиста.", "Ошибка", wx.OK | wx.ICON_ERROR)

    def on_context_menu(self, event):
        menu = wx.Menu()
        item_play = menu.Append(wx.ID_ANY, "Слушать")
        item_remove = menu.Append(wx.ID_ANY, "Удалить из плейлиста")
        menu.Bind(wx.EVT_MENU, self.on_play, item_play)
        menu.Bind(wx.EVT_MENU, self.on_remove, item_remove)
        self.PopupMenu(menu)
        menu.Destroy()

    def _main_frame(self):
        """Возвращает главное окно программы (через диалог плейлистов)."""
        parent = self.GetParent()
        if parent is not None and hasattr(parent, 'GetParent'):
            frame = parent.GetParent()
            if frame is not None and hasattr(frame, 'change_volume'):
                return frame
        return None

    def on_key_down(self, event):
        keycode = event.GetKeyCode()
        if keycode in (wx.WXK_DELETE, wx.WXK_NUMPAD_DELETE):
            self.on_remove(None)
            return
        if keycode == wx.WXK_SPACE:
            sel = self._selected()
            if sel is not None:
                self._show_track_info(sel)
            return
        event.Skip()

    def _show_track_info(self, index):
        """Показывает информацию о треке из плейлиста (Пробел)."""
        track = self.tracks[index]
        lines = []
        lines.append(f"Название: {getattr(track, 'title', '')}")
        artists = ", ".join(a.name for a in (getattr(track, 'artists', None) or []))
        if artists:
            lines.append(f"Исполнители: {artists}")
        albums = getattr(track, 'albums', None) or []
        if albums and getattr(albums[0], 'title', None):
            lines.append(f"Альбом: {albums[0].title}")
        duration = getattr(track, 'duration_ms', None)
        if duration:
            lines.append(f"Длительность: {duration // 60000}:{(duration % 60000) // 1000:02d}")
        from main import ItemInfoDialog
        dlg = ItemInfoDialog(self, "\n".join(lines))
        dlg.ShowModal()
        dlg.Destroy()

    def on_char_hook(self, event):
        """Перехватывает клавиши."""
        frame = self._main_frame()
        keycode = event.GetKeyCode()
        # По Пробелу в диалоге треков плейлиста показываем инфо о треке, а не паузу
        if keycode == wx.WXK_SPACE:
            sel = self._selected()
            if sel is not None:
                self._show_track_info(sel)
                return
        if frame is not None and keyboard.handle_media_key_event(frame, event, allow_space=False):
            return
        if keycode == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CLOSE)
            return
        event.Skip()

    def safe_speak(self, text, category="general"):
        parent = self.GetParent()
        if hasattr(parent, 'safe_speak'):
            parent.safe_speak(text, category)
