import os
import shutil
from os.path import exists


def cleanup(reddit_id) -> int:
    """Deletes all temporary assets in assets/temp

    Returns:
        int: How many files were deleted
    """
    directory = f"../assets/temp/{reddit_id}/"
    if exists(directory):
        shutil.rmtree(directory)

        return 1
