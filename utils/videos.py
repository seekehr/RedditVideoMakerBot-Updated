import json
import time
from pathlib import Path

from praw.models import Submission

from utils import settings
from utils.console import print_step, print_substep


def check_done(
    redditobj: Submission,
) -> Submission:
    # don't set this to be run anyplace that isn't subreddit.py bc of inspect stack
    """Checks if the chosen post has already been generated

    Args:
        redditobj (Submission): Reddit object gotten from reddit/subreddit.py

    Returns:
        Submission|None: Reddit object in args
    """
    videos_json_path = Path("./video_creation/data/videos.json")
    done_videos = []

    if videos_json_path.exists():
        try:
            with open(videos_json_path, "r", encoding="utf-8") as done_vids_raw:
                loaded_data = json.load(done_vids_raw)
                if isinstance(loaded_data, list):
                    done_videos = loaded_data
                else:
                    with open(videos_json_path, "w", encoding="utf-8") as f_write:
                        json.dump([], f_write)
                    print_substep("`videos.json` was not a list. Initialized as empty list.", style="bold yellow")
        except json.JSONDecodeError:
            with open(videos_json_path, "w", encoding="utf-8") as f_write:
                json.dump([], f_write)
            print_substep("`videos.json` was corrupted. Initialized as empty list.", style="bold yellow")
    else:
        with open(videos_json_path, "w", encoding="utf-8") as f_write:
            json.dump([], f_write)
        print_substep("`videos.json` not found. Created with empty list.", style="bold yellow")

    for video in done_videos:
        if video["id"] == str(redditobj):
            if settings.config["reddit"]["thread"]["post_id"]:
                print_step(
                    "You already have done this video but since it was declared specifically in the config file the program will continue"
                )
                return redditobj
            print_step("Getting new post as the current one has already been done")
            return None
    return redditobj


def save_data(subreddit: str, filename: str, reddit_title: str, reddit_id: str, credit: str):
    """Saves the videos that have already been generated to a JSON file in video_creation/data/videos.json

    Args:
        filename (str): The finished video title name
        @param subreddit:
        @param filename:
        @param reddit_id:
        @param reddit_title:
    """
    videos_json_path = Path("./video_creation/data/videos.json")
    done_vids = []

    if videos_json_path.exists():
        try:
            with open(videos_json_path, "r", encoding="utf-8") as raw_vids_read:
                loaded_data = json.load(raw_vids_read)
                if isinstance(loaded_data, list):
                    done_vids = loaded_data
        except json.JSONDecodeError:
            pass

    if not isinstance(done_vids, list):
        done_vids = []
        print_substep("Warning: `videos.json` was invalid or empty, initialized as new list for saving.", style="bold yellow")

    if reddit_id in [video["id"] for video in done_vids]:
        return  # video already done but was specified to continue anyway in the config file
    payload = {
        "subreddit": subreddit,
        "id": reddit_id,
        "time": str(int(time.time())),
        "background_credit": credit,
        "reddit_title": reddit_title,
        "filename": filename,
    }
    done_vids.append(payload)
    
    with open(videos_json_path, "w", encoding="utf-8") as raw_vids_write:
        json.dump(done_vids, raw_vids_write, ensure_ascii=False, indent=4)
