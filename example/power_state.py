#!/usr/bin/env python3
'''
Print the TV's power state ('off', 'standby', 'tv', 'art' or 'on') using REST, the art channel
and (optionally) Samsung IP Control.

First run with --ipcontrol: the TV must be on and NOT in art mode, press Allow on the TV.

usage: python power_state.py 192.168.100.32 [--ipcontrol]
'''
import argparse
import logging
import sys

sys.path.append('../')

from samsungframetv import SamsungTVWS
from samsungframetv.ipcontrol import SamsungTVIPControl


def main():
    parser = argparse.ArgumentParser(description='Samsung TV power state')
    parser.add_argument('ip', help='TV ip address')
    parser.add_argument('-t', '--token_file', default='token_file.txt', help='websocket token file')
    parser.add_argument('--ipcontrol', action='store_true', help='also use IP Control (port 1516)')
    parser.add_argument('-D', '--debug', action='store_true', help='debug logging')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    ipc = None
    if args.ipcontrol:
        ipc = SamsungTVIPControl(args.ip, token_file='ipcontrol_token_{}.txt'.format(args.ip))
        if not ipc.token:
            logging.info('pairing IP Control, press Allow on the TV')
            ipc.pair()

    tv = SamsungTVWS(args.ip, port=8002, token_file=args.token_file)
    logging.info('power state: {}'.format(tv.art().get_power_state(ipc)))


if __name__ == '__main__':
    main()
