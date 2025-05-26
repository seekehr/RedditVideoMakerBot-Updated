from collections import deque
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

def contains_swear_word(text: str) -> str | None:
    if not LOADED_SWEAR_WORDS or not text:
        return None
    text_lower = str(text).lower()
    # Use word boundaries to avoid matching substrings within words
    for word in LOADED_SWEAR_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", text_lower):
            return word # Return the word that was found
    return None
# --- End Swear Word Filter ---

# --- Helper for Unsuitable Threads ---
UNSUITABLE_THREADS_FILENAME = "unsuitable_threads.json"

def _save_unsuitable_thread_id(thread_id: str, data_path_dir: Path, unsuitable_ids_list: list):
    # print_substep is available from module imports
    if not thread_id: 
        print_substep("Attempted to save an empty thread_id to unsuitable list. Skipping.", style="bold yellow")
        return

    path = data_path_dir / UNSUITABLE_THREADS_FILENAME
    if thread_id not in unsuitable_ids_list:
        unsuitable_ids_list.append(thread_id)
        unsuitable_ids_list.sort() 
        try:
            data_path_dir.mkdir(parents=True, exist_ok=True) 
            with open(path, "w", encoding="utf-8") as f_unsuitable:
                json.dump(unsuitable_ids_list, f_unsuitable, indent=4)
            print_substep(f"Thread {thread_id} marked as unsuitable and ID saved to {path}.", style="yellow")
        except IOError as e:
            print_substep(f"Error saving unsuitable thread ID to {path}: {e}", style="bold red")
        except Exception as e: 
            print_substep(f"An unexpected error occurred while saving unsuitable thread ID to {path}: {e}", style="bold red")
# --- End Helper ---

# Add custom exception at the top of the file or within the function if it's only used locally
class PrawFallbackNeeded(Exception):
    pass

def _is_praw_comment_suitable_for_read_story(comment_obj: praw.models.Comment, used_comment_ids: set, settings_config: dict) -> bool:
    """Checks a PRAW Comment object for suitability for 'read_comment_as_story' mode."""
    if comment_obj.stickied or comment_obj.body in ["[removed]", "[deleted]"]:
        print_substep(f"Comment {comment_obj.id} skipped (PRAW suitability check): Stickied or body \'{comment_obj.body}\'.", style="dim")
        return False

    sanitised_body = sanitize_text(comment_obj.body)
    if not sanitised_body or sanitised_body.strip() == "":
        print_substep(f"Comment {comment_obj.id} skipped (PRAW suitability check): Empty after sanitization.", style="dim")
        return False
    
    blocked_word = contains_swear_word(comment_obj.body)
    if blocked_word:
        print_substep(f"Comment {comment_obj.id} skipped (PRAW suitability check): Contains blocked word \'{blocked_word}\'.", style="yellow")
        return False

    min_len = int(settings_config["reddit"]["thread"]["min_comment_length"])
    max_len = int(settings_config["reddit"]["thread"]["max_comment_length"])
    actual_len = len(comment_obj.body)
    if not (min_len <= actual_len <= max_len):
        print_substep(f"Comment {comment_obj.id} skipped (PRAW suitability check): Length {actual_len} not in range ({min_len}-{max_len}).", style="dim")
        return False
    
    author_name = getattr(comment_obj.author, 'name', None)
    if author_name is None or comment_obj.id in used_comment_ids:
        reason = "author is None" if author_name is None else f"ID {comment_obj.id} already used"
        print_substep(f"Comment {comment_obj.id} skipped (PRAW suitability check): {reason}.", style="dim")
        return False
        
    return True

