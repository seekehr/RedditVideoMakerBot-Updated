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
                        print_substep(f"Post skipped (storymode): Keywords not found in title or selftext. ID: {submission.id}", style="yellow")
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
        
        # Determine if a keyword search was performed on comments because keywords were NOT in the title
        search_keywords = settings.config["reddit"]["thread"].get("search_keywords")
        performing_keyword_comment_search = False
        keyword_matched_comments_list = []

        if (search_keywords and
                settings.config["settings"]["read_comment_as_story"] and
                not any(keyword.lower() in submission.title.lower() for keyword in search_keywords)):
            
            performing_keyword_comment_search = True
            trigger_praw_fallback = False # Initialize here
            print_substep(f"Post title of '{submission.id}' does not contain keywords. Searching its comments for: {search_keywords} using PRAW.", style="cyan")
            
            MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS = settings.config["reddit"]["thread"].get("max_comments_to_scan_for_keywords", 500) # Get from config or default
            
            keyword_matched_comments_list = [] # This will store comment-like dicts

            # Force PRAW fallback
            trigger_praw_fallback = True
            
            if trigger_praw_fallback:
                print_substep("Using PRAW BFS comment traversal for keyword search.", style="yellow")
                # --- Fallback PRAW BFS Logic (copied and adapted from previous version) ---
                MAX_TREE_NODES_TO_PROCESS = MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS * 3
                temp_comments_to_check_praw = []
                processed_tree_nodes = 0
                submission.comments.replace_more(limit=0)
                queue = deque(submission.comments)
                print_substep(f"Starting FALLBACK PRAW BFS for post '{submission.id}' (scan up to {MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS} comments, process up to {MAX_TREE_NODES_TO_PROCESS} tree items).", style="dim")
                while queue and len(temp_comments_to_check_praw) < MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS and processed_tree_nodes < MAX_TREE_NODES_TO_PROCESS:
                    item = queue.popleft()
                    processed_tree_nodes += 1
                    if isinstance(item, praw.models.Comment):
                        temp_comments_to_check_praw.append(item)
                        if len(temp_comments_to_check_praw) < MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS:
                            item.replies.replace_more(limit=0)
                            for reply in item.replies:
                                if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS: queue.append(reply)
                                else: break
                    elif isinstance(item, praw.models.MoreComments):
                        if len(temp_comments_to_check_praw) < MAX_COMMENTS_TO_SCAN_FOR_KEYWORDS:
                            try:
                                more = item.comments()
                                for child_item in more: 
                                    if processed_tree_nodes + len(queue) < MAX_TREE_NODES_TO_PROCESS: queue.append(child_item)
                                    else: break
                                if not more: print_substep(f"Fallback BFS: MoreComments node (id: {item.id if item.id else 'N/A'}) yielded no new comments.", style="dim")
                            except Exception as e_bfs:
                                print_substep(f"Fallback BFS: Error loading MoreComments (id: {item.id if item.id else 'N/A'}): {e_bfs}", style="yellow")
                    if processed_tree_nodes % 100 == 0: print_substep(f"Fallback BFS: Processed {processed_tree_nodes} tree items, collected {len(temp_comments_to_check_praw)} comments...", style="dim")
                if processed_tree_nodes >= MAX_TREE_NODES_TO_PROCESS: print_substep(f"Fallback BFS: Reached processing limit. Collected {len(temp_comments_to_check_praw)} comments.", style="yellow")
                print_substep(f"Fallback BFS: Finished collection. Collected {len(temp_comments_to_check_praw)} comments (processed {processed_tree_nodes} tree items).", style="dim")
                # Filter collected PRAW comments by keyword
                for comment_object in temp_comments_to_check_praw:
                    comment_body_text = getattr(comment_object, 'body', None)
                    if comment_body_text and isinstance(comment_body_text, str):
                        if any(keyword.lower() in comment_body_text.lower() for keyword in search_keywords):
                            # If PRAW object, append directly if compatible, or adapt its fields to the dict structure
                            keyword_matched_comments_list.append({
                                'body': comment_object.body,
                                'id': comment_object.id,
                                'permalink': f"https://www.reddit.com{comment_object.permalink}",
                                'author': getattr(comment_object.author, 'name', None), # PRAW author is an object
                                'stickied': comment_object.stickied # include fields expected by later processing
                            })
                # --- End of Fallback PRAW BFS Logic ---

            if not keyword_matched_comments_list:
                print_substep(f"No comments found matching keywords for post '{submission.id}' (after Pushshift attempt and potential PRAW fallback). Marking as unsuitable.", style="yellow")
                _save_unsuitable_thread_id(current_thread_id, data_dir, unsuitable_thread_ids)
                return None
            
            print_substep(f"Found {len(keyword_matched_comments_list)} comments matching keywords for post '{submission.id}'. Processing these for 'read_comment_as_story'.", style="cyan")

        # Now, process comments based on the mode
        if settings.config["settings"]["read_comment_as_story"]:
            comments_to_iterate = [] # This will now be a list of dicts if from Pushshift, or PRAW objects if from PRAW BFS fallback.
            if performing_keyword_comment_search:
                comments_to_iterate = keyword_matched_comments_list # This list is populated by Pushshift or PRAW BFS
            else: 
                # Standard PRAW comment fetching if not performing_keyword_comment_search for read_comment_as_story
                # This path is taken if keywords WERE in title, or no keywords used for the post.
                # We need to ensure submission.comments are loaded and iterable PRAW Comment objects.
                submission.comments.replace_more(limit=None) # Load all comments and their replies for non-keyword search
                comments_to_iterate = submission.comments.list() 

            for comment_data in comments_to_iterate: # comment_data can be a dict (Pushshift) or PRAW Comment object
                # Adapt access to comment attributes based on its type
                comment_body = None
                comment_id = None
                comment_permalink = None
                comment_author_name = None
                is_stickied = False # Default for Pushshift dicts unless explicitly mapped

                if isinstance(comment_data, praw.models.Comment):
                    if comment_data.body in ["[removed]", "[deleted]"] or comment_data.stickied:
                        continue
                    comment_body = comment_data.body
                    comment_id = comment_data.id
                    comment_permalink = f"https://www.reddit.com{comment_data.permalink}"
                    comment_author_name = getattr(comment_data.author, 'name', None)
                    is_stickied = comment_data.stickied # Already checked above, but good for consistency
                elif isinstance(comment_data, dict): # From Pushshift (or PRAW fallback adapted to dict)
                    comment_body = comment_data.get('body')
                    comment_id = comment_data.get('id')
                    comment_permalink = comment_data.get('permalink')
                    comment_author_name = comment_data.get('author')
                    is_stickied = comment_data.get('stickied', False) # Pushshift data might not always have 'stickied'
                    if comment_body in ["[removed]", "[deleted]"] or is_stickied:
                        continue
                elif isinstance(comment_data, MoreComments): # Should be filtered by PRAW .list() or Pushshift doesn't return these
                    continue 
                else: # Should not happen
                    print_substep(f"Skipping unknown item type in comments_to_iterate: {type(comment_data)}", style="yellow")
                    continue
                
                if not comment_body or not comment_id: # Basic check for valid comment data
                    continue

                sanitised_body = sanitize_text(comment_body)
                blocked_word = contains_swear_word(comment_body)

                if blocked_word:
                    print_substep(f"Comment {comment_id} skipped (read_comment_as_story): Contains a blocked word ('{blocked_word}').", style="yellow")
                    continue
                if not sanitised_body or sanitised_body == " ":
                    continue
                
                if not (int(settings.config["reddit"]["thread"]["min_comment_length"]) <= len(comment_body) <= int(settings.config["reddit"]["thread"]["max_comment_length"])):
                    continue
                # Author check: PRAW author is an object, Pushshift author is a string (name)
                if comment_author_name is None or comment_id in used_comment_ids_for_this_thread:
                    continue

                content["comments"].append({
                    "comment_body": comment_body,
                    "comment_url": comment_permalink, # Ensure this is a full URL
                    "comment_id": comment_id,
                })
                first_suitable_comment_processed = True
                print_substep(f"Selected comment {comment_id} for 'read_comment_as_story'.", style="green")
                
                # Populate thread_post and audio_segments for read_comment_as_story mode
                if content["comments"]: # Should always be true if first_suitable_comment_processed is true
                    selected_comment_text = content["comments"][0]["comment_body"]
                    
                    # Parse the selected comment into sentences and visual chunks
                    parsed_comment_data = posttextparser(selected_comment_text)
                    
                    content["parsed_story_content"] = parsed_comment_data # Used by final_video for visual timing
                    content["thread_post"] = [chunk for item in parsed_comment_data for chunk in item["visual_chunks"]] # Flat list of visual chunks for imagemaker
                    content["audio_segments"] = [item["audio_text"] for item in parsed_comment_data] # List of audio sentences for TTSEngine

                break # Found one suitable comment

            if not first_suitable_comment_processed:
                if performing_keyword_comment_search:
                    print_substep(f"Found {len(keyword_matched_comments_list)} keyword-matching comments, but none were suitable for 'read_comment_as_story'. Marking post unsuitable.", style="yellow")
                else:
                    print_substep(f"No suitable comment found in post '{submission.id}' for 'read_comment_as_story' after checking all comments. Marking as unsuitable.", style="yellow")
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
                    continue

                sanitised_body = sanitize_text(comment.body)
                blocked_word = contains_swear_word(comment.body)
                if blocked_word:
                    print_substep(f"Comment skipped (standard): Contains a blocked word ('{blocked_word}'). ID: {comment.id}", style="yellow")
                    continue
                if not sanitised_body or sanitised_body == " ":
                    continue

                if not (int(settings.config["reddit"]["thread"]["min_comment_length"]) <= len(comment.body) <= int(settings.config["reddit"]["thread"]["max_comment_length"])):
                    continue
                if comment.author is None or comment.id in used_comment_ids_for_this_thread:
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
