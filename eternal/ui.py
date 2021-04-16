from datetime import datetime
from itertools import islice
import hashlib
import re
from typing import List, Optional, Tuple

import urwid
import urwid_readline

from . import libirc, airc


palette = [
    ('Bold', 'default,bold', 'default', 'bold'),
    ('Black', 'black', 'default'),
    ('Dark red', 'dark red', 'default'),
    ('Dark green', 'dark green', 'default'),
    ('Brown', 'brown', 'default'),
    ('Dark blue', 'dark blue', 'default'),
    ('Dark magenta', 'dark magenta', 'default'),
    ('Dark cyan', 'dark cyan', 'default'),
    ('Light gray', 'light gray', 'default'),
    ('Dark gray', 'dark gray', 'default'),
    ('Light red', 'light red', 'default'),
    ('Light green', 'light green', 'default'),
    ('Yellow', 'yellow', 'default'),
    ('Light blue', 'light blue', 'default'),
    ('Light magenta', 'light magenta', 'default'),
    ('Light cyan', 'light cyan', 'default'),
    ('White', 'white', 'default')
]


def get_local_date(aware_utc_datetime: datetime) -> str:
    return aware_utc_datetime.astimezone(tz=None).strftime('%Y-%m-%d')


def get_local_time(aware_utc_datetime: datetime) -> str:
    return aware_utc_datetime.astimezone(tz=None).strftime('%H:%M')


def fit(string: str, max_length: int):
    if len(string) <= max_length:
        return string

    return string[:max_length - 1] + '…'


def nick_color(nick: str) -> str:
    colors = [
        'Black',
        'Dark red',
        'Dark green',
        'Brown',
        'Dark blue',
        'Dark magenta',
        'Dark cyan',
        'Light gray',
        'Dark gray',
        'Light red',
        'Light green',
        'Yellow',
        'Light blue',
        'Light magenta',
        'Light cyan',
        'White'
    ]
    index = int(hashlib.md5(nick.encode()).hexdigest(), 16) % (len(colors))
    return colors[index]


class Channel:

    def __init__(self, name: str, connection: airc.IRCClientProtocol):
        self.name = name
        self.connection = connection
        self.list_walker = urwid.SimpleFocusListWalker([])
        self.members_updated = False
        self.has_unread = False
        self.has_notification = False
        self.is_connection_default = False
        self._members_pile_widget = list()

    def get_members_pile_widgets(self) -> list:
        if self.members_updated:
            self._members_pile_widget = list()
            try:
                members = self.connection.irc.channels[self.name].members.values()
            except KeyError:
                members = []

            members = self.connection.irc.sort_members_by_prefix(members)

            self._members_pile_widget = [
                (urwid.Text(str(len(members)), align='right'), ('pack', None))
            ]
            self._members_pile_widget.extend([
                (urwid.Text((nick_color(m.user.source.nick), m.prefixes + m.user.source.nick)), ('pack', None))
                for m in islice(members, 64)
            ])
            self.members_updated = False

        return self._members_pile_widget


