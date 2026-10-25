#!/usr/bin/env python3
# fully async example program to change art mats on Frame TV - default is none

import logging
import sys, os
import asyncio
import argparse

from samsungframetv.async_art import SamsungTVAsyncArt
from samsungframetv import __version__
from samsungframetv.exceptions import ResponseError


def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Async Change Mats for art on Samsung TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-m','--mat', action="store", type=str, default='none', help='landscape mat to apply to art (use org to leave unchanged) (default: %(default)s))')
    parser.add_argument('-pm','--pmat', action="store", type=str, default=None, help='portrait mat to apply to art (default: %(default)s))')
    parser.add_argument('-A','--all', action='store_true', default=False, help='Apply to all art - usually just My-Photos (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()
    
def parse_args(mat_types, mat_colors, matte):
    '''
    check mat is valid, and return mat and color
    '''
    if matte is not None:
        if matte not in ['none', 'org']:
            if matte in mat_types:
                mat = matte
                color = None
            elif matte in mat_colors:
                mat = None
                color = matte
            elif '_' in matte:
                mat, color = matte.split('_')
            else:
                mat = color = None

            if (mat not in mat_types) and (color not in mat_colors):
                logging.error(f"Invalid matte type or color: {matte}. Supported matte types are: {mat_types}, colors: {mat_colors}")
                sys.exit(1)
            return mat, color
    return matte, None
    
def get_target_matte(matte, color, art, landscape=True):
    '''
    get new matte setting from mat, color and original art matte setting
    '''
    target_matte_type = 'none'
    org_matte_id = art["matte_id" if landscape else "portrait_matte_id"]
    if matte == 'org' or (matte is None and color is None):
        target_matte_type = org_matte_id
    elif matte != 'none':
        if matte and color:
            target_matte_type = '{}_{}'.format(matte, color)
        else:
            if org_matte_id == 'none':
                logging.warning(f"can't change color/type of {'landscape' if landscape else 'portrait'} mat: {org_matte_id} for {art['content_id']} to {matte or color}")
            else:
                org_mat, org_color = org_matte_id.split('_')
                if color is None:
                    color = org_color
                if matte is None:
                    matte = org_mat
                target_matte_type = '{}_{}'.format(matte, color)
    return target_matte_type
        
async def main():
    args = parseargs()
    global logging
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        level=logging.DEBUG if args.debug else logging.INFO)
    logging.debug('debug mode')
    logging.info('opening art websocket with token')
    token_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), args.token_file)
    tv = SamsungTVAsyncArt(host=args.ip, port=8002, token_file=token_file)

    logging.info('getting tv info')
    #is art mode supported
    supported = await tv.supported()
    logging.info('art mode is supported: {}'.format(supported))
    
    if supported:
        # List available mats for displaying art
        mats = await tv.get_matte_list(include_colour=True)
        mat_types  = [elem['matte_type'] for elem in mats[0]]
        mat_colors = [elem['color'] for elem in mats[1]]
        
        # parse args.mat
        mat, color = parse_args(mat_types, mat_colors, args.mat)
                    
        # parse args.pmat
        pmat, pcolor = parse_args(mat_types, mat_colors, args.pmat) if args.pmat else (None, None)

        # List the art available in My-Photos on the device (or all art if -A selected)
        available_art = await tv.available(None if args.all else 'MY-C0002', timeout=10)
        
        for art in available_art:
            try:
                #set target mat/color combo
                target_matte_type = get_target_matte(mat, color, art, True)
                portrait_target_matte_type = get_target_matte(pmat, pcolor, art, False)
                    
                if art["matte_id"] != target_matte_type or art["portrait_matte_id"] != portrait_target_matte_type:
                    logging.info(
                        f"Setting landscape matte to {target_matte_type}"
                        f"{' and portrait matte to {}'.format(portrait_target_matte_type) if portrait_target_matte_type != art['portrait_matte_id'] else ''} "
                        f"for {art['content_id']}"
                    )
                    try:
                        await tv.change_matte(art["content_id"], target_matte_type, None if portrait_target_matte_type == art["portrait_matte_id"] else portrait_target_matte_type)
                    except ResponseError:
                        logging.warning(f'Unable to change mats to {target_matte_type}/{portrait_target_matte_type} for {art["content_id"]} ({art["width"]}x{art["height"]})')
            except KeyError:
                logging.warning(f'no mat for {art}')    
                
    await tv.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        os._exit(1)