def _is_comment_dict_suitable_for_read_story(comment_dict: dict, used_comment_ids: set, settings_config: dict) -> bool:
    """Checks a comment dictionary (from keyword search) for suitability."""
    comment_body = comment_dict.get('body')
    comment_id = comment_dict.get('id')
    comment_author_name = comment_dict.get('author')
    is_stickied = comment_dict.get('stickied', False)

    if not comment_body or not comment_id:
        print_substep(f"Comment dict (ID: {comment_id if comment_id else 'Unknown'}) skipped: Missing body or ID.", style="dim")
        return False

    if is_stickied or comment_body in ["[removed]", "[deleted]"]:
        print_substep(f"Comment {comment_id} skipped (dict suitability check): Stickied or body \'{comment_body}\'.", style="dim")
        return False

    sanitised_body = sanitize_text(comment_body)
    if not sanitised_body or sanitised_body.strip() == "":
        print_substep(f"Comment {comment_id} skipped (dict suitability check): Empty after sanitization.", style="dim")
        return False
    
    blocked_word = contains_swear_word(comment_body)
    if blocked_word:
        print_substep(f"Comment {comment_id} skipped (dict suitability check): Contains blocked word \'{blocked_word}\'.", style="yellow")
        return False

    min_len = int(settings_config["reddit"]["thread"]["min_comment_length"])
    max_len = int(settings_config["reddit"]["thread"]["max_comment_length"])
    actual_len = len(comment_body)
    if not (min_len <= actual_len <= max_len):
        print_substep(f"Comment {comment_id} skipped (dict suitability check): Length {actual_len} not in range ({min_len}-{max_len}).", style="dim")
        return False
    
    if comment_author_name is None or comment_id in used_comment_ids:
        reason = "author is None" if comment_author_name is None else f"ID {comment_id} already used"
        print_substep(f"Comment {comment_id} skipped (dict suitability check): {reason}.", style="dim")
        return False
        
    return True

