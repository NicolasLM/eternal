from datetime import datetime
import hashlib
from typing import List, Tuple, Optional

import urwid
import urwid_readline

import libirc


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


def get_local_time(aware_utc_datetime: datetime) -> str:
    return aware_utc_datetime.astimezone(tz=None).strftime('%H:%M')


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

    def __init__(self, name: str, content: List[urwid.Text]):
        self.name = name
        self.list_walker = urwid.SimpleFocusListWalker(content)
        self.members = set()


class UI:

    def __init__(self):
        self._current = 0
        self._channels: List[Channel] = []
        self.chat_content = urwid.ListBox(urwid.SimpleFocusListWalker([]))
        self.pile = urwid.Pile([])
        self.members_pile = urwid.Pile([])

        self.add_channel(Channel('server', []))

        columns = urwid.Columns([
            (20, urwid.LineBox(urwid.Filler(self.pile, valign='top'))),
            self.chat_content,
            (20, urwid.LineBox(urwid.Filler(self.members_pile, valign='top'))),
        ])
        command_input = CommandEdit(self, ('Bold', "Command "))
        self.frame = MyFrame(self, body=columns, footer=command_input, focus_part='footer')

        self.protocol = None

    def _update_pile(self):
        pile_widgets = list()
        for index, channel in enumerate(self._channels):
            if index == self._current:
                widget = urwid.Text(('Bold', channel.name))
            else:
                widget = urwid.Text(channel.name)

            pile_widgets.append((widget, ('pack', None)))

        self.pile.contents = pile_widgets

    def process_changed_nick(self, old: str, new: Optional[str], line: urwid.Text):
        for channel in self._channels:
            try:
                channel.members.remove(old)
            except KeyError:
                pass
            else:
                channel.list_walker.append(line)
                if new:
                    channel.members.add(new)

        self._update_content()
        self._render_members()

    def _render_members(self):
        pile_widgets = list()
        for member in self.get_current_channel().members:
            widget = urwid.Text((nick_color(member), member))
            pile_widgets.append((widget, ('pack', None)))

        self.members_pile.contents = pile_widgets

    def add_channel(self, channel: Channel):
        self._channels.append(channel)
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
        channel_list_walker = self._channels[self._current].list_walker
        self.chat_content.body = channel_list_walker
        try:
            self.chat_content.set_focus(channel_list_walker.positions(True)[0])
        except IndexError:
            pass

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
        if self._current == 0:
            return

        i = self._current
        self._channels[i], self._channels[i-1] = self._channels[i-1], self._channels[i]
        self._current -= 1
        self._update_pile()

    def move_down(self):
        """Move a channel down the list."""
        if self._current == len(self._channels) - 1:
            return

        i = self._current
        self._channels[i], self._channels[i + 1] = self._channels[i + 1], self._channels[i]
        self._current += 1
        self._update_pile()

    def _get_channel_by_name(self, name: str) -> Tuple[int, Channel]:
        i = 0
        for i, channel in enumerate(self._channels):
            if channel.name == name:
                return i, channel

        # Create channel if it doesn't exist
        channel = Channel(name, [])
        self.add_channel(channel)
        return i + 1, channel

    def get_current_channel(self) -> Channel:
        return self._channels[self._current]

    async def consume_messages(self):
        queue = self.protocol.inbox
        while True:
            msg = await queue.get()

            if isinstance(msg, libirc.ConnectionClosedEvent):
                raise urwid.ExitMainLoop()

            time = get_local_time(msg.time)

            if isinstance(msg, libirc.ChannelJoinedEvent):
                _, channel = self._get_channel_by_name(msg.channel)
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), f' joined {msg.channel}']))
                channel.members = set(self.protocol.irc.channels[msg.channel].members.keys())
                self._update_content()
                self._render_members()

            elif isinstance(msg, libirc.ChannelPartEvent):
                _, channel = self._get_channel_by_name(msg.channel)
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), f' left {msg.channel}']))
                try:
                    channel.members = set(self.protocol.irc.channels[msg.channel].members.keys())
                except KeyError:
                    self.remove_channel(channel)
                self._update_content()
                self._render_members()

            elif isinstance(msg, libirc.NickChangedEvent):
                line = urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), ' is now known as ', (nick_color(str(msg.new_nick)), str(msg.new_nick))])
                self.process_changed_nick(str(msg.source), msg.new_nick, line)

            elif isinstance(msg, libirc.QuitEvent):
                line = urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), f' quit: {msg.reason}'])
                self.process_changed_nick(str(msg.source), None, line)

            elif isinstance(msg, libirc.NewMessageEvent):
                if msg.channel == '*':
                    _, channel = self._get_channel_by_name('server')
                else:
                    _, channel = self._get_channel_by_name(msg.channel)
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(msg.source)), str(msg.source)), f': {msg.message}']))
                self._update_content()

            elif isinstance(msg, libirc.ChannelTopicEvent):
                _, channel = self._get_channel_by_name(msg.channel)
                channel.list_walker.append(urwid.Text(msg.topic))
                self._update_content()

            elif isinstance(msg, libirc.ChannelNamesEvent):
                _, channel = self._get_channel_by_name(msg.channel)
                channel.members = set(self.protocol.irc.channels[msg.channel].members.keys())
                self._render_members()
                self._update_content()

            else:
                self._channels[0].list_walker.append(urwid.Text(str(msg)))
                self._update_content()

            queue.task_done()


class CommandEdit(urwid_readline.ReadlineEdit):

    def __init__(self, ui: UI, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ui = ui
        self.enable_autocomplete(self._auto_complete)

    def keypress(self, size, key):
        if key != 'enter':
            return super().keypress(size, key)

        command = self.get_edit_text()
        if command == '':
            # Don't send empty messages
            return

        elif command == '/close':
            self.ui.remove_channel(self.ui.get_current_channel())
        elif command == '/part':
            self.ui.protocol.send_to_server(f'PART {self.ui.get_current_channel().name}')
        elif command.startswith('/msg'):
            _, channel_name, content = command.split(' ', maxsplit=2)
            self.ui.protocol.send_to_server(f'PRIVMSG {channel_name} :{content}')

            if 'echo-message' not in self.ui.protocol.irc.capabilities:
                _, channel = self.ui._get_channel_by_name(channel_name)
                time = get_local_time(libirc.get_utc_now())
                source = self.ui.protocol.irc.nick
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(source)), str(source)), f': {content}']))
                self.ui._update_content()

        elif command.startswith('/'):
            self.ui.protocol.send_to_server(command[1:])
        else:
            channel = self.ui.get_current_channel()
            self.ui.protocol.send_to_server(f'PRIVMSG {channel.name} :{command}')

            if 'echo-message' not in self.ui.protocol.irc.capabilities:
                time = get_local_time(libirc.get_utc_now())
                source = self.ui.protocol.irc.nick
                channel.list_walker.append(urwid.Text([('Light gray', f'{time} '), (nick_color(str(source)), str(source)), f': {command}']))
                self.ui._update_content()

        self.set_edit_text('')

    def _auto_complete(self, text, state):
        candidates = self.ui.get_current_channel().members
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
