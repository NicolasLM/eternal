import asyncio
import json
import sys

import urwid

from .airc import IRCClientProtocol
from .ui import UI, palette


async def init(irc_connection_config: dict, ui: UI):
    loop = asyncio.get_running_loop()

    transport, protocol = await loop.create_connection(
        lambda: IRCClientProtocol(loop, irc_connection_config),
        irc_connection_config['server'],
        irc_connection_config['port'],
        ssl=irc_connection_config['ssl']
    )
    await ui.add_connection(protocol)


def main():
    import logging
    logging.basicConfig(filename='/tmp/irc.log', level=logging.DEBUG)

    loop = asyncio.get_event_loop()

    ui = UI()

    urwid_main_loop = urwid.MainLoop(
        ui.frame,
        palette,
        event_loop=urwid.AsyncioEventLoop(loop=loop)
    )

    for config_file_name in sys.argv[1:]:
        with open(config_file_name) as f:
            irc_connection_config = json.load(f)
        loop.create_task(init(irc_connection_config, ui))

    urwid_main_loop.run()


if __name__ == '__main__':
    main()