def _find_first_suitable_praw_comment_via_bfs(submission: praw.models.Submission, used_comment_ids: set, settings_config: dict) -> praw.models.Comment | None:
    """Performs BFS on submission comments to find the first suitable PRAW comment for read_comment_as_story."""
    MAX_COMMENTS_TO_SCAN = settings_config["reddit"]["thread"].get("max_comments_to_scan_for_keywords", 500)
    MAX_TREE_NODES_TO_PROCESS = MAX_COMMENTS_TO_SCAN * 3 
    
    comments_actually_checked = 0
    processed_tree_nodes = 0
    
    submission.comments.replace_more(limit=0) 
    queue = deque(submission.comments)
    
    print_substep(f"Post {submission.id}: Starting BFS scan for a suitable comment (limit {MAX_COMMENTS_TO_SCAN} comments, {MAX_TREE_NODES_TO_PROCESS} tree items).", style="dim")

    while queue and comments_actually_checked < MAX_COMMENTS_TO_SCAN and processed_tree_nodes < MAX_TREE_NODES_TO_PROCESS:
        item = queue.popleft()
        processed_tree_nodes += 1

        if isinstance(item, praw.models.Comment):
            comments_actually_checked += 1
            if _is_praw_comment_suitable_for_read_story(item, used_comment_ids, settings_config):
                print_substep(f"BFS scan: Found suitable PRAW comment {item.id} after checking {comments_actually_checked} comments.", style="green")
                return item 

            # If not suitable, add its replies to the queue
            item.replies.replace_more(limit=0)
            for reply in item.replies:
                if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS:
                    queue.append(reply)
                else:
                    break 
        
        elif isinstance(item, praw.models.MoreComments):
            try:
                more_comments_from_node = item.comments()
                for child_item in more_comments_from_node:
                    if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS:
                        queue.append(child_item)
                    else:
                        break
            except Exception as e_bfs_scan_more:
                print_substep(f"BFS scan (MoreComments): Error loading children for {item.id if item.id else 'N/A'}: {e_bfs_scan_more}", style="yellow")

        if processed_tree_nodes > 0 and processed_tree_nodes % 200 == 0 : # Adjusted logging frequency
            print_substep(f"BFS scan: Processed {processed_tree_nodes} tree items, checked {comments_actually_checked} comments for suitability...", style="dim")

    if comments_actually_checked >= MAX_COMMENTS_TO_SCAN :
        print_substep(f"BFS scan: Reached limit of {MAX_COMMENTS_TO_SCAN} comments checked for suitability.", style="yellow")
    elif processed_tree_nodes >= MAX_TREE_NODES_TO_PROCESS:
         print_substep(f"BFS scan: Reached limit of {MAX_TREE_NODES_TO_PROCESS} tree nodes processed.", style="yellow")
    else:
        print_substep(f"BFS scan: Finished. No suitable comment found after checking {comments_actually_checked} comments (processed {processed_tree_nodes} tree items).", style="dim")
        
    return None

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

    # Paths for data files
    data_dir = Path("video_creation/data")
    used_comments_path = data_dir / "used_comments.json"
    unsuitable_threads_path = data_dir / UNSUITABLE_THREADS_FILENAME

    # Load used comment data (thread_id -> list of comment_ids)
    used_comments_data = {}

    if used_comments_path.exists():
        try:
            with open(used_comments_path, "r", encoding="utf-8") as f:
                loaded_json = json.load(f)
                if isinstance(loaded_json, dict):
                    used_comments_data = loaded_json
                else:
                    print_substep(f"`{used_comments_path}` was not a dictionary. Initializing for thread-specific comment tracking.", style="bold yellow")
                    data_dir.mkdir(parents=True, exist_ok=True)
                    with open(used_comments_path, "w", encoding="utf-8") as f_write:
                        json.dump({}, f_write) # Overwrite with new empty dict structure
        except json.JSONDecodeError:
            print_substep(f"`{used_comments_path}` was corrupted. Initializing for thread-specific comment tracking.", style="bold yellow")
            data_dir.mkdir(parents=True, exist_ok=True)
            with open(used_comments_path, "w", encoding="utf-8") as f_write:
                json.dump({}, f_write) # Overwrite with new empty dict structure
    else:
        data_dir.mkdir(parents=True, exist_ok=True)
        with open(used_comments_path, "w", encoding="utf-8") as f_write:
            json.dump({}, f_write)
        print_substep(f"`{used_comments_path}` not found. Created for thread-specific comment tracking.", style="bold yellow")

    # Load unsuitable thread IDs
    unsuitable_thread_ids = []
    if unsuitable_threads_path.exists():
        try:
            with open(unsuitable_threads_path, "r", encoding="utf-8") as f_unsuitable:
                loaded_json = json.load(f_unsuitable)
                if isinstance(loaded_json, list):
                    unsuitable_thread_ids = loaded_json
                else:
                    print_substep(f"Warning: `{unsuitable_threads_path}` does not contain a valid list. Initializing anew.", style="bold yellow")
        except json.JSONDecodeError:
            print_substep(f"Warning: Could not parse `{unsuitable_threads_path}`. Initializing anew.", style="bold yellow")
        except Exception as e: # Catch other potential errors like permission issues
            print_substep(f"Warning: Error loading `{unsuitable_threads_path}`: {e}. Initializing anew.", style="bold yellow")
    # The file will be created by _save_unsuitable_thread_id if it doesn't exist and an ID needs to be saved.

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
                submission = get_subreddit_undone(threads, subreddit, unsuitable_thread_ids=unsuitable_thread_ids)
                if submission is None: # No suitable post found by get_subreddit_undone
                    print_substep("No suitable post found from keyword search results after checking undone status.", style="bold red")
                    return None # Changed from break to return None
                
                # Keyword check for storymode (title or selftext)
                if settings.config["settings"]["storymode"] and search_keywords:
                    keyword_found_in_post = False
                    for keyword in search_keywords:
                        if keyword.lower() in submission.title.lower() or keyword.lower() in submission.selftext.lower():
                            keyword_found_in_post = True
                            break
                    if not keyword_found_in_post:
                        print_substep(f"Skipping post {submission.id} (storymode): Keywords '{', '.join(search_keywords)}' not found in title or selftext.", style="yellow")
                        _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
                        submission = None # Mark for retry / skip
                        # Potentially refresh 'threads' or fetch new ones if all keyword-based results are exhausted by this check.
                        # For now, just continue to the next submission in the 'threads' list via get_subreddit_undone in the next iteration.
                        # If 'threads' is exhausted, get_subreddit_undone will return None, handled above.
                        continue # Try next submission from the 'threads' list

                title_to_check = submission.title
                content_to_check = submission.selftext if settings.config["settings"]["storymode"] else ""
                
                blocked_word_title = contains_swear_word(title_to_check)
                if blocked_word_title:
                    print_substep(f"Post skipped: Title contains a blocked word (\'{blocked_word_title}\'). ID: {submission.id}", style="yellow")
                    _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
                    submission = None # Mark for retry / skip
                    # threads = subreddit.hot(limit=25) # Refresh threads for next attempt - Not for keyword search path
                    continue
                
                if settings.config["settings"]["storymode"] and contains_swear_word(content_to_check):
                    print_substep(f"Post skipped: Story content contains a blocked word. ID: {submission.id}", style="yellow")
                    _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
                    submission = None # Mark for retry / skip
                    # threads = subreddit.hot(limit=25) # Refresh threads for next attempt - Not for keyword search path
                    continue
                
                break # Found a suitable, non-filtered submission
            # --- End Swear Word Filter Modification ---
        else:
            threads = subreddit.hot(limit=25)
            # --- Swear Word Filter: Added loop to retry if submission is filtered ---
            max_retries = 5 # Limit retries to avoid infinite loops
            for _ in range(max_retries):
                submission = get_subreddit_undone(threads, subreddit, unsuitable_thread_ids=unsuitable_thread_ids)
                if submission is None: # No suitable post found by get_subreddit_undone
                    break # Ensures this line remains break
                
                # Keyword check for storymode (title or selftext) - This part is for non-keyword search, so it is NOT added here.
                # The check is only relevant when search_keywords are defined.

                title_to_check = submission.title
                content_to_check = submission.selftext if settings.config["settings"]["storymode"] else ""
                
                blocked_word_title = contains_swear_word(title_to_check)
                if blocked_word_title:
                    print_substep(f"Post skipped: Title contains a blocked word (\'{blocked_word_title}\'). ID: {submission.id}", style="yellow")
                    _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
                    submission = None # Mark for retry / skip
                    threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                    continue
                
                if settings.config["settings"]["storymode"]:
                    blocked_word_content = contains_swear_word(content_to_check)
                    if blocked_word_content:
                        print_substep(f"Post skipped: Story content contains a blocked word (\'{blocked_word_content}\'). ID: {submission.id}", style="yellow")
                        _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
                        submission = None # Mark for retry / skip
                        threads = subreddit.hot(limit=25) # Refresh threads for next attempt
                        continue
                
                break # Found a suitable, non-filtered submission
            # --- End Swear Word Filter Modification ---

    if submission is None:
        print_substep("Could not find a suitable post after checking all time filters.", style="bold red")
        return None # Propagate None if no submission is found
    
    # NEW: Check if thread is known unsuitable and storymode is false
    if not settings.config["settings"]["storymode"] and submission.id in unsuitable_thread_ids:
        return None

    # Early check for no comments if not in storymode
    if not settings.config["settings"]["storymode"] and not submission.num_comments:
        print_substep(f"Thread {submission.id} has 0 comments. Marking as unsuitable and skipping.", style="bold red")
        _save_unsuitable_thread_id(submission.id, data_dir, unsuitable_thread_ids)
        return None

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
            print_substep(f"Post ID {submission.id} is in videos.json, but proceeding to check for new comments as storymode is false.", style="magenta")
        # If temp_submission_check is not None, it means the post wasn't in videos.json, or was forced by post_id.
        # In this case, submission remains the original submission object.

    # MOVED BLOCK: Initialize these after all submission validity checks
    # submission object is guaranteed to be valid here if we haven't returned None
    current_thread_id = submission.id
    used_comment_ids_for_this_thread = set(used_comments_data.get(current_thread_id, []))
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

    search_keywords = settings.config["reddit"]["thread"].get("search_keywords")
    if search_keywords:
        found_keywords_in_title = [kw for kw in search_keywords if kw.lower() in submission.title.lower()]
        if found_keywords_in_title:
            print_substep(f"Post title contains keyword(s): {', '.join(found_keywords_in_title)}", style="bold cyan")

    content["thread_url"] = threadurl
    content["thread_title"] = cleaned_title # Use the cleaned title
    content["thread_id"] = submission.id # current_thread_id is the same
    content["is_nsfw"] = submission.over_18
    content["comments"] = []
    content["parsed_story_content"] = [] # Initialize new key
    content["audio_segments"] = []    # Initialize new key

    if settings.config["settings"]["storymode"]:
        if settings.config["settings"]["storymodemethod"] == 1:
            # Get parsed content with audio_text and visual_chunks
            parsed_data = posttextparser(submission.selftext)
            content["parsed_story_content"] = parsed_data
            content["thread_post"] = [chunk for item in parsed_data for chunk in item["visual_chunks"]]
            content["audio_segments"] = [item["audio_text"] for item in parsed_data]
        else: # storymodemethod == 0
            content["thread_post"] = submission.selftext
    else: # Not storymode - This is where comment processing logic begins
        first_suitable_comment_processed = False
        
        search_keywords = settings.config["reddit"]["thread"].get("search_keywords")
        performing_keyword_comment_search = False
        keyword_matched_comments_list = [] # List of dicts from PRAW BFS keyword search

        if (search_keywords and
                settings.config["settings"]["read_comment_as_story"] and
                not any(keyword.lower() in submission.title.lower() for keyword in search_keywords)):
            
            performing_keyword_comment_search = True
            # This block is for when keywords are NOT in title, and we search comments FOR keywords
            print_substep(f"Post title of \'{submission.id}\' does not contain keywords. Searching its comments for: {search_keywords} using PRAW BFS.", style="cyan")
            
            MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS = settings.config["reddit"]["thread"].get("max_comments_to_scan_for_keywords", 500)
            MAX_TREE_NODES_TO_PROCESS = MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS * 3
            temp_comments_to_check_praw = [] # PRAW objects collected by BFS
            processed_tree_nodes = 0
            
            submission.comments.replace_more(limit=0)
            queue = deque(submission.comments)
            print_substep(f"Starting PRAW BFS for keyword search in post \'{submission.id}\' (scan up to {MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS} comments, process up to {MAX_TREE_NODES_TO_PROCESS} tree items).", style="dim")
            
            comments_collected_by_bfs = 0
            while queue and comments_collected_by_bfs < MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS and processed_tree_nodes < MAX_TREE_NODES_TO_PROCESS:
                item = queue.popleft()
                processed_tree_nodes += 1
                if isinstance(item, praw.models.Comment):
                    temp_comments_to_check_praw.append(item)
                    comments_collected_by_bfs +=1
                    # Add replies to queue
                    item.replies.replace_more(limit=0)
                    for reply in item.replies:
                        if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS: queue.append(reply)
                        else: break
                elif isinstance(item, praw.models.MoreComments):
                    try:
                        more = item.comments()
                        for child_item in more: 
                            if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS: queue.append(child_item)
                            else: break
                    except Exception as e_bfs:
                        print_substep(f"PRAW BFS (keyword search): Error loading MoreComments (id: {item.id if item.id else 'N/A'}): {e_bfs}", style="yellow")
                if processed_tree_nodes > 0 and processed_tree_nodes % 200 == 0: print_substep(f"PRAW BFS (keyword search): Processed {processed_tree_nodes} tree items, collected {len(temp_comments_to_check_praw)} comments...", style="dim")
            
            if comments_collected_by_bfs >= MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS : print_substep(f"PRAW BFS (keyword search): Reached collection limit of {MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS} comments.", style="yellow")    
            elif processed_tree_nodes >= MAX_TREE_NODES_TO_PROCESS: print_substep(f"PRAW BFS (keyword search): Reached tree processing limit. Collected {len(temp_comments_to_check_praw)} comments.", style="yellow")
            print_substep(f"PRAW BFS (keyword search): Finished. Collected {len(temp_comments_to_check_praw)} comments (processed {processed_tree_nodes} tree items). Filtering by keyword...", style="dim")

            for comment_object in temp_comments_to_check_praw:
                comment_body_text = getattr(comment_object, 'body', None)
                if comment_body_text and isinstance(comment_body_text, str):
                    if any(keyword.lower() in comment_body_text.lower() for keyword in search_keywords):
                        keyword_matched_comments_list.append({
                            'body': comment_object.body,
                            'id': comment_object.id,
                            'permalink': f"https://www.reddit.com{comment_object.permalink}",
                            'author': getattr(comment_object.author, 'name', None),
                            'stickied': comment_object.stickied
                        })
            
            if not keyword_matched_comments_list:
                print_substep(f"Skipping post \'{submission.id}\': No comments found matching keywords using PRAW BFS.", style="yellow")
                _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids)
                return None
            print_substep(f"Found {len(keyword_matched_comments_list)} comments matching keywords for post \'{submission.id}\'. Processing these for \'read_comment_as_story\'.", style="cyan")

        # Now, process comments based on the mode for read_comment_as_story
        if settings.config["settings"]["read_comment_as_story"]:
            suitable_comment_for_story = None 

            if performing_keyword_comment_search:
                # keyword_matched_comments_list has dicts. Find first suitable among them.
                for comment_dict_candidate in keyword_matched_comments_list:
                    if _is_comment_dict_suitable_for_read_story(comment_dict_candidate, used_comment_ids_for_this_thread, settings.config):
                        suitable_comment_for_story = comment_dict_candidate
                        break 
            else:
                # Keywords ARE in title (or no keywords for post).
                # Use BFS to find the first suitable PRAW comment object.
                suitable_comment_for_story = _find_first_suitable_praw_comment_via_bfs(
                    submission,
                    used_comment_ids_for_this_thread,
                    settings.config
                )

            if suitable_comment_for_story:
                final_comment_body = None
                final_comment_id = None
                final_comment_permalink = None

                if isinstance(suitable_comment_for_story, praw.models.Comment): # From BFS scan
                    final_comment_body = suitable_comment_for_story.body
                    final_comment_id = suitable_comment_for_story.id
                    final_comment_permalink = f"https://www.reddit.com{suitable_comment_for_story.permalink}"
                elif isinstance(suitable_comment_for_story, dict): # From keyword-matched list
                    final_comment_body = suitable_comment_for_story.get('body')
                    final_comment_id = suitable_comment_for_story.get('id')
                    final_comment_permalink = suitable_comment_for_story.get('permalink')
                
                if final_comment_body and final_comment_id: # Ensure we have critical info
                    content["comments"].append({
                        "comment_body": final_comment_body,
                        "comment_url": final_comment_permalink,
                        "comment_id": final_comment_id,
                    })
                    first_suitable_comment_processed = True
                    print_substep(f"Selected comment {final_comment_id} for \'read_comment_as_story\'.", style="green")
                    
                    parsed_comment_data = posttextparser(final_comment_body)
                    content["parsed_story_content"] = parsed_comment_data
                    content["thread_post"] = [chunk for item in parsed_comment_data for chunk in item["visual_chunks"]]
                    content["audio_segments"] = [item["audio_text"] for item in parsed_comment_data]
                else:
                    print_substep(f"Failed to extract details from suitable_comment_for_story. Type: {type(suitable_comment_for_story)}", style="bold red")


            if not first_suitable_comment_processed:
                if performing_keyword_comment_search:
                    print_substep(f"Skipping post \'{submission.id}\': {len(keyword_matched_comments_list)} keyword-matching comments found, but none were suitable for \'read_comment_as_story\'.", style="yellow")
                else: # Keyword in title or no keywords for post, BFS scan path
                    print_substep(f"Skipping post \'{submission.id}\': No suitable comment found for \'read_comment_as_story\' after BFS scan.", style="yellow")
                _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids)
                return None
        
        else: # Standard comment processing (not read_comment_as_story)
            # Ensure all comments are loaded if not already a list
            comments_for_standard_processing = []
            if not isinstance(submission.comments, list):
                submission.comments.replace_more(limit=None) # Loads all comments.
                comments_for_standard_processing = submission.comments.list()
            else:
                comments_for_standard_processing = submission.comments
                
            for comment in comments_for_standard_processing:
                if isinstance(comment, MoreComments):
                    continue
                if comment.body in ["[removed]", "[deleted]"] or comment.stickied:
                    print_substep(f"Comment {comment.id} skipped: Body is '{comment.body}' or comment is stickied.", style="dim")
                    continue

                sanitised_body = sanitize_text(comment.body)
                blocked_word = contains_swear_word(comment.body)
                if blocked_word:
                    print_substep(f"Comment skipped (standard): Contains a blocked word ('{blocked_word}'). ID: {comment.id}", style="yellow")
                    continue
                if not sanitised_body or sanitised_body == " ":
                    print_substep(f"Comment {comment.id} skipped: Empty after sanitization.", style="dim")
                    continue

                min_len = int(settings.config["reddit"]["thread"]["min_comment_length"])
                max_len = int(settings.config["reddit"]["thread"]["max_comment_length"])
                actual_len = len(comment.body)
                if not (min_len <= actual_len <= max_len):
                    print_substep(f"Comment {comment.id} skipped: Length {actual_len} is outside range ({min_len}-{max_len}).", style="dim")
                    continue
                if comment.author is None or comment.id in used_comment_ids_for_this_thread:
                    reason = "author is None" if comment.author is None else f"comment ID {comment.id} already used"
                    print_substep(f"Comment {comment.id} skipped: {reason}.", style="dim")
                    continue
                
                content["comments"].append({
                    "comment_body": comment.body,
                    "comment_url": comment.permalink,
                    "comment_id": comment.id,
                })
            
            if not content["comments"]:
                print_substep(f"No suitable comments found for thread {current_thread_id} after standard processing. Skipping post.", style="bold red")
                if submission_was_already_done:
                     print_substep(f"Post {current_thread_id} was already in videos.json and no suitable comments were found this run.", style="yellow")
                _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids)
                return None

    print_substep("Received subreddit threads Successfully.", style="bold green")

    if not settings.config["settings"]["storymode"] and content["comments"]:
        newly_processed_comment_ids = {comment["comment_id"] for comment in content["comments"]}
        master_list_for_thread = set(used_comments_data.get(current_thread_id, []))
        updated_used_ids_for_thread = master_list_for_thread.union(newly_processed_comment_ids)
        
        if updated_used_ids_for_thread:
            used_comments_data[current_thread_id] = sorted(list(updated_used_ids_for_thread))
            data_dir.mkdir(parents=True, exist_ok=True)
            with open(used_comments_path, "w", encoding="utf-8") as f:
                json.dump(used_comments_data, f, indent=4)
            
            num_actually_newly_saved = len(newly_processed_comment_ids - master_list_for_thread)
            if num_actually_newly_saved > 0:
                print_substep(f"Saved {num_actually_newly_saved} new comment ID(s) for thread {current_thread_id} to {used_comments_path}.", style="bold blue")
            elif newly_processed_comment_ids: # This means num_actually_newly_saved is 0, but there were comments processed (they were all old)
                print_substep(f"All processed comments for thread {current_thread_id} were already in {used_comments_path}. Marking as unsuitable.", style="bold blue")
                _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids) # Add to unsuitable if all comments were old
                return None # Skip the post
            elif not newly_processed_comment_ids and submission_was_already_done:
                 print_substep(f"Post {current_thread_id} was in videos.json and no new usable comments were found this time. Skipping save for videos.json to avoid re-logging without new content.", style="yellow")
                 _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids) # Also mark as unsuitable here
                 return None 

    return content
