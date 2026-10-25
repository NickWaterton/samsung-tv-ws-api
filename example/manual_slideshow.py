#!/usr/bin/env python3
import argparse
import logging

from samsungframetv import SamsungTVWS, exceptions, __version__

def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Example art Samsung Frame TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()
    
class Slideshow:
    input_chars = ['n', 'p']
    
    def __init__(self, args):
        self.log = logging.getLogger('Main.'+__class__.__name__)
        # Normal constructor (will ask for connection every time on 2024 TV's)
        self.tv = SamsungTVWS(host=args.ip, port=8002)
        
        # Autosave token to file
        #self.tv = SamsungTVWS(host=args.ip, port=8002, token_file=args.token_file)
        if self.get_tv_content():
            self.input_loop()
        
    def input_loop(self):
        '''
        gets next character from keyboard
        n = next, p = previous, q = quit
        '''
        char = None
        while char != 'q' :
            char = input('n,p,q >> ').lower()
            if char in self.input_chars:
                if not self.advance_frame_image(char):
                    break
        
    def get_tv_content(self, category='MY-C0002'):
        '''
        gets content_id list of category - either My Photos (MY-C0002) or Favourites (MY-C0004) from tv
        use 10 second timeout in case tv has a lot of content (tv.art(10))
        gets content_id for current art
        and current index
        '''
        try:
            self.sequence = [v['content_id'] for v in self.tv.art(10).available(category)]
            self.current = self.tv.art().get_current()['content_id']
            self.index = self.sequence.index(self.current)
            return True
        except (AssertionError, ValueError) as e:
            self.log.warning('failed to get contents from TV {}'.format(e))
        return False
        
    def advance_frame_image(self, char='n'):
        '''
        Next or previous image in the sequence
        '''
        try:
            new_index = self.index + (1 if char == 'n' else -1)
            # wrap around
            new_index = new_index % len(self.sequence)
            self.log.debug('current index: {} new index: {} max: {}'.format(self.index, new_index, len(self.sequence)-1))
            next_id = self.sequence[new_index]
            # Select new image
            self.tv.art().select_image(next_id)
            self.log.info(f"Advanced from {self.sequence[self.index]} to {next_id}")
            self.index = new_index
            return True
        except Exception as e:
            self.log.error(f"Error changing image: {e}")
        return False

def main():
    args = parseargs()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        level=logging.DEBUG if args.debug else logging.INFO)
    logging.debug('debug mode')
    Slideshow(args)


if __name__ == '__main__':
    main()