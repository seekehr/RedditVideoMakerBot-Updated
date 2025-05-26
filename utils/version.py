import requests
from requests.exceptions import JSONDecodeError

from utils.console import print_step


def checkversion(__VERSION__: str):
    try:
        response = requests.get(
            "https://api.github.com/repos/elebumm/RedditVideoMakerBot/releases/latest",
            timeout=5
        )
        response.raise_for_status()

        latestversion_data = response.json()
        latestversion = latestversion_data.get("tag_name")

        if not latestversion:
            print_step("Could not find 'tag_name' in version check response. Skipping version check.")
            return

        if __VERSION__ == latestversion:
            print_step(f"You are using the newest version ({__VERSION__}) of the bot")
        elif __VERSION__ < latestversion:
            print_step(
                f"You are using an older version ({__VERSION__}) of the bot. Download the newest version ({latestversion}) from https://github.com/elebumm/RedditVideoMakerBot/releases/latest"
            )
        else:
            print_step(
                f"Welcome to the test version ({__VERSION__}) of the bot. This version is newer than the latest release ({latestversion}). Thanks for testing and feel free to report any bugs you find."
            )

    except requests.exceptions.RequestException as e:
        print_step(f"Could not connect to GitHub to check for updates: {e}. Skipping version check.")
    except JSONDecodeError:
        print_step("Could not parse version check response from GitHub (not valid JSON). Skipping version check.")
    except KeyError:
        print_step("'tag_name' not found in version check response from GitHub. Skipping version check.")
