import json
import re
from pathlib import Path
from typing import Dict, Final

import translators
from playwright.sync_api import ViewportSize, sync_playwright
from rich.progress import track

from utils import settings
from utils.console import print_step, print_substep
from utils.imagenarator import imagemaker
from utils.playwright import clear_cookie_by_name
from utils.videos import save_data

__all__ = ["get_screenshots_of_reddit_posts"]


def get_screenshots_of_reddit_posts(reddit_object: dict, screenshot_num: int):
    """Downloads screenshots of reddit posts as seen on the web. Downloads to assets/temp/png

    Args:
        reddit_object (Dict): Reddit object received from reddit/subreddit.py
        screenshot_num (int): Number of screenshots to download
    """
    # settings values
    W: Final[int] = int(settings.config["settings"]["resolution_w"])
    H: Final[int] = int(settings.config["settings"]["resolution_h"])
    lang: Final[str] = settings.config["reddit"]["thread"]["post_lang"]
    storymode: Final[bool] = settings.config["settings"]["storymode"]
    read_comment_as_story: Final[bool] = settings.config["settings"]["read_comment_as_story"]

    print_step("Downloading screenshots of reddit posts...")
    reddit_id = re.sub(r"[^\w\s-]", "", reddit_object["thread_id"])
    # ! Make sure the reddit screenshots folder exists
    Path(f"assets/temp/{reddit_id}/png").mkdir(parents=True, exist_ok=True)

    # set the theme and disable non-essential cookies
    if settings.config["settings"]["theme"] == "dark":
        cookie_file = open("./video_creation/data/cookie-dark-mode.json", encoding="utf-8")
        bgcolor = (33, 33, 36, 255)
        txtcolor = (240, 240, 240)
        transparent = False
    elif settings.config["settings"]["theme"] == "transparent":
        # Always use transparent background if theme is transparent
        bgcolor = (0, 0, 0, 0)  # RGBA for fully transparent
        txtcolor = (255, 255, 255) # White text, good for placing over dark video
        transparent = True
        # Use dark mode cookies because Reddit's dark mode (white text)
        # will be more visible when the screenshot/image is overlayed
        # on a video, assuming the video might have dark parts.
        cookie_file = open("./video_creation/data/cookie-dark-mode.json", encoding="utf-8")
    else:
        cookie_file = open("./video_creation/data/cookie-light-mode.json", encoding="utf-8")
        bgcolor = (255, 255, 255, 255)
        txtcolor = (0, 0, 0)
        transparent = False

    if (storymode and settings.config["settings"]["storymodemethod"] == 1) or \
       (not storymode and read_comment_as_story):
        print_substep("Generating images for story-style content...")
        if not isinstance(reddit_object.get("thread_post"), list):
            print_substep("Warning: thread_post is not a list. imagemaker might not work as expected.", style="bold yellow")
            if reddit_object.get("thread_post") is None:
                print_substep("Error: thread_post is None. Cannot generate images.", style="bold red")
                return 
        
        return imagemaker(
            theme=bgcolor,
            reddit_obj=reddit_object,
            txtclr=txtcolor,
            transparent=transparent,
        )

    screenshot_num_actual = screenshot_num

    with sync_playwright() as p:
        print_substep("Launching Headless Browser...")

        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/93.0.4577.63 Safari/537.36",
            viewport=ViewportSize(width=W, height=H),
            device_scale_factor=settings.config["settings"]["zoom"],
        )
        cookies = json.load(cookie_file)
        context.add_cookies(cookies)
        page = context.new_page()
        page.set_default_timeout(0)

        page.goto(reddit_object["thread_url"], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1000)

        if page.locator('[data-testid="content-gate"]').is_visible():
            print_substep("Content gate found. Clicking...", style="yellow")
            page.locator('[data-testid="content-gate"] button').click()
            page.wait_for_timeout(1000) # Wait for gate to disappear

        try:
            if page.locator("button", has_text="See full image").is_visible(timeout=500): # Quick check
                 page.locator("button", has_text="See full image").first.click()
                 page.wait_for_timeout(500)
        except:
            print_substep("No 'See full image' button found or not clickable. Skipping...", style="dim")


        if lang != "":
            # This translation logic might need adjustment based on actual page structure
            # and if it's needed for title screenshots vs comment screenshots.
            # Assuming it's primarily for the main post content if storymode method 0
            print_substep(f"Attempting to translate page content to {lang}...", style="cyan")
            try:
                # Example: Clicking a general translate button if available
                translate_button_selector = '[aria-label="translate"], [data-translate-button]' # Placeholder
                if page.locator(translate_button_selector).is_visible(timeout=1000):
                    page.locator(translate_button_selector).click()
                    page.wait_for_timeout(1000) # Wait for translation
                    # Further steps to select specific language if needed
            except Exception as e:
                print_substep(f"Could not find or click translate button: {e}", style="yellow")


        if storymode: # This means storymode=true and storymodemethod=0 (single post image)
            print_substep("Taking screenshot for storymode (single image)...", style="green")
            # Ensure the main post body is visible and screenshot it
            post_body_selector = '[data-click-id="text"]' # Selector for the main text body of the post
            try:
                page.locator(post_body_selector).first.wait_for(state="visible", timeout=10000)
                page.locator(post_body_selector).first.screenshot(
                    path=f"assets/temp/{reddit_id}/png/story_content.png"
                )
                print_substep(f"Saved story content screenshot to assets/temp/{reddit_id}/png/story_content.png", style="green")
            except Exception as e:
                print_substep(f"Error taking screenshot for story_content.png: {e}", style="bold red")

        else:
            print_substep("Taking screenshots of comments...", style="green")
            clear_cookie_by_name(context, ['loid','session_tracker','csv','edgebucket','token_v2','session','recent_srs'])
            
            comments_on_page = reddit_object["comments"][:screenshot_num_actual]
            if not comments_on_page:
                print_substep("No comments to screenshot.", style="yellow")

            for idx, comment in enumerate(
                track(
                    comments_on_page,
                    "Downloading comment screenshots...",
                )
            ):
                comment_url = f"https://www.reddit.com{comment['comment_url']}"
                comment_id_for_filename = comment['comment_id']
                screenshot_path = f"assets/temp/{reddit_id}/png/{comment_id_for_filename}.png"
                
                print_substep(f"Navigating to comment: {comment_url}",-1)
                try:
                    page.goto(comment_url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(500)

                    comment_selector = f"#t1_{comment_id_for_filename}"
                    
                    comment_element = page.locator(comment_selector).first
                    comment_element.wait_for(state="visible", timeout=10000)
                    
                    comment_element.screenshot(path=screenshot_path)
                    print_substep(f"Saved comment screenshot: {screenshot_path}", 1)

                except Exception as e:
                    print_substep(f"Failed to screenshot comment {comment_id_for_filename}: {e}", style="bold red")

        print_substep("Closing Headless Browser.", style="green")
        browser.close()
    print_step("Finished downloading screenshots.", style="bold green")