class UI:

    COLUMN_WIDTH = 20

    def __init__(self):
        self._current = 0
        self._channels: List[Channel] = []
        self.chat_content = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.pile = urwid.Pile([])
        self.members_pile = urwid.Pile([])

        columns = urwid.Columns([
            (self.COLUMN_WIDTH, urwid.LineBox(urwid.Filler(self.pile, valign='top'))),
            self.chat_content,
            (self.COLUMN_WIDTH, urwid.LineBox(urwid.Filler(self.members_pile, valign='top'))),
        ])
        command_input = CommandEdit(self, ('Bold', "Command "))
        self.frame = MyFrame(self, body=columns, footer=command_input, focus_part='footer')

    async def add_connection(self, connection: airc.IRCClientProtocol):
        channel = Channel(connection.irc.name, connection)
        channel.is_connection_default = True
        self.add_channel(channel)
        await self._consume_messages(connection)

    def _update_pile(self):
        pile_widgets = list()
        for index, channel in enumerate(self._channels):

            if index > 0 and channel.is_connection_default:
                pile_widgets.append((urwid.Text(''), ('pack', None)))

            if channel.is_connection_default:
                channel.name = channel.connection.irc.name
                text = channel.name
            else:
                text = f' {channel.name}'

            text = fit(text, self.COLUMN_WIDTH - 2)

            if index == self._current:
                widget = urwid.Text(('White', text))
            elif channel.has_notification:
                widget = urwid.Text(('Yellow', text))
            elif channel.has_unread:
                widget = urwid.Text(('Dark green', text))
            else:
                widget = urwid.Text(text)

            pile_widgets.append((widget, ('pack', None)))

        self.pile.contents = pile_widgets

    def _render_members(self):
        self.members_pile.contents = self.get_current_channel().get_members_pile_widgets()

    def add_channel(self, channel: Channel):
        # Find the position after the last channel of the same connection
        insert_at = len(self._channels)
        for i, c in enumerate(self._channels):
            if c.connection is channel.connection:
                insert_at = i + 1

        if self._current > insert_at:
            self._current += 1

        self._channels.insert(insert_at, channel)
        self._update_pile()
        if len(self._channels) == 1:
            self._update_content()
            self._render_members()

    def remove_channel(self, channel: Channel):
        i = self._channels.index(channel)
        if self._current >= i:
            self._current -= 1
        self._channels.pop(i)
        self._update_pile()
        self._update_content()
        self._render_members()

    def _update_content(self):
        channel = self._channels[self._current]
        channel_list_walker = channel.list_walker
        self.chat_content.body = channel_list_walker
        try:
            self.chat_content.set_focus(channel_list_walker.positions(True)[0])
        except IndexError:
            pass
        channel.has_unread = False
        channel.has_notification = False
        self._update_pile()

    def select_previous(self):
        """Select previous channel."""
        if self._current == 0:
            return

        try:
            self._channels[self._current - 1]
        except IndexError:
            pass
        else:
            self._current -= 1
            self._update_pile()
            self._update_content()
            self._render_members()

    def select_next(self):
        """Select next channel."""
        try:
            self._channels[self._current + 1]
        except IndexError:
            pass
        else:
            self._current += 1
            self._update_pile()
            self._update_content()
            self._render_members()

    def move_up(self):
        """Move a channel up the list."""
        i = self._current
        if i == 0:
            return

        try:
            current_c = self._channels[i]
            previous_c = self._channels[i-1]
        except IndexError:
            return

        if current_c.connection is not previous_c.connection:
            return

        if current_c.is_connection_default or previous_c.is_connection_default:
            return

        self._channels[i], self._channels[i-1] = self._channels[i-1], self._channels[i]
        self._current -= 1
        self._update_pile()

    def move_down(self):
        """Move a channel down the list."""
        i = self._current
        if i == len(self._channels) - 1:
            return

        try:
            current_c = self._channels[i]
            next_c = self._channels[i + 1]
        except IndexError:
            return

        if current_c.connection is not next_c.connection:
            return

        if current_c.is_connection_default or next_c.is_connection_default:
            return

        self._channels[i], self._channels[i + 1] = self._channels[i + 1], self._channels[i]
        self._current += 1
        self._update_pile()

    def _get_channel_by_name(self, connection: airc.IRCClientProtocol, name: Optional[str]) -> Channel:
        for channel in self._channels:
            if channel.connection is not connection:
                continue

            if channel.name == name:
                return channel

            if name is None and channel.is_connection_default:
                return channel

        # Create channel if it doesn't exist
        channel = Channel(name, connection)
        self.add_channel(channel)
        return channel

    def get_current_channel(self) -> Channel:
        return self._channels[self._current]

    def _channel_member_update(self, msg: libirc.Message, time: str,
                               connection: airc.IRCClientProtocol, texts: list) -> Channel:
        channel = self._get_channel_by_name(connection, msg.channel)
        channel.members_updated = True
        self._render_members()
        if msg.user.is_recently_active:
            channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source))] + texts))
            self._update_content()
        return channel

    async def _consume_messages(self, connection: airc.IRCClientProtocol):
        while True:
            msg = await connection.inbox.get()

            if isinstance(msg, libirc.ConnectionClosedEvent):
                raise urwid.ExitMainLoop()

            time = get_local_time(msg.time)

            if isinstance(msg, libirc.ChannelJoinedEvent):
                self._channel_member_update(msg, time, connection, [f' joined {msg.channel}'])

            elif isinstance(msg, libirc.ChannelPartEvent):
                channel = self._channel_member_update(msg, time, connection, [f' left {msg.channel}'])
                if msg.channel not in connection.irc.channels:
                    self.remove_channel(channel)

            elif isinstance(msg, libirc.NickChangedEvent):
                self._channel_member_update(msg, time, connection, [' is now known as ', (nick_color(str(msg.new_nick)), str(msg.new_nick))])

            elif isinstance(msg, libirc.QuitEvent):
                self._channel_member_update(msg, time, connection, [f' quit: {msg.reason}'])

            elif isinstance(msg, libirc.NewMessageEvent):
                if msg.channel == '*':
                    channel = self._get_channel_by_name(connection, None)
                else:
                    channel = self._get_channel_by_name(connection, msg.channel)
                if connection.irc.nick in msg.message:
                    channel.has_notification = True
                channel.has_unread = True
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), ': ', *convert_formatting(msg.message)]))
                self._update_content()

            elif isinstance(msg, libirc.ChannelTopicEvent):
                channel = self._get_channel_by_name(connection, msg.channel)
                channel.list_walker.append(urwid.Text(*convert_formatting(msg.topic)))
                self._update_content()

            elif isinstance(msg, libirc.ChannelTopicWhoTimeEvent):
                channel = self._get_channel_by_name(connection, msg.channel)
                channel.list_walker.append(urwid.Text(['Set by ', (nick_color(str(msg.set_by)), str(msg.set_by)), f' on {get_local_date(msg.set_at)}']))
                self._update_content()

            elif isinstance(msg, libirc.ChannelNamesEvent):
                channel = self._get_channel_by_name(connection, msg.channel)
                channel.members_updated = True
                self._render_members()
                self._update_content()

            elif isinstance(msg, libirc.NewMessageFromServerEvent):
                channel = self._get_channel_by_name(connection, None)
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), *convert_formatting(msg.message)]))
                self._update_content()

            else:
                channel = self._get_channel_by_name(connection, None)
                channel.list_walker.append(urwid.Text(msg.command + ' ' + ' '.join(msg.params)))
                self._update_content()

            connection.inbox.task_done()


