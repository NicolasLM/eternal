import asyncio
import json
import sys

import urwid

from airc import IRCClientProtocol
from ui import channels, frame, palette, protocols


async def init(irc_connection_config: dict):
    loop = asyncio.get_running_loop()

    transport, protocol = await loop.create_connection(
        lambda: IRCClientProtocol(loop, irc_connection_config),
        irc_connection_config['server'],
        irc_connection_config['port'],
        ssl=irc_connection_config['ssl']
    )
    protocols.append(protocol)
    consume_messages_task = loop.create_task(channels.consume_messages())


def main():
    import logging
    logging.basicConfig(filename='/tmp/irc.log', level=logging.DEBUG)
    with open(sys.argv[1]) as f:
        irc_connection_config = json.load(f)

    # Get a reference to the event loop as we plan to use
    # low-level APIs.
    loop = asyncio.get_event_loop()

    urwid_main_loop = urwid.MainLoop(
        frame,
        palette,
        event_loop=urwid.AsyncioEventLoop(loop=loop)
    )
    loop.create_task(init(irc_connection_config))

    # Wait until the protocol signals that the connection
    # is lost and close the transport.
    urwid_main_loop.run()


if __name__ == '__main__':
    main()
