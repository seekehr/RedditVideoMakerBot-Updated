#!/usr/bin/env python
import math
import sys
import time
from os import name
from pathlib import Path
from subprocess import Popen
from typing import NoReturn

from prawcore import ResponseException

from reddit.subreddit import get_subreddit_threads
from utils import settings
from utils.cleanup import cleanup
from utils.console import print_markdown, print_step, print_substep
from utils.ffmpeg_install import ffmpeg_install
from utils.id import id
from utils.version import checkversion
from video_creation.background import (
    chop_background,
    download_background_audio,
    download_background_video,
    get_background_config,
)
from video_creation.final_video import make_final_video
from video_creation.screenshot_downloader import get_screenshots_of_reddit_posts
from video_creation.voices import save_text_to_mp3

__VERSION__ = "3.3.0"

checkversion(__VERSION__)

# --- Start of logging setup ---
class Tee:
    def __init__(self, *files):
        self.files = files

    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()

    def flush(self):
        for f in self.files:
            f.flush()

log_file_handle = None
original_stdout = None
original_stderr = None

def start_logging():
    global log_file_handle, original_stdout, original_stderr
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log_file_handle = open("logs.txt", "w", encoding="utf-8")
    sys.stdout = Tee(original_stdout, log_file_handle)
    sys.stderr = Tee(original_stderr, log_file_handle)

def stop_logging():
    global log_file_handle, original_stdout, original_stderr
    if log_file_handle:
        log_file_handle.close()
    if original_stdout:
        sys.stdout = original_stdout
    if original_stderr:
        sys.stderr = original_stderr
# --- End of logging setup ---

def main(POST_ID=None) -> None:
    global redditid, reddit_object
    reddit_object = get_subreddit_threads(POST_ID)

    if reddit_object is None:
        print_substep("No suitable Reddit post found. Skipping.", style="bold yellow")
        return False

    redditid = id(reddit_object)
    length, number_of_comments = save_text_to_mp3(reddit_object)

    if (settings.config["settings"]["read_comment_as_story"]
            and not settings.config["settings"]["storymode"]
            and number_of_comments == 0):
        print_substep("No appropriate comment found for read_comment_as_story mode. Skipping video generation.", style="bold yellow")
        return False

    length = math.ceil(length)
    get_screenshots_of_reddit_posts(reddit_object, number_of_comments)
    bg_config = {
        "video": get_background_config("video"),
        "audio": get_background_config("audio"),
    }
    download_background_video(bg_config["video"])
    download_background_audio(bg_config["audio"])
    chop_background(bg_config, length, reddit_object)
    make_final_video(number_of_comments, length, reddit_object, bg_config)
    return True


def run_many(times) -> None:
    for x in range(1, times + 1):
        print_step(
            f'on the {x}{("th", "st", "nd", "rd", "th", "th", "th", "th", "th", "th")[x % 10]} iteration of {times}'
        )
        
        successful_run = False
        max_retries = settings.config["settings"].get("redo_per_iteration", 0)
        for attempt in range(max_retries + 1):
            if main():
                successful_run = True
                break
            elif attempt < max_retries:
                print_substep(f"Iteration {x}, attempt {attempt + 1} failed. Retrying ({max_retries - attempt} retries left)...", style="bold yellow")
            else:
                print_substep(f"Iteration {x}, attempt {attempt + 1} failed. No retries left.", style="bold red")
        
        if not successful_run:
            print_substep(f"Iteration {x} completely failed after {max_retries + 1} attempts.", style="bold red")


def shutdown() -> NoReturn:
    if "redditid" in globals():
        print_markdown("## Clearing temp files")
        cleanup(redditid)

    print("Exiting...")
    sys.exit()


if __name__ == "__main__":
    start_time = time.time()
    start_logging()

    if sys.version_info.major != 3 or sys.version_info.minor not in [10, 11]:
        print("This program requires Python 3.10 or 3.11.")
        sys.exit()
    ffmpeg_install()
    directory = Path().absolute()
    config = settings.check_toml(
        f"{directory}/utils/.config.template.toml", f"{directory}/config.toml"
    )
    if not config:
        sys.exit()

    if (
        not settings.config["settings"]["tts"]["tiktok_sessionid"]
        or settings.config["settings"]["tts"]["tiktok_sessionid"] == ""
    ) and config["settings"]["tts"]["voice_choice"] == "tiktok":
        print_substep(
            "TikTok voice requires a sessionid.",
            "bold red",
        )
        sys.exit()
    try:
        if config["reddit"]["thread"]["post_id"]:
            for index, post_id in enumerate(config["reddit"]["thread"]["post_id"].split("+")):
                index += 1
                print_step(
                    f'on the {index}{("st" if index % 10 == 1 else ("nd" if index % 10 == 2 else ("rd" if index % 10 == 3 else "th")))} post of {len(config["reddit"]["thread"]["post_id"].split("+"))}'
                )
                main(post_id)
        elif config["settings"]["times_to_run"]:
            run_many(config["settings"]["times_to_run"])
        else:
            main()
    except KeyboardInterrupt:
        shutdown()
    except ResponseException:
        print_markdown("## Invalid credentials\nPlease check your credentials in the config.toml file")
        shutdown()
    except Exception as err:
        config["settings"]["tts"]["tiktok_sessionid"] = "REDACTED"
        config["settings"]["tts"]["elevenlabs_api_key"] = "REDACTED"
        print_step(
            f"An error occurred: {err}\nVersion: {__VERSION__}\nConfig (sensitive info redacted): {config['settings']}"
        )
        sys.stderr.flush()
        sys.stdout.flush()
        raise err
    finally:
        stop_logging()
        end_time = time.time()
        print(f"Entire code took {end_time - start_time:.2f} seconds to run")
