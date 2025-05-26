import json
from os.path import exists

from utils import settings

from utils.console import print_substep


def get_subreddit_undone(submissions: list, subreddit, times_checked=0, unsuitable_thread_ids: list = None):
    if times_checked:
        print_substep("Sorting submissions by AI similarity...")

    if not exists("./video_creation/data/videos.json"):
        with open("./video_creation/data/videos.json", "w+") as f:
            json.dump([], f)
    with open("./video_creation/data/videos.json", "r", encoding="utf-8") as done_vids_raw:
        done_videos = json.load(done_vids_raw)

    for submission in submissions:
        if already_done(done_videos, submission):
            continue
        
        if unsuitable_thread_ids and submission.id in unsuitable_thread_ids and not settings.config["settings"]["storymode"]:
            continue

        if submission.over_18:
            try:
                if not settings.config["settings"]["allow_nsfw"]:
                    print_substep("NSFW Post Detected. Skipping...")
                    continue
            except AttributeError:
                print_substep("NSFW settings not defined. Skipping NSFW post...")
        if submission.stickied:
            print_substep("This post was pinned by moderators. Skipping...")
            continue
        
        # New check for max_comments_for_post
        max_comments_setting = settings.config["reddit"]["thread"].get("max_comments_for_post", 0)
        if max_comments_setting > 0 and submission.num_comments > max_comments_setting:
            print_substep(f"Skipping post {submission.id}: Has {submission.num_comments} comments, exceeding max_comments_for_post ({max_comments_setting}).", style="yellow")
            if unsuitable_thread_ids is not None: # Ensure list exists before appending
                unsuitable_thread_ids.append(submission.id) 
            continue
            
        if (
            submission.num_comments <= int(settings.config["reddit"]["thread"]["min_comments"])
            and not settings.config["settings"]["storymode"]
        ):
            unsuitable_thread_ids.append(submission.id) 
            continue
        if settings.config["settings"]["storymode"]:
            if not submission.selftext:
                print_substep("You are trying to use story mode on post with no post text")
                continue
            else:
                if len(submission.selftext) > (
                    settings.config["settings"]["storymode_max_length"] or 2000
                ):
                    print_substep(
                        f"Post is too long ({len(submission.selftext)}), try with a different post. ({settings.config['settings']['storymode_max_length']} character limit)"
                    )
                    continue
                elif len(submission.selftext) < 30:
                    continue
        if settings.config["settings"]["storymode"] and not submission.is_self:
            continue
        return submission

    VALID_TIME_FILTERS = [
        "day",
        "hour",
        "month",
        "week",
        "year",
        "all",
    ]
    index = times_checked + 1
    if index >= len(VALID_TIME_FILTERS):
        return None

    return get_subreddit_undone(
        subreddit.top(
            time_filter=VALID_TIME_FILTERS[index],
            limit=(50 if int(index) == 0 else index + 1 * 50),
        ),
        subreddit,
        times_checked=index,
        unsuitable_thread_ids=unsuitable_thread_ids
    )


def already_done(done_videos: list, submission) -> bool:
    for video in done_videos:
        if video["id"] == str(submission):
            return True
    return False
