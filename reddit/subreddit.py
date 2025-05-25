import re
import json
from pathlib import Path

import praw
from praw.models import MoreComments
from prawcore.exceptions import ResponseException

from utils import settings
from utils.console import print_step, print_substep
from utils.posttextparser import posttextparser
from utils.subreddit import get_subreddit_undone
from utils.videos import check_done
from utils.voice import sanitize_text

# --- Swear Word Filter --- 
SWEAR_WORDS_PATH = Path("swear_words.json")
LOADED_SWEAR_WORDS = []

if SWEAR_WORDS_PATH.exists():
    try:
        with open(SWEAR_WORDS_PATH, "r", encoding="utf-8") as f_swear:
            LOADED_SWEAR_WORDS = json.load(f_swear)
            if not isinstance(LOADED_SWEAR_WORDS, list):
                print_substep(f"Warning: `{SWEAR_WORDS_PATH}` does not contain a valid list. Swear word filter disabled.", style="bold yellow")
                LOADED_SWEAR_WORDS = []
            else:
                LOADED_SWEAR_WORDS = [str(word).lower() for word in LOADED_SWEAR_WORDS] # Normalize to lowercase
    except json.JSONDecodeError:
        print_substep(f"Warning: Could not parse `{SWEAR_WORDS_PATH}`. Swear word filter disabled.", style="bold yellow")
        LOADED_SWEAR_WORDS = []
    except Exception as e:
        print_substep(f"Warning: Error loading `{SWEAR_WORDS_PATH}`: {e}. Swear word filter disabled.", style="bold yellow")
        LOADED_SWEAR_WORDS = []
else:
    print_substep(f"Warning: `{SWEAR_WORDS_PATH}` not found. Swear word filter disabled.", style="bold yellow")

def contains_swear_word(text: str) -> bool:
    if not LOADED_SWEAR_WORDS or not text:
        return False
    text_lower = str(text).lower()
    # Use word boundaries to avoid matching substrings within words
    for word in LOADED_SWEAR_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
            return True
    return False
# --- End Swear Word Filter ---

