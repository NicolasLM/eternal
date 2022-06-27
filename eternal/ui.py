import hashlib
import re
from datetime import datetime
from itertools import islice
from typing import Callable, List, Optional, Tuple, Union

import urwid
import urwid_readline

from . import libirc

palette = [
    ("Bold", "default,bold", "default", "bold"),
    ("Black", "black", "default"),
    ("Dark red", "dark red", "default"),
    ("Dark green", "dark green", "default"),
    ("Brown", "brown", "default"),
    ("Dark blue", "dark blue", "default"),
    ("Dark magenta", "dark magenta", "default"),
    ("Dark cyan", "dark cyan", "default"),
    ("Light gray", "light gray", "default"),
    ("Dark gray", "dark gray", "default"),
    ("Light red", "light red", "default"),
    ("Light green", "light green", "default"),
    ("Yellow", "yellow", "default"),
    ("Light blue", "light blue", "default"),
    ("Light magenta", "light magenta", "default"),
    ("Light cyan", "light cyan", "default"),
    ("White", "white", "default"),
]


def get_local_date(aware_utc_datetime: datetime) -> str:
    return aware_utc_datetime.astimezone(tz=None).strftime("%Y-%m-%d")


def get_local_time(aware_utc_datetime: datetime) -> str:
    return aware_utc_datetime.astimezone(tz=None).strftime("%H:%M")


def fit(string: str, max_length: int):
    if len(string) <= max_length:
        return string

    return string[: max_length - 1] + "…"


def nick_color(nick: str) -> str:
    colors = [
        "Black",
        "Dark red",
        "Dark green",
        "Brown",
        "Dark blue",
        "Dark magenta",
        "Dark cyan",
        "Light gray",
        "Dark gray",
        "Light red",
        "Light green",
        "Yellow",
        "Light blue",
        "Light magenta",
        "Light cyan",
        "White",
    ]
    index = int(hashlib.md5(nick.encode()).hexdigest(), 16) % (len(colors))
    return colors[index]


class Buffer:
    def __init__(self, name: str, irc: libirc.IRCClient):
        self.name = name
        self.irc = irc

        self._main_content = urwid.ListBox(urwid.SimpleFocusListWalker([]))

        self.has_unread = False
        self.has_notification = False
        self.is_client_default = False

    def append(self, text):
        self._main_content.body.append(text)
        if self.is_scrolled_fully():
            try:
                self._main_content.set_focus(
                    self._main_content.body.positions(reverse=True)[0]
                )
            except IndexError:
                pass

    def is_scrolled_fully(self) -> bool:
        try:
            return (
                self._main_content.focus_position + 1
                == self._main_content.body.positions(reverse=True)[0]
            )
        except IndexError:
            # Both lists are probably empty
            return True

    def render(self):
        pass


