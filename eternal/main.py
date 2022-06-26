import asyncio
import json
import sys
from logging import getLogger

import urwid

from .airc import IRCClientProtocol
from .libirc import IRCClient
from .ui import UI, palette

logger = getLogger(__name__)


async def init(irc_connection_config: dict, ui: UI):
    loop = asyncio.get_running_loop()

    #: Queue where parsed messages received from the IRC server are placed
    inbox = asyncio.Queue()

    irc_client = IRCClient(irc_connection_config, inbox)
    transport, protocol = await loop.create_connection(
        lambda: IRCClientProtocol(loop, irc_connection_config, irc_client),
        irc_connection_config["server"],
        irc_connection_config["port"],
        ssl=irc_connection_config["ssl"],
    )
    irc_client.notify_connection_established(protocol)
    await ui.add_irc_client(irc_client)


def main():
    import logging

    logging.basicConfig(filename="/tmp/irc.log", level=logging.DEBUG)

    loop = asyncio.get_event_loop()

    ui = UI()

    urwid_main_loop = urwid.MainLoop(
        ui.frame, palette, event_loop=urwid.AsyncioEventLoop(loop=loop)
    )

    for config_file_name in sys.argv[1:]:
        with open(config_file_name) as f:
            irc_connection_config = json.load(f)
        loop.create_task(init(irc_connection_config, ui))

    try:
        urwid_main_loop.run()
    except Exception:
        logger.exception("Unexpected error")
        raise
    else:
        logger.info("Terminating eternal")


if __name__ == "__main__":
    main()