def get_subreddit_threads(POST_ID: str):
    """
    Returns a list of threads from the AskReddit subreddit.
    """

    print_substep("Logging into Reddit.")

    content = {}
    if settings.config["reddit"]["creds"]["2fa"]:
        print("\nEnter your two-factor authentication code from your authenticator app.\n")
        code = input("> ")
        print()
        pw = settings.config["reddit"]["creds"]["password"]
        passkey = f"{pw}:{code}"
    else:
        passkey = settings.config["reddit"]["creds"]["password"]
    username = settings.config["reddit"]["creds"]["username"]
    if str(username).casefold().startswith("u/"):
        username = username[2:]
    try:
        reddit = praw.Reddit(
            client_id=settings.config["reddit"]["creds"]["client_id"],
            client_secret=settings.config["reddit"]["creds"]["client_secret"],
            user_agent="Accessing Reddit threads",
            username=username,
            passkey=passkey,
            check_for_async=False,
        )
    except ResponseException as e:
        if e.response.status_code == 401:
            print("Invalid credentials - please check them in config.toml")
    except:
        print("Something went wrong...")

    # Ask user for subreddit input
    print_step("Getting subreddit threads...")

    # Load used comment data (thread_id -> list of comment_ids)
    used_content_path = Path("video_creation/data/used_content.json")
    used_content_data = {}  # Initialize as an empty dict

    if used_content_path.exists():
        try:
            with open(used_content_path, "r", encoding="utf-8") as f:
                loaded_json = json.load(f)
                if isinstance(loaded_json, dict):
                    used_content_data = loaded_json
                else:
                    print_substep(f"`{used_content_path}` was not a dictionary. Initializing for thread-specific comment tracking.", style="bold yellow")
                    with open(used_content_path, "w", encoding="utf-8") as f_write:
                        json.dump({}, f_write) # Overwrite with new empty dict structure
        except json.JSONDecodeError:
            print_substep(f"`{used_content_path}` was corrupted. Initializing for thread-specific comment tracking.", style="bold yellow")
            with open(used_content_path, "w", encoding="utf-8") as f_write:
                json.dump({}, f_write) # Overwrite with new empty dict structure
    else:
        with open(used_content_path, "w", encoding="utf-8") as f_write:
            json.dump({}, f_write)
        print_substep(f"`{used_content_path}` not found. Created for thread-specific comment tracking.", style="bold yellow")

    if not settings.config["reddit"]["thread"][
        "subreddit"
    ]:  # note to user. you can have multiple subreddits via reddit.subreddit("redditdev+learnpython")
        try:
            subreddit = reddit.subreddit(
                re.sub(r"r\/", "", input("What subreddit would you like to pull from? "))
                # removes the r/ from the input
            )
        except ValueError:
            subreddit = reddit.subreddit("askreddit")
            print_substep("Subreddit not defined. Using AskReddit.")
    else:
        sub = settings.config["reddit"]["thread"]["subreddit"]
        print_substep(f"Using subreddit: r/{sub} from TOML config")
        subreddit_choice = sub
        if str(subreddit_choice).casefold().startswith("r/"):  # removes the r/ from the input
            subreddit_choice = subreddit_choice[2:]
        subreddit = reddit.subreddit(subreddit_choice)

    if POST_ID:  # would only be called if there are multiple queued posts
        submission = reddit.submission(id=POST_ID)

    elif (
        settings.config["reddit"]["thread"]["post_id"]
        and len(str(settings.config["reddit"]["thread"]["post_id"]).split("+")) == 1
    ):
        submission = reddit.submission(id=settings.config["reddit"]["thread"]["post_id"])
    else:
        search_keywords = settings.config["reddit"]["thread"].get("search_keywords")
        if search_keywords:
            threads = []
            for keyword in search_keywords:
                print_substep(f"Searching for keyword: '{keyword}' in subreddit r/{subreddit_choice}")
                threads.extend(list(subreddit.search(keyword, limit=10))) # Limit to 10 results per keyword
            if not threads:
                print_substep("No posts found for the given keywords.", style="bold red")
                return None
            # --- Swear Word Filter: Added loop to retry if submission is filtered ---
            max_retries = 5 # Limit retries to avoid infinite loops
            for _ in range(max_retries):
                submission = get_subreddit_undone(threads, subreddit)
                if submission is None: # No suitable post found by get_subreddit_undone
                    break 
                
                title_to_check = submission.title
                content_to_check = submission.selftext if settings.config["settings"]["storymode"] else ""
                
                if contains_swear_word(title_to_check):
                    print_substep(f"Post skipped: Title contains a blocked word. ID: {submission.id}", style="yellow")
                    submission = None # Mark for retry / skip
                    threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                    continue
                
                if settings.config["settings"]["storymode"] and contains_swear_word(content_to_check):
                    print_substep(f"Post skipped: Story content contains a blocked word. ID: {submission.id}", style="yellow")
                    submission = None # Mark for retry / skip
                    threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                    continue
                
                break # Found a suitable, non-filtered submission
            # --- End Swear Word Filter Modification ---
        else:
            threads = subreddit.hot(limit=25)
            # --- Swear Word Filter: Added loop to retry if submission is filtered ---
            max_retries = 5 # Limit retries to avoid infinite loops
            for _ in range(max_retries):
                submission = get_subreddit_undone(threads, subreddit)
                if submission is None: # No suitable post found by get_subreddit_undone
                    break 
                
                title_to_check = submission.title
                content_to_check = submission.selftext if settings.config["settings"]["storymode"] else ""
                
                if contains_swear_word(title_to_check):
                    print_substep(f"Post skipped: Title contains a blocked word. ID: {submission.id}", style="yellow")
                    submission = None # Mark for retry / skip
                    threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                    continue
                
                if settings.config["settings"]["storymode"] and contains_swear_word(content_to_check):
                    print_substep(f"Post skipped: Story content contains a blocked word. ID: {submission.id}", style="yellow")
                    submission = None # Mark for retry / skip
                    threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                    continue
                
                break # Found a suitable, non-filtered submission
            # --- End Swear Word Filter Modification ---

    if submission is None:
        print_substep("Could not find a suitable post after checking all time filters.", style="bold red")
        return None # Propagate None if no submission is found
    
    elif not submission.num_comments and settings.config["settings"]["storymode"] == "false":
        print_substep("No comments found. Skipping.")
        exit()

    # Original submission before check_done, in case we need to proceed with it if storymode is false
    original_submission_before_check = submission 
    submission_was_already_done = False

    if settings.config["settings"]["storymode"]:
        submission = check_done(submission)  # storymode always respects videos.json
        if submission is None: # check_done might return None if post was already done and not overridden
            return None # check_done will print the reason
    else: # Not storymode, check_done is only for informational purposes or if we can't find new comments
        temp_submission_check = check_done(submission)
        if temp_submission_check is None: # Indicates post is in videos.json
            submission_was_already_done = True
            # We don't return None here for non-storymode. We'll try to find new comments.
            # The original submission object is preserved in `submission` (or original_submission_before_check)
            print_substep(f"Post ID {submission.id} is in videos.json, but proceeding to check for new comments as storymode is false.", style="magenta")
        # If temp_submission_check is not None, it means the post wasn't in videos.json, or was forced by post_id.
        # In this case, submission remains the original submission object.

    # MOVED BLOCK: Initialize these after all submission validity checks
    current_thread_id = submission.id
    used_comment_ids_for_this_thread = set(used_content_data.get(current_thread_id, []))
    # END MOVED BLOCK

    upvotes = submission.score
    ratio = submission.upvote_ratio * 100
    num_comments = submission.num_comments
    threadurl = f"https://new.reddit.com/{submission.permalink}"

    # Remove text within square brackets from the title
    original_title = submission.title
    cleaned_title = re.sub(r'\[[^\]]*\]', '', original_title).strip()

    print_substep(f"Video will be: {cleaned_title} :thumbsup:", style="bold green")
    print_substep(f"Thread url is: {threadurl} :thumbsup:", style="bold green")
    print_substep(f"Thread has {upvotes} upvotes", style="bold blue")
    print_substep(f"Thread has a upvote ratio of {ratio}%", style="bold blue")
    print_substep(f"Thread has {num_comments} comments", style="bold blue")

    content["thread_url"] = threadurl
    content["thread_title"] = cleaned_title # Use the cleaned title
    content["thread_id"] = submission.id
    content["is_nsfw"] = submission.over_18
    content["comments"] = []
    content["parsed_story_content"] = [] # Initialize new key
    content["audio_segments"] = []    # Initialize new key

    if settings.config["settings"]["storymode"]:
        if settings.config["settings"]["storymodemethod"] == 1:
            # Get parsed content with audio_text and visual_chunks
            parsed_data = posttextparser(submission.selftext)
            content["parsed_story_content"] = parsed_data
            # Flatten visual_chunks for imagemaker into thread_post
            content["thread_post"] = [chunk for item in parsed_data for chunk in item["visual_chunks"]]
            # Extract audio_text for TTS
            content["audio_segments"] = [item["audio_text"] for item in parsed_data]
        else: # storymodemethod == 0
            content["thread_post"] = submission.selftext # Remains as single block for TTS/image
            # For storymodemethod 0, audio_segments can be just the thread_post itself if needed by TTS
            # Or TTS for method 0 directly uses thread_post, this depends on TTS/final_video logic for method 0
            # For simplicity, let audio_segments mirror thread_post for method 0 if it expects a list.
            # However, TTS for method 0 typically handles a single string.
            # Let's assume for now that TTS for storymodemethod 0 will directly use content["thread_post"]
            # and doesn't need content["audio_segments"]. If it does, this part might need adjustment.

    else: # Not storymode
        if settings.config["settings"]["read_first_comment_as_story"]:
            first_comment_processed = False
            for top_level_comment in submission.comments:
                if isinstance(top_level_comment, MoreComments):
                    continue
                if top_level_comment.body in ["[removed]", "[deleted]"]:
                    continue
                if not top_level_comment.stickied:
                    sanitised_body = sanitize_text(top_level_comment.body)
                    if not sanitised_body or sanitised_body.isspace():
                        continue
                    
                    # --- Swear Word Filter for read_first_comment_as_story ---
                    if contains_swear_word(top_level_comment.body):
                        print_substep(f"Comment skipped (read_first_comment_as_story): Contains a blocked word. ID: {top_level_comment.id}", style="yellow")
                        continue
                    # --- End Swear Word Filter ---

                    if len(top_level_comment.body) <= int(
                        settings.config["reddit"]["thread"]["max_comment_length"]
                    ) and len(top_level_comment.body) >= int(
                        settings.config["reddit"]["thread"]["min_comment_length"]
                    ):
                        if top_level_comment.author is not None:
                            parsed_comment_data = posttextparser(top_level_comment.body)
                            content["parsed_story_content"] = parsed_comment_data
                            content["thread_post"] = [chunk for item in parsed_comment_data for chunk in item["visual_chunks"]]
                            content["audio_segments"] = [item["audio_text"] for item in parsed_comment_data]
                            
                            content["comments"].append(
                                {
                                    "comment_body": top_level_comment.body, 
                                    "comment_url": top_level_comment.permalink,
                                    "comment_id": top_level_comment.id,
                                    "is_first_comment_story": True 
                                }
                            )
                            first_comment_processed = True
                            break 
            if not first_comment_processed:
                print_substep("No suitable first comment found to process as story. Skipping post.", style="bold red")
                # If the post was already done and we found no new first comment, then skip
                if submission_was_already_done:
                     print_substep(f"Post {original_submission_before_check.id} was in videos.json and no new suitable first comment found. Skipping.", style="yellow")
                     return None
                # else, it's a new post but just had no suitable first comment, which is also a skip condition
                return None 
        else:
            # Original comment processing logic (no change here for parsed_story_content)
            for top_level_comment in submission.comments:
                if isinstance(top_level_comment, MoreComments):
                    continue

                if top_level_comment.body in ["[removed]", "[deleted]"]:
                    continue  # # see https://github.com/JasonLovesDoggo/RedditVideoMakerBot/issues/78
                if not top_level_comment.stickied:
                    sanitised = sanitize_text(top_level_comment.body)
                    if not sanitised or sanitised == " ":
                        continue
                    
                    # --- Swear Word Filter for standard comments ---
                    if contains_swear_word(top_level_comment.body):
                        print_substep(f"Comment skipped: Contains a blocked word. ID: {top_level_comment.id}", style="yellow")
                        continue
                    # --- End Swear Word Filter ---

                    if len(top_level_comment.body) <= int(
                        settings.config["reddit"]["thread"]["max_comment_length"]
                    ):
                        if len(top_level_comment.body) >= int(
                            settings.config["reddit"]["thread"]["min_comment_length"]
                        ):
                            if (
                                top_level_comment.author is not None
                                and sanitize_text(top_level_comment.body) is not None
                                and top_level_comment.id not in used_comment_ids_for_this_thread # Check against thread-specific list
                            ):  # if errors occur with this change to if not.
                                content["comments"].append(
                                    {
                                        "comment_body": top_level_comment.body,
                                        "comment_url": top_level_comment.permalink,
                                        "comment_id": top_level_comment.id,
                                    }
                                )

    print_substep("Received subreddit threads Successfully.", style="bold green")

    # Save used comment IDs if not in storymode and comments were processed
    if not settings.config["settings"]["storymode"] and content["comments"]:
        newly_processed_comment_ids = {comment["comment_id"] for comment in content["comments"]}
        
        # Get existing used comment IDs for this thread from the main dict
        master_list_for_thread = set(used_content_data.get(current_thread_id, []))
        
        updated_used_ids_for_thread = master_list_for_thread.union(newly_processed_comment_ids)
        
        if updated_used_ids_for_thread: # Only update if there's something to save for this thread
            used_content_data[current_thread_id] = sorted(list(updated_used_ids_for_thread))
            
            with open(used_content_path, "w", encoding="utf-8") as f:
                json.dump(used_content_data, f, indent=4)
            
            num_actually_newly_saved = len(newly_processed_comment_ids - master_list_for_thread)
            if num_actually_newly_saved > 0:
                print_substep(f"Saved {num_actually_newly_saved} new comment ID(s) for thread {current_thread_id} to {used_content_path}.", style="bold blue")
            elif newly_processed_comment_ids: # Comments were processed, but all were already known for this thread
                print_substep(f"All processed comments for thread {current_thread_id} were already in {used_content_path} (or no new comments were found suitable).", style="bold blue")
            # If no comments were processed at all (e.g. all filtered by used_content or swear words)
            # and the post was already in videos.json, then we might consider this a skip.
            elif not newly_processed_comment_ids and submission_was_already_done:
                 print_substep(f"Post {current_thread_id} was in videos.json and no new usable comments were found this time. Skipping save for videos.json to avoid re-logging without new content.", style="yellow")
                 # We don't return the whole content object here, because we don't want main.py to try and make a video
                 # The `save_data` call in `final_video.py` would update `videos.json` again if we returned content.
                 # By returning None, we signal that no video should be made FROM THIS ATTEMPT.
                 return None 

    return content
