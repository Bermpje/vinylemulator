import time
import nfc
import logging
from typing import Optional, Dict
import sys
import soco
from soco.discovery import by_name
import appsettings
import usersettings
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modify SonosController to accept an existing speaker
class SonosController:
    def __init__(self, initial_room: str, existing_speaker=None):
        self.current_room = initial_room
        self.speaker = existing_speaker or self._get_speaker(initial_room)
        self.service_map: Dict[str, str] = {
            'spotify': ('spotify', lambda x: ('spotify', x)),
            'tunein': ('tunein', lambda x: ('tunein', x)),
            'favorite': ('favorite', self._handle_favorite),
            'amazonmusic:': ('amazonmusic', lambda x: ('amazonmusic', x[12:])),
            'apple:': ('applemusic', lambda x: ('applemusic', x[6:])),
            'applemusic:': ('applemusic', lambda x: ('applemusic', x[11:])),
            'command': ('command', self._handle_command),
            'room': ('room', lambda x: ('room', x[5:]))
        }

    def _get_speaker(self, room_name: str) -> soco.SoCo:
        speaker = by_name(room_name)
        if not speaker:
            available_speakers = soco.discover()
            if available_speakers:
                speaker = list(available_speakers)[0]
                logger.warning(f"Room {room_name} not found, using {speaker.player_name}")
            else:
                raise RuntimeError("No Sonos speakers found")
        return speaker

    def _handle_favorite(self, favorite_name: str) -> tuple[str, str]:
        favorites = self.speaker.get_sonos_favorites()
        for favorite in favorites:
            if favorite.title.lower() == favorite_name.lower():
                return ('favorite', favorite)
        logger.warning(f"Favorite {favorite_name} not found")
        return ('favorite', None)

    def _handle_command(self, command: str) -> tuple[str, str]:
        commands = {
            'play': lambda: self.speaker.play(),
            'pause': lambda: self.speaker.pause(),
            'next': lambda: self.speaker.next(),
            'previous': lambda: self.speaker.previous(),
            'volume_up': lambda: self.speaker.volume + 10,
            'volume_down': lambda: self.speaker.volume - 10
        }
        return ('command', commands.get(command.lower(), lambda: None))

    def _process_nfc_record(self, record) -> bool:
        try:
            received_text = record.text
        except Exception as e:
            logger.error(f"Error reading TEXT tag from NFC: {e}")
            return True

        received_text_lower = received_text.lower()
        logger.info(f"Read from NFC tag: {received_text}")

        service_type, instruction = self._parse_service_type(received_text, received_text_lower)
        if not service_type:
            logger.warning("Service type not recognised")
            return True

        if service_type == "room":
            self.current_room = instruction
            self.speaker = self._get_speaker(instruction)
            logger.info(f"Sonos room changed to {self.current_room}")
            return True

        return self._handle_sonos_request(service_type, instruction)

    def _handle_sonos_request(self, service_type: str, instruction: str) -> bool:
        try:
            if service_type != "command":
                self.speaker.clear_queue()
            
            if service_type == 'spotify':
                self.speaker.play_uri(f"spotify:track:{instruction}")
            elif service_type == 'favorite':
                if instruction:
                    self.speaker.play_favorite(instruction)
            elif service_type == 'command':
                instruction()
            
            return True
        except Exception as e:
            logger.error(f"Error communicating with Sonos: {e}")
            return True

    def handle_nfc_tag(self, tag: nfc.tag) -> bool:
        if not tag.ndef:
            return True

        for record in tag.ndef.records:
            try:
                return self._process_nfc_record(record)
            except Exception as e:
                logger.error(f"Error processing NFC tag: {e}")
                return True
        return True

# Modify the touched function to accept a speaker
def touched(tag, speaker=None):
    controller = SonosController(usersettings.sonosroom, speaker)
    return controller.handle_nfc_tag(tag)

# Update main function to pass the speaker
def main():
    print("\nLoading and checking readnfc")
    print("~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n")
    print("SCRIPT")
    print(f"You are running version {appsettings.appversion}...")

    print("\nNFC READER")
    print("Connecting to NFC reader...")
    try:
        reader = nfc.ContactlessFrontend(usersettings.nfc_reader_path)
    except IOError:
        print("... could not connect to reader\n")
        print("You should check that the reader is working by running: python -m nfcpy")
        sys.exit()

    print(f"... and connected to {reader}\n")

    print("SONOS Setup")
    sonosroom_local = usersettings.sonosroom    
    print(f"Sonos room set to {sonosroom_local}")

    print("Attempting direct connection to Sonos speaker...")
    try:
        direct_speaker = soco.SoCo('192.168.50.152')
        info = direct_speaker.get_speaker_info()
        print(f"\nSuccessfully connected to speaker:")
        print(f"  - Name: {direct_speaker.player_name}")
        print(f"  - Model: {info['model_name']}")
        print(f"  - Version: {info['software_version']}")
        
        print("\nOK, all ready! Present an NFC tag.\n")
        
        while True:
            reader.connect(rdwr={
                'on-connect': lambda tag: touched(tag, direct_speaker), 
                'beep-on-connect': False
            })
            time.sleep(0.1)
        
    except Exception as e:
        print(f"\nError connecting to speaker at 192.168.50.152: {e}")
        print("Debug info:")
        print(f"  Python version: {sys.version}")
        print(f"  SoCo version: {soco.__version__}")
        sys.exit(1)

if __name__ == "__main__":
    main()