class CommandEdit(urwid_readline.ReadlineEdit):

    def __init__(self, ui: UI, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ui = ui
        self.enable_autocomplete(self._auto_complete)

    def keypress(self, size, key):
        if key != 'enter':
            return super().keypress(size, key)

        channel = self.ui.get_current_channel()
        command = self.get_edit_text()
        if command == '':
            # Don't send empty messages
            return

        elif command == '/close':
            self.ui.remove_channel(channel)
        elif command == '/part':
            channel.connection.send_to_server(f'PART {channel.name}')
        elif command.startswith('/msg'):
            connection = channel.connection
            _, channel_name, content = command.split(' ', maxsplit=2)
            connection.send_to_server(f'PRIVMSG {channel_name} :{content}')

            if 'echo-message' not in connection.irc.capabilities:
                channel = self.ui._get_channel_by_name(connection, channel_name)
                time = get_local_time(libirc.get_utc_now())
                source = connection.irc.nick
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(source)), str(source)), f': {content}']))
                self.ui._update_content()

        elif command.startswith('/'):
            channel.connection.send_to_server(command[1:])
        else:
            channel.connection.send_to_server(f'PRIVMSG {channel.name} :{command}')

            if 'echo-message' not in channel.connection.irc.capabilities:
                time = get_local_time(libirc.get_utc_now())
                source = channel.connection.irc.nick
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(source)), str(source)), f': {command}']))
                self.ui._update_content()

        self.set_edit_text('')

    def _auto_complete(self, text, state):
        channel = self.ui.get_current_channel()
        try:
            candidates = channel.connection.irc.channels[channel.name].members.keys()
        except KeyError:
            candidates = list()
        tmp = [c + ', ' for c in candidates if c and c.startswith(text)] if text else candidates
        try:
            return tmp[state]
        except (IndexError, TypeError):
            return None


class MyFrame(urwid.Frame):

    def __init__(self, ui: UI, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ui = ui

    def keypress(self, size, key):

        if key == 'ctrl p':
            self.ui.select_previous()
            return

        if key == 'ctrl n':
            self.ui.select_next()
            return

        if key == 'ctrl o':
            self.ui.move_up()
            return

        if key == 'ctrl b':
            self.ui.move_down()
            return

        if key in ('page up', 'page down', 'home', 'end', 'up', 'down'):
            return self.get_body().keypress(size, key)

        return super().keypress(size, key)


TOGGLE_FORMATTERS = {
    '\x02': 'bold',
    '\x1D': 'italics',
    '\x1E': 'strikethrough',
    '\x1F': 'underline',
    '\x16': 'standout',
}
COLOR = '\x03'
RESET = '\x0F'
FORMATTERS = list(TOGGLE_FORMATTERS.keys()) + [COLOR, RESET]
COLOR_REGEX = re.compile(r'^(\d{1,2})(,(\d{1,2}))?')
IRC_TO_URWID_COLORS = {
    0: 'white',
    1: 'black',
    2: 'dark blue',
    3: 'dark green',
    4: 'dark red',
    5: 'brown',
    6: 'dark magenta',
    7: 'light red',
    8: 'yellow',
    9: 'light green',
    10: 'dark cyan',
    11: 'light cyan',
    12: 'light blue',
    13: 'light magenta',
    14: 'dark gray',
    15: 'light gray'
}


def convert_formatting(irc_string: str) -> List[Tuple[urwid.AttrSpec, str]]:
    rv = list()

    current_format: List[str] = []
    current_fg_color = ''
    current_bg_color = ''
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
        fg = ','.join(to_join)
        rv.append((urwid.AttrSpec(fg, current_bg_color), irc_string[current_text_start_idx:end]))

    def _process_color() -> Tuple[str, str, int]:
        try:
            match = COLOR_REGEX.match(irc_string[i+1:])
        except IndexError:
            return '', '', 0

        if not match:
            return '', '', 0

        fg, middle, bg = match.groups()
        middle = middle or ''
        bg = bg or ''
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
            current_fg_color = ''
            current_bg_color = ''
        elif s in TOGGLE_FORMATTERS.keys():
            _toggle(TOGGLE_FORMATTERS[s])
        elif s == COLOR:
            fg, bg, skip_next = _process_color()
            try:
                current_fg_color = IRC_TO_URWID_COLORS[int(fg)]
            except ValueError:
                current_fg_color = ''
            except KeyError:
                current_fg_color = 'h' + fg  # This is not the right color

            try:
                current_bg_color = IRC_TO_URWID_COLORS[int(bg)]
            except ValueError:
                current_bg_color = ''
            except KeyError:
                current_bg_color = 'h' + bg

        else:
            raise Exception('Unreachable')

    if current_format_used:
        _finish_substring(end=i+1)

    return rv
