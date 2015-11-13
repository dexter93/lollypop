# Copyright (c) 2014-2015 Cedric Bellegarde <cedric.bellegarde@adishatz.org>
# Copyright (C) 2011 kedals0@gmail.com
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

from gi.repository import GLib, Gst

from datetime import datetime
import socketserver
import threading
import os

from lollypop.define import Lp, Type
from lollypop.objects import Track
from lollypop.database_mpd import MpdDatabase
from lollypop.utils import translate_artist_name, format_artist_name, get_ip


class MpdHandler(socketserver.StreamRequestHandler):
    # Delayed signal
    _PLCHANGES = ["add", "delete", "clear", "deleteid", "move",
                  "moveid", "load", "playlistadd"]

    def __init__(self, arg1, arg2, arg3):
        """
            Init handler
        """
        self._is_idle = False
        self._mpddb = MpdDatabase()
        self._playlist = {}
        self._playlist_version = 0
        self._idle_wanted_strings = []
        self._idle_strings = []
        self._last_tracks = Lp().playlists.get_tracks_ids(Type.MPD)
        socketserver.StreamRequestHandler.__init__(self, arg1, arg2, arg3)

    def handle(self):
        """
            One function to handle them all
        """
        self.request.send("OK MPD 0.19.0\n".encode('utf-8'))
        self.__connect()
        while self.server.running:
            msg = ""
            try:
                cmdlist = None
                cmds = []
                # check for delayed plchanges
                delayed = False
                # Read commands
                while True:
                    data = self.rfile.readline().strip().decode("utf-8")
                    if len(data) == 0:
                        raise IOError  # EOF
                    if data == "command_list_ok_begin":
                        cmdlist = "list_ok"
                    elif data == "command_list_begin":
                        cmdlist = "list"
                    elif data == "command_list_end":
                        break
                    else:
                        cmds.append(data)
                        if not cmdlist:
                            break
                try:
                    print(cmds)
                    for cmd in cmds:
                        command = cmd.split(' ')[0]
                        size = len(command) + 1
                        args = cmd[size:]
                        if command in self._PLCHANGES:
                            delayed = True
                        call = getattr(self, '_%s' % command)
                        msg += call(args)
                        if cmdlist == "list_ok":
                            msg += "list_OK\n"
                except Exception as e:
                    print("MpdHandler::handle(): ", cmds, e)
                    raise
                msg += "OK\n"
                self.request.send(msg.encode("utf-8"))
                print(msg.encode("utf-8"), self)
                if delayed:
                    print('DELAYED')
                    GLib.idle_add(Lp().playlists.emit,
                                  'playlist-changed',
                                  Type.MPD)
            except Exception as e:
                print("IOERRRO")
                break
        self.__connect(False)

    def _add(self, cmd_args):
        """
            Add track to mpd playlist
            @syntax add filepath
            @param args as str
            @return msg as str
        """
        tracks = []
        arg = self._get_args(cmd_args)[0]
        track_id = Lp().tracks.get_id_by_path(arg)
        if track_id is None:
            path = ""
            for musicpath in Lp().settings.get_music_paths():
                search = musicpath.replace("/", "_")
                if search in arg:
                    path = musicpath + arg.replace(search, path)
            if os.path.isdir(path):
                tracks_ids = Lp().tracks.get_ids_by_path(path)
                for track_id in tracks_ids:
                    tracks.append(Track(track_id))
            elif os.path.isfile(path):
                track_id = Lp().tracks.get_id_by_path(path)
                tracks.append(Track(track_id))
        else:
            tracks.append(Track(track_id))
        Lp().playlists.add_tracks(Type.MPD, tracks, False)
        return ""

    def _clear(self, cmd_args):
        """
            Clear mpd playlist
            @syntax clear
            @param args as str
            @return msg as str
        """
        Lp().playlists.clear(Type.MPD, False)
        Lp().player.set_user_playlist_id(Type.NONE)
        GLib.idle_add(Lp().player.stop)
        Lp().player.current_track = Track()
        GLib.idle_add(Lp().player.emit, 'current-changed')
        return ""

    def _channels(self, cmd_args):
        return ""

    def _commands(self, cmd_args):
        """
            Send available commands
            @syntax commands
            @param args as str
            @return msg as str
        """
        msg = "command: add\ncommand: addid\ncommand: clear\
\ncommand: channels\ncommand: count\ncommand: currentsong\
\ncommand: delete\ncommand: deleteid\ncommand: idle\ncommand: noidle\
\ncommand: list\ncommand: listallinfo\ncommand: listplaylists\ncommand: lsinfo\
\ncommand: next\ncommand: outputs\ncommand: pause\ncommand: play\
\ncommand: playid\ncommand: playlistinfo\ncommand: plchanges\
\ncommand: plchangesposid\ncommand: prev\ncommand:find\ncommand:findadd\
\ncommand: replay_gain_status\
\ncommand: repeat\ncommand: seek\ncommand: seekid\ncommand: search\
\ncommand: setvol\ncommand: stats\ncommand: status\ncommand: sticker\
\ncommand: stop\ncommand: tagtypes\ncommand: update\ncommand: urlhandlers\n"
        return msg

    def _count(self, cmd_args):
        """
            Send lollypop current song
            @syntax count tag
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        # Search for filters
        i = 0
        artist = artist_id = year = album = genre = genre_id = None
        while i < len(args) - 1:
            if args[i].lower() == 'album':
                album = args[i+1]
            elif args[i].lower() == 'artist':
                artist = format_artist_name(args[i+1])
            elif args[i].lower() == 'genre':
                genre = args[i+1]
            elif args[i].lower() == 'date':
                date = args[i+1]
            i += 2

        # Artist have albums with different dates so
        # we do not want None in year
        if artist_id is not None or album is not None:
            try:
                year = int(date)
            except:
                year = None
        else:
            year = Type.NONE

        if genre is not None:
            genre_id = Lp().genres.get_id(genre)
        if artist is not None:
            artist_id = Lp().artists.get_id(artist)

        (songs, playtime) = self._mpddb.count(album, artist_id,
                                              genre_id, year)
        msg = "songs: %s\nplaytime: %s\n" % (songs, playtime)
        return msg

    def _currentsong(self, cmd_args):
        """
            Send lollypop current song
            @syntax currentsong
            @param args as str

            @return msg as str
        """
        if Lp().player.current_track.id is not None:
            msg = self._string_for_track_id(Lp().player.current_track.id)
        else:
            msg = ""
        return msg

    def _delete(self, cmd_args):
        """
            Delete track from playlist
            @syntax delete position
            @param args as str
            @return msg as str
        """
        arg = self._get_args(cmd_args)[0]
        # Check for a range
        try:
            splited = arg.split(':')
            start = int(splited[0])
            end = int(splited[1])
        except:
            start = end = int(arg)
        tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
        tracks = []
        for i in range(start, end):
            track_id = tracks_ids[i]
            tracks.append(Track(track_id))
        Lp().playlists.remove_tracks(Type.MPD, tracks, False)
        return ""

    def _deleteid(self, cmd_args):
        """
            Delete track from playlist
            @syntax delete track_id
            @param args as str

            @return msg as str
        """
        arg = self._get_args(cmd_args)
        Lp().playlists.remove_tracks(Type.MPD, Track(int(arg[0])), False)
        return ""

    def _find(self, cmd_args):
        """
            find tracks
            @syntax find filter value
            @param args as str

            @return msg as str
        """
        msg = ""
        for track_id in self._find_tracks(cmd_args):
            msg += self._string_for_track_id(track_id)
        return msg

    def _findadd(self, cmd_args):
        """
            Find tracks and add them to playlist
            @syntax findadd filter value
            @param args as str
            @return msg as str
        """
        tracks = []
        for track_id in self._find_tracks(cmd_args):
            tracks.append(Track(track_id))
        if tracks:
            Lp().playlists.add_tracks(Type.MPD, tracks, False)
        return ""

    def _idle(self, cmd_args):
        """
            Idle waiting for changes
            @syntax idle type (type not implemented here)
            @param args as str
            @return msg as str
        """
        msg = ''
        args = self._get_args(cmd_args)
        if args:
            self._idle_wanted_strings = []
            for arg in args:
                self._idle_wanted_strings.append(arg)
        else:
            self._idle_wanted_strings = ["stored_playlist",
                                         "player", "playlist"]
        self.request.settimeout(0)
        self.server.event.clear()
        # We handle notifications directly if something in queue
        if not self._idle_strings:
            self.server.event.wait()
        # Handle new notifications
        for string in self._idle_strings:
            msg += "changed: %s\n" % string
        self._idle_strings = []
        self.request.settimeout(10)
        return msg

    def _noidle(self, cmd_args):
        """
            Stop idle
            @syntax noidle
            @param args as str
            @return msg as str
        """
        self._idle_strings = []
        self.server.event.set()
        return ""

    def _list(self, cmd_args):
        """
            List objects
            @syntax list what [filter value...] or list album artist_name
            @param args as str
            @return msg as str
        """
        msg = ""
        args = self._get_args(cmd_args)
        # Search for filters
        if len(args) == 2:
            i = 0
        else:
            i = 1
        artist = artist_id = None
        album = None
        genre = genre_id = None
        date = ''
        while i < len(args) - 1:
            print(args[i], i)
            if args[i].lower() == 'album':
                if i % 2:
                    album = args[i+1]
                else:
                    artist = format_artist_name(args[i+1])
            elif args[i].lower() == 'artist' or\
                    args[i].lower() == 'albumartist':
                artist = format_artist_name(args[i+1])
            elif args[i].lower() == 'genre':
                genre = args[i+1]
            elif args[i].lower() == 'date':
                date = args[i+1]
            i += 2

        try:
            year = int(date)
        except:
            year = Type.NONE

        if genre is not None:
            genre_id = Lp().genres.get_id(genre)
        if artist is not None:
            artist_id = Lp().artists.get_id(artist)

        if args[0].lower() == 'file':
            for path in self._mpddb.get_tracks_paths(album, artist_id,
                                                     genre_id, year):
                msg += "File: "+path+"\n"
        if args[0].lower() == 'album':
            print(artist_id)
            for album in self._mpddb.get_albums_names(artist_id,
                                                      genre_id, year):
                msg += "Album: "+album+"\n"
        elif args[0].lower() == 'artist':
            for artist in self._mpddb.get_artists_names(genre_id):
                msg += "Artist: "+translate_artist_name(artist)+"\n"
        elif args[0].lower() == 'genre':
            results = Lp().genres.get_names()
            for name in results:
                msg += "Genre: "+name+"\n"
        elif args[0].lower() == 'date':
            for year in self._mpddb.get_albums_years(album, artist_id,
                                                     genre_id):
                msg += "Date: "+str(year)+"\n"
        return msg

    def _listall(self, cmd_args):
        """
            List all tracks
            @syntax listall
            @param args as str
            @return msg as str
        """
        return ""

    def _listallinfo(self, cmd_args):
        """
            List all tracks
            @syntax listallinfo
            @param args as str
            @return msg as str
        """
        i = 0
        msg = ""
        for (path, artist, album, album_artist,
             title, date, genre, time,
             track_id, pos) in self._mpddb.listallinfos():
            msg += "file: %s\nArtist: %s\nAlbum: %s\nAlbumArtist: %s\
\nTitle: %s\nDate: %s\nGenre: %s\nTime: %s\nId: %s\nPos: %s\nTrack: %s\n" % (
                                        path,
                                        translate_artist_name(artist),
                                        album,
                                        translate_artist_name(album_artist),
                                        title,
                                        date,
                                        genre,
                                        time,
                                        track_id,
                                        pos,
                                        pos)
            if i > 1000:
                self.request.send(msg.encode("utf-8"))
                msg = ""
                i = 0
            else:
                i += 1
        return msg

    def _listplaylistinfo(self, cmd_args):
        """
            List playlist informations
            @syntax listplaylistinfo name
            @param args as str
            @return msg as str
        """
        arg = self._get_args(cmd_args)[0]
        playlist_id = Lp().playlists.get_id(arg)
        msg = ""
        for track_id in Lp().playlists.get_tracks_ids(playlist_id):
            msg += self._string_for_track_id(track_id)
        return msg

    def _listplaylists(self, cmd_args):
        """
            Send available playlists
            @syntax listplaylists
            @param args as str
            @return msg as str
        """
        dt = datetime.utcnow()
        dt = dt.replace(microsecond=0)
        msg = ""
        for (playlist_id, name) in Lp().playlists.get():
            msg += "playlist: %s\nLast-Modified: %s\n" % (
                                                      name,
                                                      '%sZ' % dt.isoformat())
        return msg

    def _load(self, cmd_args):
        """
            Load playlist
            @syntax load name
            @param args as str
            @return msg as str
        """
        arg = self._get_args(cmd_args)[0]
        playlist_id = Lp().playlists.get_id(arg)
        tracks = []
        tracks_ids = Lp().playlists.get_tracks_ids(playlist_id)
        for track_id in tracks_ids:
            tracks.append(Track(track_id))
        Lp().playlists.add_tracks(Type.MPD, tracks, False)
        Lp().player.set_user_playlist_id(Type.MPD)
        GLib.idle_add(Lp().player.load_in_playlist, tracks_ids[0])
        return ""

    def _lsinfo(self, cmd_args):
        """
            List directories and files
            @syntax lsinfo path
            @param args as str
            @return msg as str
        """
        msg = ""
        args = self._get_args(cmd_args)
        if not args:
            arg = ""
        else:
            arg = args[0]
            if arg == "/":
                arg = ""
        results = []
        root = ""
        if arg == "":
            for path in Lp().settings.get_music_paths():
                results.append((True, path.replace("/", "_")))
        else:
            splited = arg.split("/")
            root = None
            for path in Lp().settings.get_music_paths():
                if path.replace("/", "_") in [arg, splited[0]]:
                    root = path + "/" + "/".join(splited[1:])
                    break
            if root is not None:
                for entry in os.listdir(root):
                    if os.path.isdir(root+"/"+entry):
                        results.append((True, entry))
                    else:
                        results.append((False, entry))
        i = 0
        for (d, path) in results:
            relative = path.replace(root, '', 1)
            if d:
                if arg == "":
                    msg += "directory: %s\n" % relative
                else:
                    msg += "directory: %s/%s\n" % (arg, relative)
            elif Lp().tracks.get_id_by_path(root+"/"+relative) is not None:
                msg += "file: %s/%s\n" % (arg, relative)
            if i > 100:
                self.request.send(msg.encode("utf-8"))
                msg = ""
                i = 0
            i += 1
        return msg

    def _next(self, cmd_args):
        """
            Send output
            @syntax next
            @param args as str
            @return msg as str
        """
        # Make sure we have a playlist loaded in player
        if not Lp().player.is_party():
            if Lp().player.get_user_playlist_id() != Type.MPD or not\
               Lp().player.get_user_playlist():
                Lp().player.set_user_playlist_id(Type.MPD)
            tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
            if tracks_ids and Lp().player.current_track.id not in tracks_ids:
                GLib.idle_add(Lp().player.load_in_playlist, tracks_ids[0])
                return ""
        GLib.idle_add(Lp().player.next)
        return ""

    def _move(self, cmd_args):
        """
            Move range in playlist
            @syntax move position destination
            @param args as str
            @return msg as str
        """
        # TODO implement range
        tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
        arg = self._get_args(cmd_args)
        orig = int(arg[0])
        dst = int(arg[1])
        if orig != dst:
            track_id = tracks_ids[orig]
            del tracks_ids[orig]
            tracks_ids.insert(dst, track_id)
            Lp().playlists.clear(Type.MPD, False)
            tracks = []
            for track_id in tracks_ids:
                tracks.append(Track(track_id))
            Lp().player.set_user_playlist_id(Type.NONE)
            Lp().playlists.add_tracks(Type.MPD, tracks, False)
        return ""

    def _moveid(self, cmd_args):
        """
            Move id in playlist
            @syntax move track_id destination
            @param args as str
            @return msg as str
        """
        try:
            tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
            arg = self._get_args(cmd_args)
            track_id = int(arg[0])
            orig = tracks_ids.index(track_id)
            dst = int(arg[1])
            del tracks_ids[orig]
            tracks_ids.insert(dst, track_id)

            Lp().playlists.clear(Type.MPD)
            tracks = []
            for track_id in tracks_ids:
                tracks.append(Track(track_id))
            Lp().player.set_user_playlist_id(Type.NONE)
            Lp().playlists.add_tracks(Type.MPD, tracks, False)
        except:
            pass
        return ""

    def _outputs(self, cmd_args):
        """
            Send output
            @syntax outputs
            @param args as str
            @return msg as str
        """
        msg = "outputid: 0\noutputname: null\noutputenabled: 1\n"
        return msg

    def _pause(self, cmd_args):
        """
            Pause track
            @syntax pause [1|0]
            @param args as str
            @return msg as str
        """
        print("debut")
        try:
            args = self._get_args(cmd_args)
            if args[0] == "0":
                GLib.idle_add(Lp().player.play)
            else:
                GLib.idle_add(Lp().player.pause)
        except:
            GLib.idle_add(Lp().player.play_pause)
        print('fin')
        return ""

    def _play(self, cmd_args):
        """
            Play track
            @syntax play [position|-1]
            @param args as str
            @return msg as str
        """
        if Lp().player.is_party():
            # Force player to not load albums
            Lp().player.current_track.id = None
            GLib.idle_add(Lp().player.set_party, False)
        # Make sure we have a playlist loaded in player
        if Lp().player.get_user_playlist_id() != Type.MPD or\
           not Lp().player.get_user_playlist():
            Lp().player.set_user_playlist_id(Type.MPD)
        try:
            arg = int(self._get_args(cmd_args)[0])
            currents = Lp().player.get_user_playlist()
            if currents:
                track = currents[arg]
                GLib.idle_add(Lp().player.load_in_playlist, track.id)
        except:
            arg = -1
        if Lp().player.get_status() == Gst.State.PAUSED:
            GLib.idle_add(Lp().player.play)
        elif Lp().player.get_status() == Gst.State.NULL:
            if Lp().player.current_track.id is not None:
                GLib.idle_add(Lp().player.play)
            else:
                currents = Lp().player.get_user_playlist()
                if currents:
                    track = currents[0]
                    GLib.idle_add(Lp().player.load_in_playlist, track.id)
        return ""

    def _playid(self, cmd_args):
        """
            Play track
            @syntax play [track_id|-1]
            @param args as str
            @return msg as str
        """
        if Lp().player.is_party():
            # Force player to not load albums
            Lp().player.current_track.id = None
            GLib.idle_add(Lp().player.set_party, False)
        # Make sure we have a playlist loaded in player
        if Lp().player.get_user_playlist_id() != Type.MPD or\
           not Lp().player.get_user_playlist():
            Lp().player.set_user_playlist_id(Type.MPD)
        try:
            arg = int(self._get_args(cmd_args)[0])
            GLib.idle_add(Lp().player.load_in_playlist, arg)
        except:
            if Lp().player.get_status() == Gst.State.PAUSED:
                GLib.idle_add(Lp().player.play)
            elif Lp().player.get_status() == Gst.State.NULL:
                if Lp().player.current_track.id is not None:
                    GLib.idle_add(Lp().player.play)
                else:
                    currents = Lp().player.get_user_playlist()
                    if currents:
                        track = currents[0]
                        GLib.idle_add(Lp().player.load_in_playlist, track.id)
        return ""

    def _playlistadd(self, cmd_args):
        """
            Add a new playlist
            @syntax playlistadd name
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        playlist_id = Lp().playlists.get_id(args[0])
        tracks = []
        if not Lp().playlists.exists(playlist_id):
            Lp().playlists.add(args[0])
            playlist_id = Lp().playlists.get_id(args[0])
        for arg in args[1:]:
            track_id = Lp().tracks.get_id_by_path(arg)
            tracks.append(Track(track_id))
        if tracks:
            Lp().playlists.add_tracks(playlist_id, tracks, False)
        return ""

    def _playlistid(self, cmd_args):
        """
            Send informations about current playlist
            @param playlistid
            @param args as str
            @return msg as str
        """
        msg = ""
        try:
            track_id = int(self._get_args(cmd_args))
            msg += self._string_for_track_id(track_id)
        except:
            tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
            if Lp().player.current_track.id is not None and\
               Lp().player.current_track.id not in tracks_ids and\
               Lp().player.is_party():
                tracks_ids.insert(0, Lp().player.current_track.id)
            for track_id in tracks_ids:
                msg += self._string_for_track_id(track_id)
        return msg

    def _playlistinfo(self, cmd_args):
        """
            Send informations about current playlist
            @parma playlistinfo
            @param args as str
            @return msg as str
        """
        msg = ""
        tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
        if Lp().player.current_track.id is not None and\
           Lp().player.current_track.id not in tracks_ids and\
           Lp().player.is_party():
            tracks_ids.insert(0, Lp().player.current_track.id)
        for track_id in tracks_ids:
            msg += self._string_for_track_id(track_id)
        return msg

    def _plchanges(self, cmd_args):
        """
            Send informations about playlists
            @syntax plchanges version
            @param args as str
            @return msg as str
        """
        msg = ""
        version = int(self._get_args(cmd_args)[0])
        i = 0
        try:
            for track_id in self._playlist[version]:
                msg += self._string_for_track_id(track_id)
                if i > 100:
                    self.request.send(msg.encode("utf-8"))
                    msg = ""
                    i = 0
                else:
                    i += 1
        except:
            print("No such version")
        return msg

    def _plchangesposid(self, cmd_args):
        """
            Send informations about playlists
            @param plchangesposid version
            @param args as str
            @return msg as str
        """
        i = 0
        msg = ""
        version = int(self._get_args(cmd_args)[0])
        pl = Lp().playlists.get_tracks_ids(Type.MPD)
        for track_id in self._playlist[version]:
            msg += "cpos: %s\nId: %s\n" % (pl.index(track_id), track_id)
            if i > 100:
                self.request.send(msg.encode("utf-8"))
                msg = ""
                i = 0
            else:
                i += 1
        return msg

    def _previous(self, cmd_args):
        """
            Send output
            @syntax previous
            @param args as str
            @return msg as str
        """
        # Make sure we have a playlist loaded in player
        if not Lp().player.is_party():
            if Lp().player.get_user_playlist_id() != Type.MPD or not\
               Lp().player.get_user_playlist():
                Lp().player.set_user_playlist_id(Type.MPD)
            tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
            if tracks_ids and Lp().player.current_track.id not in tracks_ids:
                GLib.idle_add(Lp().player.load_in_playlist, tracks_ids[0])
                return ""
        GLib.idle_add(Lp().player.prev)
        return ""

    def _random(self, cmd_args):
        """
            Set player random, as MPD can't handle all lollypop random modes,
            set party mode
            @syntax random [1|0]
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        GLib.idle_add(Lp().player.set_party, bool(int(args[0])))
        return ""

    def _replay_gain_status(self, cmd_args):
        """
            Send output
            @syntax replay_gain_status
            @param args as str
            @return msg as str
        """
        msg = "replay_gain_mode: on\n"
        return msg

    def _repeat(self, cmd_args):
        """
            Ignore
            @param args as str
            @return msg as str
        """
        return ""

    def _seek(self, cmd_args):
        """
           Seek current
           @syntax seek position
           @param args as str
           @return msg as str
        """
        args = self._get_args(cmd_args)
        seek = int(args[1])
        GLib.idle_add(Lp().player.seek, seek)
        return ""

    def _seekid(self, cmd_args):
        """
            Seek track id
            @syntax seekid track_id position
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        track_id = int(args[0])
        seek = int(args[1])
        if track_id == Lp().player.current_track.id:
            GLib.idle_add(Lp().player.seek, seek)
        return ""

    def _search(self, cmd_args):
        """
            Send stats about db
            @syntax search what value
            @param args as str
            @return msg as str
        """
        msg = ""
        args = self._get_args(cmd_args)
        # Search for filters
        i = 0
        artist = artist_id = None
        album = None
        genre = genre_id = None
        date = ''
        while i < len(args) - 1:
            if args[i].lower() == 'album':
                album = args[i+1]
            elif args[i].lower() == 'artist' or\
                    args[i].lower() == 'albumartist':
                artist = format_artist_name(args[i+1])
            elif args[i].lower() == 'genre':
                genre = args[i+1]
            elif args[i].lower() == 'date':
                date = args[i+1]
            i += 2

        try:
            year = int(date)
        except:
            year = Type.NONE

        if genre is not None:
            genre_id = Lp().genres.get_id(genre)
        if artist is not None:
            artist_id = Lp().artists.get_id(artist)

        for track_id in self._mpddb.get_tracks_ids(album, artist_id,
                                                   genre_id, year):
            msg += self._string_for_track_id(track_id)
        return msg

    def _setvol(self, cmd_args):
        """
            Send stats about db
            @syntax setvol value
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        vol = float(args[0])
        Lp().player.set_volume(vol/100)
        return ""

    def _stats(self, cmd_args):
        """
            Send stats about db
            @syntax stats
            @param args as str
            @return msg as str
        """
        artists = Lp().artists.count()
        albums = Lp().albums.count()
        tracks = Lp().tracks.count()
        msg = "artists: %s\nalbums: %s\nsongs: %s\nuptime: 0\
\nplaytime: 0\ndb_playtime: 0\ndb_update: %s\n" % \
            (artists, albums, tracks,
             Lp().settings.get_value('db-mtime').get_int32())
        return msg

    def _status(self, cmd_args):
        """
            Send lollypop status
            @syntax status
            @param args as str
            @return msg as str
        """
        msg = "volume: %s\nrepeat: %s\nrandom: %s\
\nsingle: %s\nconsume: %s\nplaylist: %s\
\nplaylistlength: %s\nstate: %s\
\nbitrate: 0\naudio: 44100:24:2\nmixrampdb: 0.000000\n" % (
                                   int(Lp().player.get_volume()*100),
                                   1,
                                   int(Lp().player.is_party()),
                                   1,
                                   1,
                                   self._playlist_version,
                                   len(Lp().playlists.get_tracks(Type.MPD)),
                                   self._get_status(),
                                   )
        if self._get_status() != 'stop':
            elapsed = Lp().player.get_position_in_track() / 1000000 / 60
            time = Lp().player.current_track.duration
            songid = Lp().player.current_track.id
            msg += "song: %s\nsongid: %s\ntime: %s:%s\nelapsed: %s\n" % (
                                       Lp().playlists.get_position(
                                            Type.MPD,
                                            Lp().player.current_track.id),
                                       songid,
                                       int(elapsed),
                                       time,
                                       int(elapsed))
        return msg

    def _sticker(self, cmd_args):
        """
            Send stickers
            @syntax sticker [get|set] song rating
            @param args as str
            @return msg as str
        """
        args = self._get_args(cmd_args)
        msg = ""
        if args[0].find("get song ") != -1 and\
                args[2].find("rating") != -1:
            track_id = Lp().tracks.get_id_by_path(args[1])
            track = Track(track_id)
            msg = "sticker: rating=%s\n" % int(track.get_popularity()*2)
        elif args[0].find("set song") != -1 and\
                args[2].find("rating") != -1:
            track_id = Lp().tracks.get_id_by_path(args[1])
            track = Track(track_id)
            track.set_popularity(int(args[3])/2)
        return msg

    def _stop(self, cmd_args):
        """
            Stop player
            @syntax stop
            @param args as str
            @return msg as str
        """
        GLib.idle_add(Lp().player.stop)
        return ""

    def _tagtypes(self, cmd_args):
        """
            Send available tags
            @syntax tagtypes
            @param args as str
            @return msg as str
        """
        msg = "tagtype: Artist\ntagtype: Album\ntagtype: Title\
\ntagtype: Track\ntagtype: Name\ntagtype: Genre\ntagtype: Date\
\ntagtype: Performer\ntagtype: Disc\n"
        return msg

    def _update(self, cmd_args):
        """
            Update database
            @syntax update
            @param args as str
            @return msg as str
        """
        Lp().window.update_db()
        return ""

    def _urlhandlers(self, cmd_args):
        """
            Send url handlers
            @syntax urlhandlers
            @param args as str
            @return msg as str
        """
        msg = "handler: http\n"
        return msg

    def _string_for_track_id(self, track_id):
        """
            Get mpd protocol string for track id
            @param track id as int
            @return str
        """
        if track_id is None:
            msg = ""
        else:
            track = Track(track_id)
            msg = "file: %s\nArtist: %s\nAlbum: %s\nAlbumArtist: %s\
\nTitle: %s\nDate: %s\nGenre: %s\nTime: %s\nId: %s\nPos: %s\nTrack: %s\n" % (
                     track.path,
                     track.artist,
                     track.album.name,
                     track.album_artist,
                     track.name,
                     track.album.year,
                     track.genre,
                     track.duration,
                     track.id,
                     track.position,
                     track.position)
        return msg

    def _get_status(self):
        """
            Player status
            @return str
        """
        state = Lp().player.get_status()
        if state == Gst.State.PLAYING:
            return 'play'
        elif state == Gst.State.PAUSED:
            return 'pause'
        else:
            return 'stop'

    def _get_args(self, args):
        """
            Get args from string
            @param args as str
            @return args as [str]
        """
        splited = args.split('"')
        ret = []
        for arg in splited:
            if len(arg.replace(' ', '')) == 0:
                continue
            ret.append(arg)
        return ret

    def _find_tracks(self, cmd_args):
        """
            find tracks
            @syntax find filter value
            @param args as str

        """
        tracks = []
        args = self._get_args(cmd_args)
        # Search for filters
        i = 0
        track_position = None
        artist = artist_id = None
        album = None
        genre = genre_id = None
        date = ''
        while i < len(args) - 1:
            if args[i].lower() == 'album':
                album = args[i+1]
            elif args[i].lower() == 'artist' or\
                    args[i].lower() == 'albumartist':
                artist = format_artist_name(args[i+1])
            elif args[i].lower() == 'genre':
                genre = args[i+1]
            elif args[i].lower() == 'date':
                date = args[i+1]
            elif args[i].lower() == 'track':
                track_position = args[i+1]
            i += 2

        try:
            year = int(date)
        except:
            year = Type.NONE

        if genre is not None:
            genre_id = Lp().genres.get_id(genre)
        if artist is not None:
            artist_id = Lp().artists.get_id(artist)

        # We search for tracks and filter on position
        for track_id in self._mpddb.get_tracks_ids(album, artist_id,
                                                   genre_id, year):
            track_id_position = None
            if track_position is not None:
                track_id_position = Lp().tracks.get_position(track_id)
            if track_id_position == track_position:
                tracks.append(track_id)
        return tracks

    def _on_player_changed(self, player, data=None):
        """
            Add player to idle
            @param player as Player
        """
        if "player" in self._idle_wanted_strings:
            self._idle_strings.append("player")
            tracks_ids = Lp().playlists.get_tracks_ids(Type.MPD)
            if Lp().player.current_track.id not in tracks_ids:
                self._idle_strings.append("playlist")
            self.server.event.set()

    def _on_position_changed(self, player, data=None):
        """
            Add player to idle
            @param player as Player
        """
        # Player may be in pause so wait for playback
        if player.get_status() == Gst.State.PAUSED:
            GLib.idle_add(self._on_position_changed, player, data)
        elif "player" in self._idle_wanted_strings:
            self._idle_strings.append("player")
            self.server.event.set()

    def _on_playlist_changed(self, playlists, playlist_id):
        """
            Add playlist to idle if mpd
            @param playlists as Playlists
            @param playlist id as int
        """
        if playlist_id == Type.MPD:
            if "playlist" in self._idle_wanted_strings:
                try:
                    previous = self._playlist[self._playlist_version-1]
                except:
                    previous = []
                i = 0
                self._playlist[self._playlist_version] = []
                for track_id in Lp().playlists.get_tracks_ids(Type.MPD):
                    if track_id not in previous or previous[i] != track_id:
                        self._playlist[self._playlist_version].append(track_id)
                self._playlist_version += 1
                self._idle_strings.append("playlist")
                self.server.event.set()
        elif "stored_playlist" in self._idle_wanted_strings:
            self._idle_strings.append("stored_playlist")
            self.server.event.set()

    def __connect(self, connect=True):
        """
            Connect or disconnect signals
        """
        if connect:
            self._signal1 = Lp().player.connect('current-changed',
                                                self._on_player_changed)
            self._signal2 = Lp().player.connect('status-changed',
                                                self._on_player_changed)
            self._signal3 = Lp().player.connect('seeked',
                                                self._on_position_changed)
            self._signal4 = Lp().playlists.connect('playlist-changed',
                                                   self._on_playlist_changed)
        else:
            Lp().player.disconnect(self._signal1)
            Lp().player.disconnect(self._signal2)
            Lp().player.disconnect(self._signal3)
            Lp().playlists.disconnect(self._signal4)


class MpdServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """
        Create a MPD server.
    """

    def __init__(self, eth, port=6600):
        """
            Init server
            @param eth as string
            @param port as int
        """
        self.event = None
        try:
            socketserver.TCPServer.allow_reuse_address = True
            # Get ip for interface
            ip = ""
            if eth != "":
                ip = get_ip(eth)
            socketserver.TCPServer.__init__(self, (ip, port), MpdHandler)
        except Exception as e:
            print("MpdServer::__init__(): %s" % e)

    def run(self, e):
        """
            Run MPD server in a blocking way.
            @param e as threading.Event
        """
        try:
            self.event = e
            self.serve_forever()
        except Exception as e:
            print("MpdServer::run(): %s" % e)


class MpdServerDaemon(MpdServer):
    """
        Create a deamonized MPD server
        @param eth as string
        @param port as int
    """
    def __init__(self, eth="", port=6600):
        """
            Init daemon
        """
        MpdServer.__init__(self, eth, port)
        self.running = True
        event = threading.Event()
        self.thread = threading.Thread(target=self.run,
                                       args=(event,))
        self.thread.setDaemon(True)
        self.thread.start()

    def quit(self):
        """
            Stop MPD server deamon
        """
        self.running = False
        self.shutdown()
        self.server_close()