#!/usr/bin/env python3
import argparse
import logging
import os
import sys

sys.path.append("../")

from samsungframetv import SamsungTVWS, exceptions, __version__

def parseargs():
    # Add command line argument parsing
    parser = argparse.ArgumentParser(description='Example art Samsung Frame TV Version: {}'.format(__version__))
    parser.add_argument('ip', action="store", type=str, default=None, help='ip address of TV (default: %(default)s))')
    parser.add_argument('-t','--token_file', action="store", type=str, default="token_file.txt", help='default token file to use (default: %(default)s))')
    parser.add_argument('-D','--debug', action='store_true', default=False, help='Debug mode (default: %(default)s))')
    return parser.parse_args()

def main():
    args = parseargs()
    logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(funcName)s %(message)s',
                        level=logging.DEBUG if args.debug else logging.INFO)
    logging.debug('debug mode')
    
    # Example showing different token files for different tv's, with default of "token_file.txt" from args.token_file
    tokens = {  '192.168.100.32' : "token_file1.txt",
                '192.168.100.73' : "token_file2.txt"
             }
    token_file = tokens.get(args.ip, args.token_file)

    # Normal constructor (will ask for connection every time)
    #tv = SamsungTVWS(host=args.ip)
    
    # Autosave token to file
    tv = SamsungTVWS(host=args.ip, port=8002, token_file=token_file)

    # Get device info (device name, model, supported features..)
    #info = tv.rest_device_info()
    #logging.info(info)
    
    # Get device power state as bool (True=ON)
    on = tv.on()
    logging.info('tv is ON: {}'.format(on))
    
    if on:
        # Is art mode supported?
        supported = tv.art().supported()
        logging.info('art mode supported: {}'.format(supported))
        
        if supported:
            #get api version 4.3.4.0 is new api, 2.03 is old api
            api_version = tv.art().get_api_version()
            logging.info('api version: {}'.format(api_version))
            
            # Determine whether the TV is currently in art mode
            info = tv.art().get_artmode()
            logging.info('artmode: {}'.format(info))

            # List the art available on the device
            info = tv.art(10).available()                               #note the art(10) here sets the timeout to 10 seconds, not the normal 5 second timeout
            logging.info('available: {}'.format(info))
            
            # get brightness
            info = tv.art().get_brightness()
            logging.info('art brightness: {}'.format(info))
            
            # get colour temperature
            info = tv.art().get_color_temperature()
            logging.info('art colour temperature: {}'.format(info))
            
            # Retrieve information about the currently selected art
            info = tv.art().get_current()
            logging.info('current art: {}'.format(info))
            content_id = info['content_id']
            
            #get thumbnail for current artwork
            try:
                thumb = b''
                if int(api_version.replace('.','')) < 4000:             #check api version number, and use correct api call
                    #thumb = tv.art().get_thumbnail(content_id)         #old api, just gets binary data
                    thumbs = tv.art().get_thumbnail(content_id, True)   #old api, gets thumbs in same format as new api
                else:
                    thumbs = tv.art().get_thumbnail_list(content_id)    #list of content_id's or single content_id
                if thumbs:                                              #dictionary of content_id (with file type extension) and binary data
                    thumb = list(thumbs.values())[0]
                    content_id = list(thumbs.keys())[0]
                logging.info('got thumbnail for {} binary data length: {}'.format(content_id, len(thumb)))
            except Exception as e:
                logging.error('FAILED to get thumbnail for {}: {}'.format(content_id, e))
            
            #get slideshow status, old api fails silently on new TV's, but new api throws ResponseError on older ones
            try:
                info = tv.art().get_slideshow_status()      #new api
            except exceptions.ResponseError:
                info = tv.art().get_auto_rotation_status()  #old api
            logging.info('current slideshow status: {}'.format(info))
            
            #upload file
            filename = "drwmrk_cropped2.jpeg"#None # enter file name/path here eg "IMG_0256.JPG"
            content_id = None
            if filename:
                content_id = tv.art(10).upload(filename)        #note the art(10) here sets the timeout to 10 seconds, not the normal 5 second timeout
                content_id = os.path.splitext(content_id)[0]    #remove file extension if any (eg .jpg)
                logging.info('uploaded {} to tv as {}'.format(filename, content_id))

            #delete art on tv
            if content_id:
                result = tv.art().delete_list([content_id])
                if result:
                    logging.info('deleted from tv: {}'.format([content_id]))
                else:
                    logging.warning('FAILED to delete from tv: {}'.format([content_id]))
            

main()