#!/usr/bin/env python3
# exif data class, gets exif data from image, and GPS location (if GPSInfo exists)
# needs PIL (pip install pillow)
# and optionally geopy (pip install geopy) for location

import asyncio
import os, json
from pprint import pprint, pformat
HAVE_PIL = False
try:
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS, IFD
    HAVE_PIL=True
except ImportError:
    pass
HAVE_GEOPY = False
try:
    from geopy.geocoders import Nominatim
    from geopy.adapters import AioHTTPAdapter
    from geopy.extra.rate_limiter import AsyncRateLimiter
    from geopy.exc import GeocoderTimedOut
    HAVE_GEOPY=True
except ImportError:
    pass
import logging

__version__ = '1.0.0'

logging.basicConfig(level=logging.INFO)

class ExifData:
    
    def __init__(self, folder, ip=None):
                           
        self.log = logging.getLogger('Main.'+__class__.__name__)
        self.debug = self.log.getEffectiveLevel() <= logging.DEBUG
        self.folder = folder
        self.ip = ip
        self.exif = {}
        self.gps_task = None
        self.filename = './gps_data.json'
        self.get_files()

    def get_files(self, image_names=None):
        '''
        make list from files in static folder
        '''
        if HAVE_PIL and self.folder:
            if not image_names:
                image_names = [img for img in os.listdir(self.folder) if not img.upper().endswith('.TXT')]
            for file in image_names:
                self.update_exif_dict(file)
            #run as task because of rate limiting
            if not self.gps_task or self.gps_task.done():
                self.gps_task = asyncio.create_task(self.update_addresses(image_names))
                
    def load_data(self):
        try:
            with open(self.filename, 'r') as f:
                data = json.load(f)
                [self.exif[file].update(data[file]) for file in self.exif.keys() if data.get(file)]
        except Exception:
            pass
            
    def save_data(self):
        try:
            with open(self.filename, 'w') as f:
                data = {file:{k:v} for file in self.exif.keys() for k, v in self.exif[file].items() if k ==  'GEOPY_Address'}
                self.log.debug('SAVE:\r\n{}'.format(pformat(data)))
                if data:
                    json.dump(data, f, indent=2)
        except Exception as e:
            pass
        
    def update_exif_dict(self, file):
        '''
        only available if PIL is installed (pip install Pillow)
        get exif tags from image file and update self.exif
        so that we can extract 'DateTimeOriginal' and 'GPSInfo'later
        NOTE: have to use _getexif() getexif() is different
        '''
        if HAVE_PIL and file not in self.exif.keys():
            self.log.info('{}: getting exif data'.format(file))
            img = Image.open(os.path.join(self.folder, file))
            self.exif[file]={TAGS.get(tag, str(tag)): self.conv_bytes(value) for tag, value in (img._getexif() or {}).items()}
            self.log.debug('exif tags:\r\n{}'.format(pformat(self.exif)))
                
    def conv_bytes(self, value):
        return value.decode() if isinstance(value, bytes) else value
        
    def get_key(self, file, key):
        return self.exif.get(file, {}).get(key)
        
    def get_lat_long(self, file):
        '''
        Extract raw GPS data for latitude (north) and longitude (east)
        '''
        gpsinfo = self.get_key(file, 'GPSInfo')
        if isinstance(gpsinfo, dict) and gpsinfo:
            # Convert latitude and longitude from degrees-minutes-seconds to decimal format
            self.log.debug('{}: GPS info:\r\n{}'.format(file, pformat({'{}({})'.format(GPSTAGS[k], k):v for k, v in gpsinfo.items()}))) 
            lat = self._convert_to_degrees(gpsinfo[2]) * (-1 if gpsinfo[1].upper() != "N" else 1)
            lng = self._convert_to_degrees(gpsinfo[4]) * (-1 if gpsinfo[3].upper() != "E" else 1)
            self.log.debug('got lat: {}, long: {}'.format(lat, lng))
            return lat, lng
        return None, None
                
    def _convert_to_degrees(self, value):
        '''
        Helper function to convert the GPS coordinates stored in the EXIF to degrees in float format
        '''
        if value:
            val = [(lambda x,y: float(x/y))(*x) if isinstance(x, tuple) else x for x in value]
            return sum([float(x)/(60**i) for i, x in enumerate(val)])
        return 0    
        
    async def update_addresses(self, file_list):
        '''
        Use Geopy's Nominatim geocoder to retrieve the address for the coordinates
        This is free, but limited to calling once per second, with a unique user_agent name for the app
        see https://operations.osmfoundation.org/policies/nominatim/
        '''
        if HAVE_GEOPY:
            self.load_data()
            async with Nominatim(user_agent="{}-SamsungtvwsGetLocGallery".format(self.ip or ''), timeout=20, adapter_factory=AioHTTPAdapter) as geolocator:
                reverse  = AsyncRateLimiter(geolocator.reverse, min_delay_seconds=1.5, max_retries=1, swallow_exceptions=False)
                for file in file_list:
                    if 'GEOPY_Address' not in self.exif[file].keys():
                        lat, lng = self.get_lat_long(file)
                        if lat and lng:
                            self.log.info('{}: getting GPS data'.format(file))
                            try:
                                locname = await reverse(f"{lat}, {lng}")
                                if locname:
                                    self.exif[file]['GEOPY_Address'] = self.format_address(file, locname)
                                
                            except (asyncio.exceptions.TimeoutError, GeocoderTimedOut) as e:
                                self.log.warning('geocode failed on {}: {}'.format(file, e))
                                await asyncio.sleep(5)
                            continue
                        self.log.info('{}: NO address found'.format(file))
                        self.exif[file]['GEOPY_Address'] = None
            self.save_data()
           
    def format_address(self, file, locname):
        '''
        format address so it fits in modal footer
        '''
        try:
            self.log.debug('{}: location: {}'.format(file, locname.raw))
            addr = [locname.raw['address'].get('village'),
                    locname.raw['address'].get('city_district'),
                    locname.raw['address'].get('territory'),
                    locname.raw['address'].get('country')]
            address = ('{}'.format(', '.join([a for a in addr if a])))
        except Exception as e:
            address = locname.address
        self.log.info('{}: address: {}'.format(file, address))
        return address
        
    def get_location(self, file):
        '''
        returns formatted address
        '''
        return self.get_key(file, 'GEOPY_Address')
        
    def get_date_original(self, file):
        '''
        returns date photo was taken from exif data, or None
        '''
        return self.get_key(file, 'DateTimeOriginal')
        
        
