import logging
import signal
import sys
import time
from typing import Optional
import aiohttp
import asyncio
import nfc
from dataclasses import dataclass
import requests
import uuid
import appsettings #you shouldnt need to edit this file
import usersettings #this is the file you might need to edit

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    sonos_api_url: str
    room: str
    nfc_path: str

class NFCReader:
    def __init__(self, config: Config):
        self.config = config
        self.reader: Optional[nfc.ContactlessFrontend] = None
        self.running = True
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.info("Shutting down gracefully...")
        self.running = False
        if self.reader:
            self.reader.close()
        sys.exit(0)

    async def check_api_connection(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.config.sonos_api_url) as response:
                    return response.status == 200
        except aiohttp.ClientError as e:
            logger.warning(f"API connection check failed: {e}")
            return False

    def initialize_reader(self) -> bool:
        try:
            self.reader = nfc.ContactlessFrontend(self.config.nfc_path)
            logger.info(f"Connected to NFC reader: {self.reader}")
            return True
        except IOError as e:
            logger.error(f"Failed to initialize NFC reader: {e}")
            self._show_reader_troubleshooting()
            return False

    def _show_reader_troubleshooting(self):
        logger.error("""
        Please check:
        1. Run 'python -m nfcpy' to test reader
        2. Check if reader is in use:
           > pm2 status
           > pm2 stop readnfc
        3. To remove from startup:
           > pm2 delete readnfc
           > pm2 save
           > sudo reboot
        """)

    async def run(self):
        if not self.initialize_reader():
            return

        logger.info(f"Room set to: {self.config.room}")
        api_status = await self.check_api_connection()
        
        if api_status:
            logger.info("API connection successful")
        else:
            logger.warning("API not responding, continuing with reduced functionality")

        logger.info("Ready for NFC tags")

        while self.running:
            try:
                self.reader.connect(rdwr={
                    'on-connect': self.on_tag_detected,
                    'beep-on-connect': False
                })
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error reading NFC: {e}")
                await asyncio.sleep(1)

    def on_tag_detected(self, tag):
        global sonosroom_local

        if tag.ndef:
            for record in tag.ndef.records:
                try:
                    receivedtext = record.text
                except:
                    logger.error("Error reading a *TEXT* tag from NFC.")
                    return True
                
                receivedtext_lower = receivedtext.lower()

                logger.info(f"Read from NFC tag: {receivedtext}")

                servicetype = ""
                
                #check if a full HTTP URL read from NFC
                if receivedtext_lower.startswith ('http'):
                    servicetype = "completeurl"
                    sonosinstruction = receivedtext

                #determine which music service read from NFC
                if receivedtext_lower.startswith ('spotify'):
                    servicetype = "spotify"
                    sonosinstruction = "spotify/now/" + receivedtext

                if receivedtext_lower.startswith ('tunein'):
                    servicetype = "tunein"
                    sonosinstruction = receivedtext
                
                if receivedtext_lower.startswith ('favorite'):
                    servicetype = "favorite"
                    sonosinstruction = receivedtext
                
                if receivedtext_lower.startswith ('amazonmusic:'):
                    servicetype = "amazonmusic"
                    sonosinstruction = "amazonmusic/now/" + receivedtext[12:]

                if receivedtext_lower.startswith ('apple:'):
                    servicetype = "applemusic"
                    sonosinstruction = "applemusic/now/" + receivedtext[6:]

                if receivedtext_lower.startswith ('applemusic:'):
                    servicetype = "applemusic"
                    sonosinstruction = "applemusic/now/" + receivedtext[11:]

                if receivedtext_lower.startswith ('bbcsounds:'):
                    servicetype = "bbcsounds"
                    sonosinstruction = 'bbcsounds/play/' + receivedtext[10:]

                #check if a Sonos "command" or room change read from NFC
                if receivedtext_lower.startswith ('command'):
                    servicetype = "command"
                    sonosinstruction = receivedtext[8:]
                
                if receivedtext_lower.startswith ('room'):
                    servicetype = "room"
                    sonosroom_local = receivedtext[5:]
                    logger.info(f"Sonos room changed to {sonosroom_local}")
                    return True

                #if no service or command detected, exit
                if servicetype == "":
                    logger.warning("Service type not recognised. NFC tag text should begin spotify, tunein, amazonmusic, apple/applemusic, command or room.")
                    if usersettings.sendanonymoususagestatistics == "yes":
                        r = requests.post(appsettings.usagestatsurl, data = {'time': time.time(), 'value1': appsettings.appversion, 'value2': hex(uuid.getnode()), 'value3': 'invalid service type sent'})
                    return True
                
                logger.info(f"Detected {servicetype} service request")

                #build the URL we want to request
                if servicetype.lower() == 'completeurl':
                    urltoget = sonosinstruction
                else:
                    urltoget = usersettings.sonoshttpaddress + "/" + sonosroom_local + "/" + sonosinstruction
                
                #check Sonos API is responding
                try:
                    r = requests.get(usersettings.sonoshttpaddress)
                except:
                    logger.error(f"Failed to connect to Sonos API at {usersettings.sonoshttpaddress}")
                    return True

                #clear the queue for every service request type except commands
                if servicetype != "command":
                    logger.info("Clearing Sonos queue")
                    r = requests.get(usersettings.sonoshttpaddress + "/" + sonosroom_local + "/clearqueue")

                #use the request function to get the URL built previously, triggering the sonos
                logger.info(f"Fetching URL via HTTP: {urltoget}")
                r = requests.get(urltoget)

                if r.status_code != 200:
                    logger.error("Error code returned from Sonos API")
                    return True
                
                logger.info(f"Sonos API reports {r.json()['status']}")

        else:
            logger.warning("""
            NFC reader could not read tag. This can be because the reader didn't get a clear read of the card. 
            If the issue persists then this is usually because (a) the tag is encoded (b) you are trying to use a 
            mifare classic card, which is not supported or (c) you have tried to add data to the card which is not 
            in text format. Please check the data on the card using NFC Tools on Windows or Mac.
            """)
            if usersettings.sendanonymoususagestatistics == "yes":
                r = requests.post(appsettings.usagestatsurl, data = {'time': time.time(), 'value1': appsettings.appversion, 'value2': hex(uuid.getnode()), 'value3': 'nfcreaderror'})

        return True

async def main():
    config = Config(
        sonos_api_url=usersettings.sonoshttpaddress,
        room=usersettings.sonosroom,
        nfc_path=usersettings.nfc_reader_path
    )
    
    reader = NFCReader(config)
    await reader.run()

if __name__ == "__main__":
    asyncio.run(main())
