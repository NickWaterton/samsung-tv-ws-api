# Overview

This example is a web front end for art display on Frame TV's, for use in Art Galleries.

## Install

### Install additional packages

There are additional packages required to support the web framework, to install them, change to this directory and run:
```bash
cd samsung-tv-ws-api/example/web_interface
pip install -r requirements.txt
```
This is *after* you have installed the `samsungframetv` package as described on the main page. `pip` may be `pip3` on your system.

## Usage

### Basic

The entry point is `web_interface.py`, the other files are resources used by the web interface. The command line options are:
```bash
nick@MQTT-Servers-Host:~/Scripts/samsung-tv-ws-api/example/web_interface$ ./web_interface.py -h
usage: web_interface.py [-h] [-p PORT] [-f FOLDER] [-m MATTE] [-t TOKEN_FILE] [-u UPDATE] [-c CHECK] [-d DISPLAY_FOR]
                        [-mo {modal-sm,modal-lg,modal-xl,modal-fullscreen,modal-fullscreen-sm-down,modal-fullscreen-md-down,modal-fullscreen-lg-down,modal-fullscreen-xl-down,modal-fullscreen-xxl-down}]
                        [-s] [-K] [-P] [-S] [-O] [-F] [-X] [-D]
                        ip

Async Art gallery for Samsung Frame TV Version: 1.2.0

positional arguments:
  ip                    ip address of TV (default: None))

options:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  port for web page interface (default: 5000))
  -f FOLDER, --folder FOLDER
                        folder to load images from (default: ./images))
  -m MATTE, --matte MATTE
                        default matte to use (default: none))
  -t TOKEN_FILE, --token_file TOKEN_FILE
                        default token file to use (default: token_file.txt))
  -u UPDATE, --update UPDATE
                        slideshow update period (mins) 0=off (default: 0))
  -c CHECK, --check CHECK
                        how often to check for new art 0=run once (default: 600))
  -d DISPLAY_FOR, --display_for DISPLAY_FOR
                        how long to display manually selected art for (default: 120))
  -mo {modal-sm,modal-lg,modal-xl,modal-fullscreen,modal-fullscreen-sm-down,modal-fullscreen-md-down,modal-fullscreen-lg-down,modal-fullscreen-xl-down,modal-fullscreen-xxl-down}, --modal {modal-sm,modal-lg,modal-xl,modal-fullscreen,modal-fullscreen-sm-down,modal-fullscreen-md-down,modal-fullscreen-lg-down,modal-fullscreen-xl-down,modal-fullscreen-xxl-down}
                        size of modal text box see https://www.w3schools.com/bootstrap5/bootstrap_modal.php for explanation (default: medium)
  -s, --sync            automatically syncronize (needs Pil library) (default: True))
  -K, --kiosk           Show in Kiosk mode (default: False))
  -P, --production      Run in Production server mode (default: False))
  -S, --sequential      sequential slide show (default: False))
  -O, --on              exit if TV is off (default: False))
  -F, --favourite       include favourites in rotation (default: False))
  -X, --exif            Use Exif data (default: True))
  -D, --debug           Debug mode (default: False))

```
The ip address of your TV is required, the rest of the command line is optional.  
Here is a basic example:
```bash
./web_interface.py 192.168.100.32 -u 1 -d 30
```
Where `192.168.100.32` is *your* TV ip address.  
This will start the interface to the frame TV with ip address 192.168.100.32, automatically updasting the displayed image every 1 minutes, and if an image is selected from the web interface, it will be displayed for 30 seconds, before the rotation resumes.  
To view the web interface, open a browser and navigate to:
```bash
http://<ip>:5000
```
Where ip is the ip address of the system running the server (**NOT** the TV).

### Shut Down

Use `<cntl>C` to exit the web server, it takes a few seconds to shut down. The modal window (if any) will be removed.

### TV is off or playing

If the TV turns off, or starts playing TV, the modal image will be removed, and clicking the buttons will do nothing.  
When the TV is switched to art Mode, the image updating will resume, and modal windows will be displayed again.

### Internet

I do not reccomend exposing the web interface to the internet, this is just an example interface, it is **NOT** secure. If you **must** expose it, ensure you only use Production mode `-P` option, and do not expose your `images` directory.

### Typical Use

This interface is intended to be used in a Gallery, so that visitors can see information on the displayed image, or select their own. Typically, you would have this running on a small computer, such as a raspery Pi, with a touch screen in Kiosk mode.  
Examples of kiosk mode for RPI:  
* https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode
* https://github.com/debloper/piosk

### Modes of Operation

There are two main modes of operation, display mode and Kiosk mode. Display mode is the default, Kiosk mode is enabled with the `-K` switch.

#### Display Mode

In Display mode, the images in the `./images` folder (or whatever folder you have selected using the `-f` option) will be displayed as buttons on the web interface. Clicking on one of the buttons will display the image on the TV (assuming the TV is in art mode).  

If you have created a text file to describe the image, the description will appear in a modal window. After the preset time, (option `-d`) the automatic rotation will resume.

#### Kiosk Mode

In Kiosk mode, the images will be displayed in rotation as before, but for each image displayed, the text box describing the currently displayed image will automatically appear.  
You can still close the text box, and select a new image the same way as in display mode.  
This is the command line for Kiosk mode
```bash
./web_interface.py 192.168.100.32 -u 1 -d 30 -K
```
Where `192.168.100.32` is *your* TV ip address.

## Text Files

You can create text files to describe the associated image - this imformation will appear in a modal window, when the image is clicked, or automatically in Kiosk mode.

### Naming  

The text file has to be given the same name as the image file, with a `.TXT` extension.

### Format

The text files are in json format, with the following layout:
```json
{   
    "header"        : "Crimson Finch at Fogg Dam",
    "description"   : "<i>Neochmia phaeton</i>",
    "details"       : "The <b>Crimson Finch</b> is a species of bird in the family <i>Estrildidae</i>. It is found throughout Northern Australia as well as parts of southern New Guinea.<br>Crimson finches feature a distinctively bright crimson coat and are known for their aggression.",
    "time"          : "",
    "location"      : "",
    "credit"        : "wildfoto.au"
}
```
The only mandatory fields are `"header"` and `"details"`, the rest are optional.  
The `"description"` and `"details"` fields support inline html, so italic `<i>`, bold `<b>` line break `<br>` etc are supported as shown in the example above.  

If the image has embedded exif information, the `"location"` and `"time"` fields will be filled in automatically if exif tags `DateTimeOriginal` and `GPSInfo` are available.  
The minnimum example TXT file would be:
```json
{   
    "header"        : "This is snowy trees",
    "details"       : "This Scene shows a winter lanscape with snowy trees"
}
```
**NOTE:** If you enter details for `"time"` and `"location"` in the text file, then the exif data will not overwrite this information.  
**NOTE:** There are rate limits on the free geolocating api, so loading large amounts of GPS info will be slow, and you should read the acceptible use policy at: https://operations.osmfoundation.org/policies/nominatim/ GPS addresses are cached locally in the file `gps_info.json` to reduce hits on the server.  
If there is no TXT file, the modal window will not be shown.

If you use and like this library for art mode, [Buy me a coffee](https://paypal.me/NWaterton).
