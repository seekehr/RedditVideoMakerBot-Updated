import random

from gtts import gTTS

from utils import settings


class GTTS:
    def __init__(self):
        self.max_chars = 5000

    def run(self, text, filepath):
        tts = gTTS(
            text=text,
            lang=settings.config["reddit"]["thread"]["post_lang"] or "en",
            slow=False,
        )
        tts.save(filepath)
