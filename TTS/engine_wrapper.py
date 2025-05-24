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

DEFAULT_MAX_LENGTH: int = (
    50  # Video length variable, edit this on your own risk. It should work, but it's not supported
)


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
    ):  # adds periods to the end of paragraphs (where people often forget to put them) so tts doesn't blend sentences
        for comment in self.reddit_object["comments"]:
            # remove links
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
        # processed_text = ##self.reddit_object["thread_post"] != ""
        idx = 0

        if settings.config["settings"]["storymode"]:
            if settings.config["settings"]["storymodemethod"] == 0:
                # Storymode method 0: processes thread_post as a single block or splits if too long by chars
                if len(self.reddit_object["thread_post"]) > self.tts_module.max_chars:
                    self.split_post(self.reddit_object["thread_post"], "postaudio") # Creates postaudio.mp3
                else:
                    self.call_tts("postaudio", process_text(self.reddit_object["thread_post"]))
            
            elif settings.config["settings"]["storymodemethod"] == 1:
                # Storymode method 1: uses pre-parsed audio_segments
                audio_segments_text = self.reddit_object.get("audio_segments", [])
                if audio_segments_text:
                    for idx, text_segment in track(enumerate(audio_segments_text), "Processing audio segments for story..."):
                        self.call_tts(f"audio_segment-{idx}", process_text(text_segment))
                else:
                    print_substep("No audio segments found for storymode method 1.", style="yellow")
                # idx here will be the number of audio segments - 1, or 0 if empty
                idx = len(audio_segments_text) -1 if audio_segments_text else -1 # -1 indicates no clips if list is empty

        elif settings.config["settings"]["read_first_comment_as_story"]:
            # New mode: process first comment like a story, using pre-parsed audio_segments
            audio_segments_text = self.reddit_object.get("audio_segments", []) # Parsed sentences of the first comment
            segment_temp_files = []
            actual_segments_processed = 0

            if audio_segments_text:
                for i, text_segment in track(enumerate(audio_segments_text), "Processing audio segments for first comment..."):
                    segment_filename_base = f"audio_segment-{i}"
                    self.call_tts(segment_filename_base, process_text(text_segment))
                    segment_temp_files.append(f"{self.path}/{segment_filename_base}.mp3")
                    actual_segments_processed += 1
                
                if segment_temp_files:
                    output_concatenated_filename = f"{self.path}/0.mp3" # Main audio for the first comment
                    list_file_path = f"{self.path}/concat_list_first_comment.txt"
                    with open(list_file_path, "w") as f_list:
                        for audio_file in segment_temp_files:
                            f_list.write(f"file '{os.path.relpath(audio_file, os.path.dirname(list_file_path))}'\\n")
                    
                    ffmpeg_command = (
                        f"ffmpeg -f concat -safe 0 -y -hide_banner -loglevel panic"
                        f' -i "{list_file_path}" -c copy "{output_concatenated_filename}"'
                    )
                    os.system(ffmpeg_command)
                    try: os.remove(list_file_path)
                    except OSError as e: print(f"Warning: Could not remove temp concat list: {e}")

                    try:
                        clip = AudioFileClip(output_concatenated_filename)
                        self.length = clip.duration # Total length is of the concatenated comment
                        self.last_clip_length = clip.duration
                        clip.close()
                    except Exception as e:
                        print(f"Could not get duration of concatenated 0.mp3: {e}")
                idx = 0 # Represents one comment processed (the first one)
            else:
                print_substep("No audio segments found for read_first_comment_as_story mode.", style="yellow")
                idx = -1 # No clips essentially

        else:
            for idx, comment in track(enumerate(self.reddit_object["comments"]), "Saving comments to audio..."):
                # ! Stop creating mp3 files if the length is greater than max length.
                if self.length > self.max_length and idx > 1:
                    self.length -= self.last_clip_length
                    idx -= 1
                    break
                if (
                    len(comment["comment_body"]) > self.tts_module.max_chars
                ):  # Split the comment if it is too long
                    self.split_post(comment["comment_body"], idx)  # Split the comment
                else:  # If the comment is not too long, just call the tts engine
                    self.call_tts(f"{idx}", process_text(comment["comment_body"]))

        # The return `idx` should be number of actual audio files created for separate comments/clips
        # For storymode method 1, it is len(audio_segments) if >0, else 0. (or number of items on timeline)
        # For read_first_comment_as_story, it is 1 (for 0.mp3) if successful, else 0.
        # For normal comments, it is the number of comments processed.
        # A more consistent return would be the count of primary audio timeline entries.

        # Let's adjust what `idx` means for return for storymode 1 and read_first_comment
        if settings.config["settings"]["storymode"] and settings.config["settings"]["storymodemethod"] == 1:
            final_idx = len(self.reddit_object.get("audio_segments", []))
        elif settings.config["settings"]["read_first_comment_as_story"]:
            final_idx = 1 if actual_segments_processed > 0 else 0
        else: # storymode method 0 or standard comments
            # For storymode method 0, idx is typically 0 (for postaudio.mp3) or 1 if title is also counted by caller
            # For standard comments, idx is already the number of comments processed.
            # This part of idx logic might need review based on how final_video.py uses number_of_clips
            # Let's assume for storymode method 0, number_of_clips in final_video refers to post parts (1: postaudio.mp3)
            # and for standard, it's number of comments.
            final_idx = idx # This is complex. Original `idx` from loops is fine for standard comments.
                           # For storymode 0, the caller of TTSEngine.run() often expects a specific value for number_of_clips.
                           # For now, pass through the idx from the loop that was active. It's usually 0 or num_comments.

        print_substep("Saved Text to MP3 files successfully.", style="bold green")
        return self.length, final_idx # Return final_idx reflecting number of main audio pieces

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
            # print(f"{idx}-{idy}: {newtext}\n")

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
            print("File not found: " + e.filename)
        except OSError:
            print("OSError")

    def call_tts(self, filename: str, text: str):
        self.tts_module.run(
            text,
            filepath=f"{self.path}/{filename}.mp3",
            random_voice=settings.config["settings"]["tts"]["random_voice"],
        )
        # try:
        #     self.length += MP3(f"{self.path}/{filename}.mp3").info.length
        # except (MutagenError, HeaderNotFoundError):
        #     self.length += sox.file_info.duration(f"{self.path}/{filename}.mp3")
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
