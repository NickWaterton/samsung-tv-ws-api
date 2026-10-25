#!/usr/bin/env python3
# NOTE old api is 2021 and earlier Frame TV's, new api is 2022+ Frame TV's
# example program to put TV back into art mode if it switches to playing
# demonstrates use of a class, running as a task, use of signals etc

import asyncio
import logging
import argparse
from pathlib import Path
from signal import SIGTERM, SIGINT

from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv.async_remote import SamsungTVWSAsyncRemote
from samsungframetv.remote import SendRemoteKey
from samsungframetv import exceptions, __version__

logging.basicConfig(level=logging.DEBUG)

class EnsureArtMode:
    
    def __init__(self,ip, token_file, period):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.debug = self.log.getEffectiveLevel() <= logging.DEBUG
        self.ip = ip
        self.token_file = Path(token_file)
        self.period = period
        self.exit = False
        self.task = None
        self.add_signals()
        self.log.info('opening art websocket with token')
        self.tv_art = SamsungTVAsyncArt(host=self.ip, port=8002, token_file=self.token_file)
        self.tv = SamsungTVWSAsyncRemote(host=self.ip, port=8002, token_file=self.token_file)
        
    def close(self):
        '''
        exit program
        '''
        self.log.info('SIGINT/SIGTERM received, exiting')
        self.exit=True
        if self.task:
            self.task.cancel()
        
    def add_signals(self):
        '''
        setup signals to exit program
        '''
        try:    #might not work on windows
            asyncio.get_running_loop().add_signal_handler(SIGINT, self.close)
            asyncio.get_running_loop().add_signal_handler(SIGTERM, self.close)
        except Exception:
            self.log.warning('signal error')
        
    async def run_forever(self):
        '''
        Start monitoring art mode
        '''
        await self.tv.start_listening()
        await self.tv_art.start_listening()
        # this is just an example, really could just await self.ensure_artmode()
        #await self.ensure_artmode()
        # or example of running as a task
        self.task = asyncio.create_task(self.ensure_artmode())
        await self.do_other_things()
            
    async def do_other_things(self):
        #loop forever
        while not self.exit:
            # do other things here - but don't exit or the program ends
            await asyncio.sleep(1)
        self.close()
        
    async def ensure_artmode(self):
        '''
        check is TV is on, and in art mode, if not, switch back to art mode (send KEY_POWER)
        If tv is off, do nothing
        Close websockets on exit
        '''
        try:
            while not self.exit:
                try:
                    #is tv on (calls tv rest api)
                    tv_on = await self.tv_art.on()
                    self.log.info('tv is on: {}'.format(tv_on))
                    
                    if tv_on:
                        #is art mode on
                        art_mode = await self.tv_art.get_artmode()                  #calls websocket command to determine status
                        self.log.info('art mode is: {}'.format(art_mode))
                        
                        #is tv on and in art mode (alternative)
                        #art_mode = await self.tv_art.in_artmode()                   #calls rest api and websocket command to determine status
                        #self.info('TV is in art mode: {}'.format(art_mode))
                
                        if art_mode != "on":
                            # Turn off
                            self.log.info('Turning TV off - to art mode')
                            await self.tv.send_command(SendRemoteKey.click("KEY_POWER"))
                        
                except exceptions.ResponseError as e:
                    self.log.warning('ERROR: {}'.format(e))
                except AssertionError as e:
                    self.log.warning('no data received: {}'.format(e))
                    
                await asyncio.sleep(self.period)
                
        except asyncio.exceptions.CancelledError:
            self.log.info('cancelled')
        except Exception as e:
            self.log.exception(e)
        await self.tv.close()
        await self.tv_art.close()


def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Example async ensure art mode Samsung Frame TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-p','--period', action="store", type=int, default=15, help='How often to check in seconds (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()

async def main():
    args = parseargs()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        force=True,
                        level=logging.DEBUG if args.debug else logging.INFO)
    log = logging.getLogger('Main')
    log.info('Program Started, version: {}'.format(__version__))
    log.debug('Debug mode')
       
    artmode = EnsureArtMode( args.ip,
                             token_file = args.token_file,
                             period     = args.period,)
    
    await artmode.run_forever()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
