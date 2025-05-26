import os
import re
from pathlib import Path
from typing import Tuple

import numpy as np
import translators
from moviepy.audio.AudioClip import AudioClip
from moviepy.audio.fx.volumex import volumex
from moviepy.editor import AudioFileClip
from rich.progress import track

from utils import settings
from utils.console import print_step, print_substep
from utils.voice import sanitize_text

DEFAULT_MAX_LENGTH: int = 50


class TTSEngine:
    """Calls the given TTS engine to reduce code duplication and allow multiple TTS engines.

    Args:
        tts_module            : The TTS module. Your module should handle the TTS itself and saving to the given path under the run method.
        reddit_object         : The reddit object that contains the posts to read.
        path (Optional)       : The unix style path to save the mp3 files to. This must not have leading or trailing slashes.
        max_length (Optional) : The maximum length of the mp3 files in total.

    Notes:
        tts_module must take the arguments text and filepath.
    """

    def __init__(
        self,
        tts_module,
        reddit_object: dict,
        path: str = "assets/temp/",
        max_length: int = DEFAULT_MAX_LENGTH,
        last_clip_length: int = 0,
    ):
        self.tts_module = tts_module()
        self.reddit_object = reddit_object

        self.redditid = re.sub(r"[^\w\s-]", "", reddit_object["thread_id"])
        self.path = path + self.redditid + "/mp3"
        self.max_length = max_length
        self.length = 0
        self.last_clip_length = last_clip_length

    def add_periods(
        self,
    ):
        for comment in self.reddit_object["comments"]:
            regex_urls = r"((http|https)\:\/\/)?[a-zA-Z0-9\.\/\?\:@\-_=#]+\.([a-zA-Z]){2,6}([a-zA-Z0-9\.\&\/\?\:@\-_=#])*"
            comment["comment_body"] = re.sub(regex_urls, " ", comment["comment_body"])
            comment["comment_body"] = comment["comment_body"].replace("\n", ". ")
            comment["comment_body"] = re.sub(r"\bAI\b", "A.I", comment["comment_body"])
            comment["comment_body"] = re.sub(r"\bAGI\b", "A.G.I", comment["comment_body"])
            if comment["comment_body"][-1] != ".":
                comment["comment_body"] += "."
            comment["comment_body"] = comment["comment_body"].replace(". . .", ".")
            comment["comment_body"] = comment["comment_body"].replace(".. . ", ".")
            comment["comment_body"] = comment["comment_body"].replace(". . ", ".")
            comment["comment_body"] = re.sub(r'\."\.', '".', comment["comment_body"])

    def run(self) -> Tuple[int, int]:
        Path(self.path).mkdir(parents=True, exist_ok=True)
        print_step("Saving Text to MP3 files...")

        self.add_periods()
        self.call_tts("title", process_text(self.reddit_object["thread_title"]))
        idx = 0

        if settings.config["settings"]["storymode"]:
            if settings.config["settings"]["storymodemethod"] == 0:
                if len(self.reddit_object["thread_post"]) > self.tts_module.max_chars:
                    self.split_post(self.reddit_object["thread_post"], "postaudio")
                else:
                    self.call_tts("postaudio", process_text(self.reddit_object["thread_post"]))
            
            elif settings.config["settings"]["storymodemethod"] == 1:
                audio_segments_text = self.reddit_object.get("audio_segments", [])
                if audio_segments_text:
                    for idx, text_segment in track(enumerate(audio_segments_text), "Processing audio segments for story..."):
                        self.call_tts(f"audio_segment-{idx}", process_text(text_segment))
                else:
                    print_substep("No audio segments found for storymode method 1.", style="yellow")
                idx = len(audio_segments_text) -1 if audio_segments_text else -1

        elif settings.config["settings"]["read_comment_as_story"]:
            audio_segments_text = self.reddit_object.get("audio_segments", [])
            actual_segments_processed = 0

            if audio_segments_text:
                for i, text_segment_sentence in track(enumerate(audio_segments_text), "Processing audio segments for comment..."):
                    segment_filename_base = f"audio_segment-{i}"
                    segment_full_path = f"{self.path}/{segment_filename_base}.mp3"
                    
                    processed_sentence = process_text(text_segment_sentence)

                    if not processed_sentence or processed_sentence.isspace():
                        print_substep(f"Skipping empty sentence segment {i} for read_comment_as_story.", style="dim")
                        continue

                    if len(processed_sentence) > self.tts_module.max_chars:
                        print_substep(f"Sentence segment {i} is too long ({len(processed_sentence)} chars), splitting for TTS.", style="dim")
                        self.split_post(processed_sentence, segment_filename_base)
                    else:
                        self.call_tts(segment_filename_base, processed_sentence)
                    
                    if os.path.exists(segment_full_path) and os.path.getsize(segment_full_path) > 0:
                        actual_segments_processed += 1
                    else:
                        print_substep(f"Warning: Audio segment file {segment_full_path} was not created or is empty. TTS might have failed for this segment.", style="bold yellow")
                
                idx = actual_segments_processed
            else:
                print_substep("No audio segments (sentences) found for read_comment_as_story.", style="yellow")
                idx = 0

        else:
            for idx, comment in track(enumerate(self.reddit_object["comments"]), "Saving comments to audio..."):
                if self.length > self.max_length and idx > 1:
                    self.length -= self.last_clip_length
                    idx -= 1
                    break
                if (
                    len(comment["comment_body"]) > self.tts_module.max_chars
                ):
                    self.split_post(comment["comment_body"], idx)
                else:
                    self.call_tts(f"{idx}", process_text(comment["comment_body"]))

        if settings.config["settings"]["storymode"] and settings.config["settings"]["storymodemethod"] == 1:
            final_idx = len(self.reddit_object.get("audio_segments", []))
        elif settings.config["settings"]["read_comment_as_story"]:
            final_idx = idx
        else:
            final_idx = idx

        print_substep("Saved Text to MP3 files successfully.", style="bold green")
        return self.length, final_idx

    def split_post(self, text: str, idx):
        split_files = []
        split_text = [
            x.group().strip()
            for x in re.finditer(
                r" *(((.|\n){0," + str(self.tts_module.max_chars) + "})(\.|.$))", text
            )
        ]
        self.create_silence_mp3()

        idy = None
        for idy, text_cut in enumerate(split_text):
            newtext = process_text(text_cut)

            if not newtext or newtext.isspace():
                print("newtext was blank because sanitized split text resulted in none")
                continue
            else:
                self.call_tts(f"{idx}-{idy}.part", newtext)
                with open(f"{self.path}/list.txt", "w") as f:
                    for idz in range(0, len(split_text)):
                        f.write("file " + f"'{idx}-{idz}.part.mp3'" + "\n")
                    split_files.append(str(f"{self.path}/{idx}-{idy}.part.mp3"))
                    f.write("file " + f"'silence.mp3'" + "\n")

                os.system(
                    "ffmpeg -f concat -y -hide_banner -loglevel panic -safe 0 "
                    + "-i "
                    + f"{self.path}/list.txt "
                    + "-c copy "
                    + f"{self.path}/{idx}.mp3"
                )
        try:
            for i in range(0, len(split_files)):
                os.unlink(split_files[i])
        except FileNotFoundError as e:
            pass
        except OSError:
            pass

    def call_tts(self, filename: str, text: str):
        self.tts_module.run(
            text,
            filepath=f"{self.path}/{filename}.mp3",
            random_voice=settings.config["settings"]["tts"]["random_voice"],
        )
        try:
            clip = AudioFileClip(f"{self.path}/{filename}.mp3")
            self.last_clip_length = clip.duration
            self.length += clip.duration
            clip.close()
        except:
            self.length = 0

    def create_silence_mp3(self):
        silence_duration = settings.config["settings"]["tts"]["silence_duration"]
        silence = AudioClip(
            make_frame=lambda t: np.sin(440 * 2 * np.pi * t),
            duration=silence_duration,
            fps=44100,
        )
        silence = volumex(silence, 0)
        silence.write_audiofile(f"{self.path}/silence.mp3", fps=44100, verbose=False, logger=None)


def process_text(text: str, clean: bool = True):
    lang = settings.config["reddit"]["thread"]["post_lang"]
    new_text = sanitize_text(text) if clean else text
    if lang:
        print_substep("Translating Text...")
        translated_text = translators.translate_text(text, translator="google", to_language=lang)
        new_text = sanitize_text(translated_text)
    return new_text