class ServerBuffer(Buffer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.widget = self._main_content


class ChannelBuffer(Buffer):

    #: Maximum numbers of members to show typing at the same time.
    NUM_TYPING_LIMIT: int = 6

    #: Maximum numbers of members to display in the members list column.
    NUM_MEMBERS_LIMIT: int = 128

    #: Size of the right column containing the members list.
    MEMBERS_COLUMN_WIDTH: int = 20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.members_pile = urwid.Pile([])
        self.members_updated = False
        self.status_line = urwid.Text("")

        self.widget = urwid.Columns(
            [
                urwid.Frame(
                    body=self._main_content,
                    footer=self.status_line,
                ),
                (
                    self.MEMBERS_COLUMN_WIDTH,
                    urwid.LineBox(urwid.Filler(self.members_pile, valign="top")),
                ),
            ]
        )

    def get_members_pile_contents(self) -> list:
        members_pile_widget = list()
        try:
            members = self.irc.channels[self.name].members.values()
            modes = self.irc.channels[self.name].modes
        except KeyError:
            members = []
            modes = ""

        members = self.irc.sort_members_by_prefix(members)

        header_str = str(len(members))
        if modes:
            header_str = f"{modes} - {header_str}"
        members_pile_widget = [(urwid.Text(header_str, align="right"), ("pack", None))]
        members_pile_widget.extend(
            [
                (
                    urwid.Text(
                        (
                            nick_color(m.user.source.nick),
                            m.highest_prefix + m.user.source.nick,
                        ),
                        align="right" if m.user.is_away else "left",
                    ),
                    ("pack", None),
                )
                for m in islice(members, self.NUM_MEMBERS_LIMIT)
            ]
        )

        return members_pile_widget

    def get_status_line_content(self) -> Union[str, List]:
        try:
            members = self.irc.channels[self.name].members.values()
        except KeyError:
            return ""

        nicks = [m.user.source.nick for m in members if m.is_typing]
        nicks = [n for n in nicks if n != self.irc.nick]
        nicks = [(nick_color(nick), nick) for nick in sorted(nicks)]
        num_typing = len(nicks)
        if num_typing == 0:
            return ""

        if num_typing == 1:
            return [nicks[0], " is typing..."]

        rv = []
        for i, nick in enumerate(nicks):
            rv.append(nick)
            if i + 1 == self.NUM_TYPING_LIMIT:
                num_others = num_typing - self.NUM_TYPING_LIMIT
                if num_others:
                    rv.append(f" and {num_others} others")
                break
            elif i + 2 == num_typing:
                rv.append(" and ")
            elif i + 1 == num_typing:
                pass
            else:
                rv.append(", ")

        rv.append(" are typing")
        return rv

    def render(self):
        if self.members_updated:
            self.members_pile.contents = self.get_members_pile_contents()
            self.members_updated = False
        self.status_line.set_text(self.get_status_line_content())


class UI(urwid.Frame):

    BUFFERS_COLUMN_WIDTH = 20

    def __init__(self):
        # List of open buffers.
        self._buffers: List[Buffer] = []

        # Content of the left column of the UI displaying
        # open buffers.
        self.buffers_pile = urwid.Pile([])

        # Index of the selected buffer.
        self._current = 0

        # When set, contains a callable that updates the urwid
        # screen. It is necessary to call it explicitly each time
        # widgets are modified as a result of an event external to
        # urwid. For example changing the active buffer does not
        # require drawing the screen explicitly because it's done
        # as a result of a keypress managed by urwid.
        # However adding a received IRC message to the current buffer
        # requires calling it because the event is external to urwid.
        self._draw_screen_soon: Optional[Callable] = None

        self._buffer_frame = urwid.Frame(body=urwid.SolidFill())
        self._columns = urwid.Columns(
            [
                (
                    self.BUFFERS_COLUMN_WIDTH,
                    urwid.LineBox(urwid.Filler(self.buffers_pile, valign="top")),
                ),
                self._buffer_frame,
            ]
        )
        command_input = CommandEdit(self, ("Bold", "Command "))
        super().__init__(body=self._columns, footer=command_input, focus_part="footer")

    def set_draw_screen_soon(self, draw_screen_soon: Callable):
        self._draw_screen_soon = draw_screen_soon
        draw_screen_soon()

    def keypress(self, size, key):

        if key == "ctrl p":
            self.select_previous()
            return

        if key == "ctrl n":
            self.select_next()
            return

        if key == "ctrl o":
            self.move_up()
            return

        if key == "ctrl b":
            self.move_down()
            return

        if key in ("page up", "page down", "home", "end", "up", "down"):
            return self.get_body().keypress(size, key)

        return super().keypress(size, key)

    async def add_irc_client(self, irc: libirc.IRCClient):
        buffer = ServerBuffer(irc.name, irc)
        buffer.is_client_default = True
        self.add_buffer(buffer)
        await self._consume_messages(irc)

    def _update_pile(self):
        pile_widgets = list()
        for index, buffer in enumerate(self._buffers):

            if index > 0 and buffer.is_client_default:
                pile_widgets.append((urwid.Text(""), ("pack", None)))

            if buffer.is_client_default:
                buffer.name = buffer.irc.name
                text = buffer.name
            else:
                text = f" {buffer.name}"

            text = fit(text, self.BUFFERS_COLUMN_WIDTH - 2)

            if index == self._current:
                widget = urwid.Text(("White", text))
            elif buffer.has_notification:
                widget = urwid.Text(("Yellow", text))
            elif buffer.has_unread:
                widget = urwid.Text(("Dark green", text))
            else:
                widget = urwid.Text(text)

            pile_widgets.append((widget, ("pack", None)))

        self.buffers_pile.contents = pile_widgets

    def add_buffer(self, buffer: Buffer):
        # Find the position after the last buffer of the same client
        insert_at = len(self._buffers)
        for i, c in enumerate(self._buffers):
            if c.irc is buffer.irc:
                insert_at = i + 1

        if self._current > insert_at:
            self._current += 1

        self._buffers.insert(insert_at, buffer)
        if len(self._buffers) == 1:
            self.select_buffer_by_index(0)
        else:
            self._update_pile()

    def remove_buffer(self, buffer: Buffer):
        i = self._buffers.index(buffer)
        if self._current >= i:
            self._current -= 1
        self._buffers.pop(i)
        self.select_buffer_by_index(self._current)

    def _update_content(self):
        # TODO: remove all that?
        buffer = self._buffers[self._current]
        # self._buffer_frame = buffer.widget
        self._update_pile()

    def select_buffer_by_index(self, index):
        """Select a buffer by its index on the list."""
        if index < 0:
            raise IndexError("Cannot select buffer smaller than 0")

        # Just raise an exception is it does not exist
        buffer = self._buffers[index]

        self._current = index
        buffer.has_unread = False
        buffer.has_notification = False
        buffer.render()
        self._update_pile()
        self._buffer_frame.body = buffer.widget

    def select_previous(self):
        """Select previous buffer."""
        try:
            self.select_buffer_by_index(self._current - 1)
        except IndexError:
            pass

    def select_next(self):
        """Select next buffer."""
        try:
            self.select_buffer_by_index(self._current + 1)
        except IndexError:
            pass

    def move_up(self):
        """Move a buffer up the list."""
        i = self._current
        if i == 0:
            return

        try:
            current_c = self._buffers[i]
            previous_c = self._buffers[i - 1]
        except IndexError:
            return

        if current_c.irc is not previous_c.irc:
            return

        if current_c.is_client_default or previous_c.is_client_default:
            return

        self._buffers[i], self._buffers[i - 1] = (
            self._buffers[i - 1],
            self._buffers[i],
        )
        self._current -= 1
        self._update_pile()

    def move_down(self):
        """Move a buffer down the list."""
        i = self._current
        if i == len(self._buffers) - 1:
            return

        try:
            current_c = self._buffers[i]
            next_c = self._buffers[i + 1]
        except IndexError:
            return

        if current_c.irc is not next_c.irc:
            return

        if current_c.is_client_default or next_c.is_client_default:
            return

        self._buffers[i], self._buffers[i + 1] = (
            self._buffers[i + 1],
            self._buffers[i],
        )
        self._current += 1
        self._update_pile()

    def _get_buffer_by_name(self, irc: libirc.IRCClient, name: Optional[str]) -> Buffer:
        for buffer in self._buffers:
            if buffer.irc is not irc:
                continue

            if buffer.name == name:
                return buffer

            if name is None and buffer.is_client_default:
                return buffer

        # Create buffer if it doesn't exist
        buffer = ChannelBuffer(name, irc)
        self.add_buffer(buffer)
        return buffer

    def get_current_buffer(self) -> Buffer:
        return self._buffers[self._current]

    def _channel_member_update(
        self,
        msg: libirc.Message,
        time: str,
        irc: libirc.IRCClient,
        texts: list,
        always_show=False,
    ) -> ChannelBuffer:
        channel = self._get_buffer_by_name(irc, msg.channel)
        assert isinstance(channel, ChannelBuffer)

        channel.members_updated = True
        channel.render()
        if msg.user.is_recently_active or always_show:
            channel.append(
                urwid.Text(
                    [
                        ("Light gray", f"{time} "),
                        (nick_color(str(msg.source)), str(msg.source)),
                    ]
                    + texts
                )
            )
            self._update_content()
        return channel

    async def _consume_messages(self, irc: libirc.IRCClient):
        while True:
            msg = await irc.inbox.get()

            if isinstance(msg, libirc.ConnectionClosedEvent):
                raise urwid.ExitMainLoop()

            time = get_local_time(msg.time)

            if isinstance(msg, libirc.ChannelJoinedEvent):
                self._channel_member_update(msg, time, irc, [f" joined {msg.channel}"])

            elif isinstance(msg, libirc.ChannelPartEvent):
                channel = self._channel_member_update(
                    msg, time, irc, [f" left {msg.channel}"]
                )
                if msg.channel not in irc.channels:
                    self.remove_buffer(channel)

            elif isinstance(msg, libirc.ChannelKickEvent):
                self._channel_member_update(
                    msg,
                    time,
                    irc,
                    [
                        " kicked ",
                        (nick_color(str(msg.kicked_nick)), str(msg.kicked_nick)),
                        ": ",
                        msg.reason,
                    ],
                    always_show=True,
                )

            elif isinstance(msg, libirc.NickChangedEvent):
                self._channel_member_update(
                    msg,
                    time,
                    irc,
                    [
                        " is now known as ",
                        (nick_color(str(msg.new_nick)), str(msg.new_nick)),
                    ],
                )

            elif isinstance(msg, libirc.QuitEvent):
                self._channel_member_update(msg, time, irc, [f" quit: {msg.reason}"])

            elif isinstance(msg, libirc.GoneAwayEvent):
                self._channel_member_update(
                    msg, time, irc, [f" has gone away: {msg.away_message}"]
                )

            elif isinstance(msg, libirc.BackFromAwayEvent):
                self._channel_member_update(msg, time, irc, [f" is back"])

            elif isinstance(
                msg, (libirc.NewMessageEvent, libirc.NewActionMessageEvent)
            ):
                if msg.channel == "*":
                    buffer = self._get_buffer_by_name(irc, None)
                else:
                    buffer = self._get_buffer_by_name(irc, msg.channel)
                if irc.nick in msg.message:
                    buffer.has_notification = True
                buffer.has_unread = True
                if isinstance(msg, libirc.NewActionMessageEvent):
                    line = urwid.Text(
                        [
                            ("Light gray", f"{time} "),
                            (nick_color(str(msg.source)), str(msg.source)),
                            ("Bold", f" {msg.message} "),
                        ]
                    )
                else:
                    line = urwid.Text(
                        [
                            ("Light gray", f"{time} "),
                            (nick_color(str(msg.source)), str(msg.source)),
                            ": ",
                            *convert_formatting(msg.message),
                        ]
                    )
                buffer.append(line)
                self._update_content()

            elif isinstance(msg, libirc.ChannelTopicEvent):
                buffer = self._get_buffer_by_name(irc, msg.channel)
                buffer.append(urwid.Text(*convert_formatting(msg.topic)))
                self._update_content()

            elif isinstance(msg, libirc.ChannelTopicWhoTimeEvent):
                buffer = self._get_buffer_by_name(irc, msg.channel)
                buffer.append(
                    urwid.Text(
                        [
                            "Set by ",
                            (nick_color(str(msg.set_by)), str(msg.set_by)),
                            f" on {get_local_date(msg.set_at)}",
                        ]
                    )
                )
                self._update_content()

            elif isinstance(msg, libirc.ChannelNamesEvent):
                buffer = self._get_buffer_by_name(irc, msg.channel)
                buffer.members_updated = True
                buffer.render()
                self._update_content()

            elif isinstance(msg, libirc.ChannelModeEvent):
                buffer = self._get_buffer_by_name(irc, msg.channel)
                buffer.members_updated = True
                buffer.render()

            elif isinstance(msg, libirc.ChannelTypingEvent):
                buffer = self._get_buffer_by_name(irc, msg.channel)
                buffer.render()

            elif isinstance(msg, libirc.NewMessageFromServerEvent):
                buffer = self._get_buffer_by_name(irc, None)
                buffer.append(
                    urwid.Text(
                        [("Light gray", f"{time} "), *convert_formatting(msg.message)]
                    )
                )
                self._update_content()

            else:
                buffer = self._get_buffer_by_name(irc, None)
                buffer.append(urwid.Text(msg.command + " " + " ".join(msg.params)))
                self._update_content()

            if self._draw_screen_soon is not None:
                self._draw_screen_soon()

            irc.inbox.task_done()


class CommandEdit(urwid_readline.ReadlineEdit):
    def __init__(self, ui: UI, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ui = ui
        self.enable_autocomplete(self._auto_complete)

    def keypress(self, size, key):
        if key != "enter":
            rv = super().keypress(size, key)
            self._handle_typing_notification()
            return rv

        buffer = self.ui.get_current_buffer()
        command = self.get_edit_text()
        if command == "":
            # Don't send empty messages
            return

        elif command == "/close":
            self.ui.remove_buffer(buffer)
        elif command == "/part":
            buffer.irc.send_to_server(f"PART {buffer.name}")
        elif command.startswith("/msg "):
            irc = buffer.irc
            _, target, content = command.split(" ", maxsplit=2)
            irc.send_to_server(f"PRIVMSG {target} :{content}")

            if "echo-message" not in irc.capabilities:
                buffer = self.ui._get_buffer_by_name(irc, target)
                time = get_local_time(libirc.get_utc_now())
                source = irc.nick
                buffer.append(
                    urwid.Text(
                        [
                            ("Light gray", f"{time} "),
                            (nick_color(str(source)), str(source)),
                            f": {content}",
                        ]
                    )
                )
                self.ui._update_content()

        elif command.startswith("/me "):
            _, message = command.split(" ", maxsplit=1)
            buffer.irc.send_to_server(
                f"PRIVMSG {buffer.name} :\x01ACTION {message}\x01"
            )

            if "echo-message" not in buffer.irc.capabilities:
                time = get_local_time(libirc.get_utc_now())
                source = buffer.irc.nick
                buffer.append(
                    urwid.Text(
                        [
                            ("Light gray", f"{time} "),
                            (nick_color(str(source)), str(source)),
                            ("Bold", f" {message} "),
                        ]
                    )
                )
                self.ui._update_content()

        elif command.startswith("/"):
            buffer.irc.send_to_server(command[1:])
        else:
            buffer.irc.send_to_server(f"PRIVMSG {buffer.name} :{command}")

            if "echo-message" not in buffer.irc.capabilities:
                time = get_local_time(libirc.get_utc_now())
                source = buffer.irc.nick
                buffer.append(
                    urwid.Text(
                        [
                            ("Light gray", f"{time} "),
                            (nick_color(str(source)), str(source)),
                            f": {command}",
                        ]
                    )
                )
                self.ui._update_content()

        self.set_edit_text("")

    def _handle_typing_notification(self):
        try:
            buffer = self.ui.get_current_buffer()
        except IndexError:
            return

        command = self.get_edit_text()
        if command.startswith("/") or command == "":
            buffer.irc.notify_typing_done(buffer.name)
        else:
            buffer.irc.notify_typing_active(buffer.name)

    def _auto_complete(self, text, state):
        buffer = self.ui.get_current_buffer()
        try:
            candidates = buffer.irc.channels[buffer.name].members.keys()
        except KeyError:
            candidates = list()
        tmp = (
            [c + ", " for c in candidates if c and c.startswith(text)]
            if text
            else candidates
        )
        try:
            return tmp[state]
        except (IndexError, TypeError):
            return None


TOGGLE_FORMATTERS = {
    "\x02": "bold",
    "\x1D": "italics",
    "\x1E": "strikethrough",
    "\x1F": "underline",
    "\x16": "standout",
}
COLOR = "\x03"
RESET = "\x0F"
FORMATTERS = list(TOGGLE_FORMATTERS.keys()) + [COLOR, RESET]
COLOR_REGEX = re.compile(r"^(\d{1,2})(,(\d{1,2}))?")
IRC_TO_URWID_COLORS = {
    0: "white",
    1: "black",
    2: "dark blue",
    3: "dark green",
    4: "dark red",
    5: "brown",
    6: "dark magenta",
    7: "light red",
    8: "yellow",
    9: "light green",
    10: "dark cyan",
    11: "light cyan",
    12: "light blue",
    13: "light magenta",
    14: "dark gray",
    15: "light gray",
}


def convert_formatting(irc_string: str) -> List[Tuple[urwid.AttrSpec, str]]:
    rv = list()

    current_format: List[str] = []
    current_fg_color = ""
    current_bg_color = ""
    current_format_used = False
    current_text_start_idx = 0
    i = 0
    skip_next = 0

    def _toggle(formatter: str):
        try:
            current_format.remove(formatter)
        except ValueError:
            current_format.append(formatter)

    def _finish_substring(end: int):
        if current_fg_color:
            to_join = [current_fg_color] + current_format
        else:
            to_join = current_format
        fg = ",".join(to_join)
        rv.append(
            (
                urwid.AttrSpec(fg, current_bg_color),
                irc_string[current_text_start_idx:end],
            )
        )

    def _process_color() -> Tuple[str, str, int]:
        try:
            match = COLOR_REGEX.match(irc_string[i + 1 :])
        except IndexError:
            return "", "", 0

        if not match:
            return "", "", 0

        fg, middle, bg = match.groups()
        middle = middle or ""
        bg = bg or ""
        return fg, bg, len(fg) + len(middle)

    for i, s in enumerate(irc_string):
        if skip_next:
            skip_next -= 1
            continue

        if not current_format_used:
            current_text_start_idx = i

        if s not in FORMATTERS:
            current_format_used = True
            continue

        # Current char is a format code

        # Finish the previous substring
        if current_format_used:
            _finish_substring(end=i)

        current_format_used = False
        if s == RESET:
            current_format = []
            current_fg_color = ""
            current_bg_color = ""
        elif s in TOGGLE_FORMATTERS.keys():
            _toggle(TOGGLE_FORMATTERS[s])
        elif s == COLOR:
            fg, bg, skip_next = _process_color()
            try:
                current_fg_color = IRC_TO_URWID_COLORS[int(fg)]
            except ValueError:
                current_fg_color = ""
            except KeyError:
                current_fg_color = "h" + fg  # This is not the right color

            try:
                current_bg_color = IRC_TO_URWID_COLORS[int(bg)]
            except ValueError:
                current_bg_color = ""
            except KeyError:
                current_bg_color = "h" + bg

        else:
            raise Exception("Unreachable")

    if current_format_used:
        _finish_substring(end=i + 1)

    return rv
