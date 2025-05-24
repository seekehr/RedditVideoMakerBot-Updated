import multiprocessing
import os
import re
import tempfile
import textwrap
import threading
import time
from os.path import exists  # Needs to be imported specifically
from pathlib import Path
from typing import Dict, Final, Tuple

import ffmpeg
import translators
from PIL import Image, ImageDraw, ImageFont
from rich.console import Console
from rich.progress import track

from utils import settings
from utils.cleanup import cleanup
from utils.console import print_step, print_substep
from utils.fonts import getheight
from utils.thumbnail import create_thumbnail
from utils.videos import save_data

console = Console()


class ProgressFfmpeg(threading.Thread):
    def __init__(self, vid_duration_seconds, progress_update_callback):
        threading.Thread.__init__(self, name="ProgressFfmpeg")
        self.stop_event = threading.Event()
        self.output_file = tempfile.NamedTemporaryFile(mode="w+", delete=False)
        self.vid_duration_seconds = vid_duration_seconds
        self.progress_update_callback = progress_update_callback

    def run(self):
        while not self.stop_event.is_set():
            latest_progress = self.get_latest_ms_progress()
            if latest_progress is not None:
                completed_percent = latest_progress / self.vid_duration_seconds
                self.progress_update_callback(completed_percent)
            time.sleep(1)

    def get_latest_ms_progress(self):
        lines = self.output_file.readlines()

        if lines:
            for line in lines:
                if "out_time_ms" in line:
                    out_time_ms_str = line.split("=")[1].strip()
                    if out_time_ms_str.isnumeric():
                        return float(out_time_ms_str) / 1000000.0
                    else:
                        # Handle the case when "N/A" is encountered
                        return None
        return None

    def stop(self):
        self.stop_event.set()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args, **kwargs):
        self.stop()


def name_normalize(name: str) -> str:
    name = re.sub(r'[?\\"%*:|<>]', "", name)
    name = re.sub(r"( [w,W]\s?\/\s?[o,O,0])", r" without", name)
    name = re.sub(r"( [w,W]\s?\/)", r" with", name)
    name = re.sub(r"(\d+)\s?\/\s?(\d+)", r"\1 of \2", name)
    name = re.sub(r"(\w+)\s?\/\s?(\w+)", r"\1 or \2", name)
    name = re.sub(r"\/", r"", name)

    lang = settings.config["reddit"]["thread"]["post_lang"]
    if lang:
        print_substep("Translating filename...")
        translated_name = translators.translate_text(name, translator="google", to_language=lang)
        return translated_name
    else:
        return name


def prepare_background(reddit_id: str, W: int, H: int) -> str:
    output_path = f"assets/temp/{reddit_id}/background_noaudio.mp4"
    output = (
        ffmpeg.input(f"assets/temp/{reddit_id}/background.mp4")
        .filter("crop", f"ih*({W}/{H})", "ih")
        .output(
            output_path,
            an=None,
            **{
                "c:v": "h264",
                "b:v": "20M",
                "b:a": "192k",
                "threads": multiprocessing.cpu_count(),
            },
        )
        .overwrite_output()
    )
    try:
        output.run(quiet=True)
    except ffmpeg.Error as e:
        print(e.stderr.decode("utf8"))
        exit(1)
    return output_path


def create_fancy_thumbnail(image, text, text_color, padding, wrap=35):
    print_step(f"Creating fancy thumbnail for: {text}")
    font_title_size = 47
    font = ImageFont.truetype(os.path.join("fonts", "Roboto-Bold.ttf"), font_title_size)
    image_width, image_height = image.size
    lines = textwrap.wrap(text, width=wrap)
    y = (
        (image_height / 2)
        - (((getheight(font, text) + (len(lines) * padding) / len(lines)) * len(lines)) / 2)
        + 30
    )
    draw = ImageDraw.Draw(image)

    username_font = ImageFont.truetype(os.path.join("fonts", "Roboto-Bold.ttf"), 30)
    draw.text(
        (205, 825),
        settings.config["settings"]["channel_name"],
        font=username_font,
        fill=text_color,
        align="left",
    )

    if len(lines) == 3:
        lines = textwrap.wrap(text, width=wrap + 10)
        font_title_size = 40
        font = ImageFont.truetype(os.path.join("fonts", "Roboto-Bold.ttf"), font_title_size)
        y = (
            (image_height / 2)
            - (((getheight(font, text) + (len(lines) * padding) / len(lines)) * len(lines)) / 2)
            + 35
        )
    elif len(lines) == 4:
        lines = textwrap.wrap(text, width=wrap + 10)
        font_title_size = 35
        font = ImageFont.truetype(os.path.join("fonts", "Roboto-Bold.ttf"), font_title_size)
        y = (
            (image_height / 2)
            - (((getheight(font, text) + (len(lines) * padding) / len(lines)) * len(lines)) / 2)
            + 40
        )
    elif len(lines) > 4:
        lines = textwrap.wrap(text, width=wrap + 10)
        font_title_size = 30
        font = ImageFont.truetype(os.path.join("fonts", "Roboto-Bold.ttf"), font_title_size)
        y = (
            (image_height / 2)
            - (((getheight(font, text) + (len(lines) * padding) / len(lines)) * len(lines)) / 2)
            + 30
        )

    for line in lines:
        draw.text((120, y), line, font=font, fill=text_color, align="left")
        y += getheight(font, line) + padding

    return image


def merge_background_audio(audio: ffmpeg, reddit_id: str):
    """Gather an audio and merge with assets/backgrounds/background.mp3
    Args:
        audio (ffmpeg): The TTS final audio but without background.
        reddit_id (str): The ID of subreddit
    """
    background_audio_volume = settings.config["settings"]["background"]["background_audio_volume"]
    if background_audio_volume == 0:
        return audio  # Return the original audio
    else:
        # sets volume to config
        bg_audio = ffmpeg.input(f"assets/temp/{reddit_id}/background.mp3").filter(
            "volume",
            background_audio_volume,
        )
        # Merges audio and background_audio
        merged_audio = ffmpeg.filter([audio, bg_audio], "amix", duration="longest")
        return merged_audio  # Return merged audio


def make_final_video(
    number_of_clips: int,
    length: int,
    reddit_obj: dict,
    background_config: Dict[str, Tuple],
):
    """Gathers audio clips, gathers all screenshots, stitches them together and saves the final video to assets/temp
    Args:
        number_of_clips (int): Index to end at when going through the screenshots'
        length (int): Length of the video
        reddit_obj (dict): The reddit object that contains the posts to read.
        background_config (Tuple[str, str, str, Any]): The background config to use.
    """
    # settings values
    W: Final[int] = int(settings.config["settings"]["resolution_w"])
    H: Final[int] = int(settings.config["settings"]["resolution_h"])

    opacity = settings.config["settings"]["opacity"]
    storymode_enabled: bool = settings.config["settings"]["storymode"]
    read_first_comment_as_story_enabled: bool = settings.config["settings"]["read_first_comment_as_story"]

    reddit_id = re.sub(r"[^\w\s-]", "", reddit_obj["thread_id"])

    allowOnlyTTSFolder: bool = (
        settings.config["settings"]["background"]["enable_extra_audio"]
        and settings.config["settings"]["background"]["background_audio_volume"] != 0
    )

    print_step("Creating the final video 🎥")

    background_clip = ffmpeg.input(prepare_background(reddit_id, W=W, H=H))

    # Gather all audio clips
    audio_clips = list()
    # number_of_clips from TTSEngine now refers to number of primary audio segments (sentences/comments)

    if storymode_enabled:
        if settings.config["settings"]["storymodemethod"] == 0:
            # Storymode Method 0: Title audio + single post audio (postaudio.mp3)
            audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/title.mp3"))
            if exists(f"assets/temp/{reddit_id}/mp3/postaudio.mp3"):
                audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/postaudio.mp3"))
            else:
                print_substep("Warning: postaudio.mp3 not found for storymode method 0.", style="yellow")
        
        elif settings.config["settings"]["storymodemethod"] == 1:
            # Storymode Method 1: Title audio + multiple audio_segment-N.mp3 files
            audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/title.mp3"))
            num_audio_segments = len(reddit_obj.get("audio_segments", []))
            for i in range(num_audio_segments):
                audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/audio_segment-{i}.mp3"))

    elif read_first_comment_as_story_enabled:
        # Read first comment as story: Title audio + multiple audio_segment-N.mp3 for the first comment
        audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/title.mp3"))
        num_audio_segments_comment = len(reddit_obj.get("audio_segments", [])) # These are sentences of the 1st comment
        for i in range(num_audio_segments_comment):
            audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/audio_segment-{i}.mp3"))
        # Note: The concatenated 0.mp3 for the first comment is used for the main audio track,
        # but the individual audio_segment-i.mp3 durations are needed for visual timing.

    else:
        # Standard comment processing (screenshots, not story-like)
        # number_of_clips here is actual number of comment mp3s (0.mp3, 1.mp3, ...)
        if number_of_clips == 0:
             print("No audio clips to gather for standard mode (number_of_clips is 0).")
             # exit() or handle as error
        audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/title.mp3"))
        for i in range(number_of_clips): # Assumes 0.mp3, 1.mp3 ... exist
            audio_clips.append(ffmpeg.input(f"assets/temp/{reddit_id}/mp3/{i}.mp3"))

    if not audio_clips:
        print_substep("CRITICAL: No audio clips were gathered. Video generation cannot proceed.", style="bold red")
        # Consider exiting or raising an error here
        return # or exit()
    
    # Create the main concatenated audio track for the video
    # For read_first_comment_as_story, TTS already created 0.mp3 which is the full comment audio.
    # We need to decide if audio_concat should use that, or concat title + audio_segments.
    # For simplicity and consistency with storymode method 1, let's concat title + audio_segments.
    # The final audio mix with background music will use this.
    if audio_clips: # Ensure there are clips before attempting concat
        audio_concat = ffmpeg.concat(*audio_clips, a=1, v=0)
        ffmpeg.output(
            audio_concat, f"assets/temp/{reddit_id}/audio.mp3", **{"b:a": "192k"}
        ).overwrite_output().run(quiet=True)
    else:
        print_substep("No audio clips to concatenate for main audio track.", style="red")
        # Handle error: perhaps create silent audio of `length`? For now, we might crash later.

    console.log(f"[bold green] Video Will Be: {length} Seconds Long")

    screenshot_width = int((W * 45) // 100)
    audio = ffmpeg.input(f"assets/temp/{reddit_id}/audio.mp3")
    final_audio = merge_background_audio(audio, reddit_id)

    image_clips = list()

    Path(f"assets/temp/{reddit_id}/png").mkdir(parents=True, exist_ok=True)

    # Credits to tim (beingbored)
    # get the title_template image and draw a text in the middle part of it with the title of the thread
    title_template = Image.open("assets/title_template.png")

    title = reddit_obj["thread_title"]

    title = name_normalize(title)

    font_color = "#000000"
    padding = 5

    # create_fancy_thumbnail(image, text, text_color, padding
    title_img = create_fancy_thumbnail(title_template, title, font_color, padding)

    title_img.save(f"assets/temp/{reddit_id}/png/title.png")
    image_clips.insert(
        0,
        ffmpeg.input(f"assets/temp/{reddit_id}/png/title.png")["v"].filter(
            "scale", screenshot_width, -1
        ),
    )

    current_time = 0
    # Get title duration first
    title_audio_duration = float(ffmpeg.probe(f"assets/temp/{reddit_id}/mp3/title.mp3")["format"]["duration"])
    background_clip = background_clip.overlay(
        image_clips[0], # title.png
        enable=f"between(t,0,{title_audio_duration})",
        x="(main_w-overlay_w)/2",
        y="(main_h-overlay_h)/2",
    )
    current_time += title_audio_duration

    if storymode_enabled and settings.config["settings"]["storymodemethod"] == 1:
        parsed_story_content = reddit_obj.get("parsed_story_content", [])
        global_visual_chunk_idx = 0 # To index into the flat list of all visual chunks (img0.png, img1.png ...)
        
        for j, audio_segment_info in enumerate(parsed_story_content):
            audio_segment_duration = float(ffmpeg.probe(f"assets/temp/{reddit_id}/mp3/audio_segment-{j}.mp3")["format"]["duration"])
            visual_chunks_for_this_audio_segment = audio_segment_info.get("visual_chunks", [])
            num_visual_chunks = len(visual_chunks_for_this_audio_segment)

            if num_visual_chunks == 0: continue # Should not happen if posttextparser is correct

            time_per_visual_chunk = audio_segment_duration / num_visual_chunks

            for vc_idx in range(num_visual_chunks):
                # Load the corresponding visual chunk image (img0.png, img1.png, ...)
                # These are already created by imagemaker based on the flat list from content["thread_post"]
                img_path = f"assets/temp/{reddit_id}/png/img{global_visual_chunk_idx}.png"
                if not exists(img_path):
                    print_substep(f"Warning: Visual chunk image not found: {img_path}", style="yellow")
                    current_time += time_per_visual_chunk # Still advance time
                    global_visual_chunk_idx += 1
                    continue
                
                # Add to image_clips list if not already (though it might be better to load on demand)
                # For simplicity, let's assume they are pre-loaded if this path is taken or load them dynamically.
                # Let's try dynamic loading here for overlay to avoid managing a large image_clips list index for visuals.
                visual_chunk_image = ffmpeg.input(img_path)["v"].filter("scale", screenshot_width, -1)
                
                overlay_start_time = current_time
                overlay_end_time = current_time + time_per_visual_chunk
                
                # Apply opacity filter only if the theme is not transparent
                # (because if it is, imagemaker already made a transparent BG for the text)
                processed_visual_chunk_image = visual_chunk_image
                if settings.config["settings"]["theme"] != "transparent":
                    processed_visual_chunk_image = visual_chunk_image.filter("colorchannelmixer", aa=opacity)

                background_clip = background_clip.overlay(
                    processed_visual_chunk_image,
                    enable=f"between(t,{overlay_start_time},{overlay_end_time})",
                    x="(main_w-overlay_w)/2",
                    y="(main_h-overlay_h)/2",
                )
                current_time = overlay_end_time # Advance current time by the duration of this visual chunk
                global_visual_chunk_idx += 1

    elif read_first_comment_as_story_enabled:
        # Similar logic for the first comment being read as a story
        parsed_story_content = reddit_obj.get("parsed_story_content", []) # This is for the first comment
        global_visual_chunk_idx = 0 

        for j, audio_segment_info in enumerate(parsed_story_content):
            # audio_segment-j.mp3 corresponds to the j-th sentence of the first comment
            audio_segment_duration = float(ffmpeg.probe(f"assets/temp/{reddit_id}/mp3/audio_segment-{j}.mp3")["format"]["duration"])
            visual_chunks_for_this_audio_segment = audio_segment_info.get("visual_chunks", [])
            num_visual_chunks = len(visual_chunks_for_this_audio_segment)

            if num_visual_chunks == 0: continue
            time_per_visual_chunk = audio_segment_duration / num_visual_chunks

            for vc_idx in range(num_visual_chunks):
                img_path = f"assets/temp/{reddit_id}/png/img{global_visual_chunk_idx}.png"
                if not exists(img_path):
                    print_substep(f"Warning: Visual chunk image not found: {img_path}", style="yellow")
                    current_time += time_per_visual_chunk
                    global_visual_chunk_idx += 1
                    continue
                
                visual_chunk_image = ffmpeg.input(img_path)["v"].filter("scale", screenshot_width, -1)
                overlay_start_time = current_time
                overlay_end_time = current_time + time_per_visual_chunk
                
                processed_visual_chunk_image_comment = visual_chunk_image
                if settings.config["settings"]["theme"] != "transparent":
                    processed_visual_chunk_image_comment = visual_chunk_image.filter("colorchannelmixer", aa=opacity)

                background_clip = background_clip.overlay(
                    processed_visual_chunk_image_comment,
                    enable=f"between(t,{overlay_start_time},{overlay_end_time})",
                    x="(main_w-overlay_w)/2",
                    y="(main_h-overlay_h)/2",
                )
                current_time = overlay_end_time
                global_visual_chunk_idx += 1

    elif storymode_enabled and settings.config["settings"]["storymodemethod"] == 0:
        # Storymode Method 0: Single post image (story_content.png) with its audio (postaudio.mp3)
        if exists(f"assets/temp/{reddit_id}/mp3/postaudio.mp3") and exists(f"assets/temp/{reddit_id}/png/story_content.png"):
            post_audio_duration = float(ffmpeg.probe(f"assets/temp/{reddit_id}/mp3/postaudio.mp3")["format"]["duration"])
            post_image = ffmpeg.input(f"assets/temp/{reddit_id}/png/story_content.png")["v"].filter("scale", screenshot_width, -1)
            
            overlay_start_time = current_time
            overlay_end_time = current_time + post_audio_duration

            processed_post_image = post_image
            if settings.config["settings"]["theme"] != "transparent": # Though storymode method 0 usually uses dark/light actual screenshots
                processed_post_image = post_image.filter("colorchannelmixer", aa=opacity)

            background_clip = background_clip.overlay(
                processed_post_image,
                enable=f"between(t,{overlay_start_time},{overlay_end_time})",
                x="(main_w-overlay_w)/2",
                y="(main_h-overlay_h)/2",
            )
            current_time = overlay_end_time
        else:
            print_substep("Warning: Audio or image missing for storymode method 0.", style="yellow")

    else: # Standard comment screenshot mode
        # This assumes 0.mp3, 1.mp3... and corresponding comment_ID.png exist
        audio_clips_durations_standard = []
        # number_of_clips is from TTS, should be count of actual comment audios (0.mp3, 1.mp3...)
        for i in range(number_of_clips):
            audio_path = f"assets/temp/{reddit_id}/mp3/{i}.mp3"
            if exists(audio_path):
                audio_clips_durations_standard.append(float(ffmpeg.probe(audio_path)["format"]["duration"])) 
            else:
                print_substep(f"Warning: Comment audio {audio_path} not found.", style="yellow")
                audio_clips_durations_standard.append(0) # Append 0 duration if audio missing

        for i in range(number_of_clips):
            actual_comment_id = reddit_obj["comments"][i]["comment_id"]
            img_path = f"assets/temp/{reddit_id}/png/{actual_comment_id}.png"
            if not exists(img_path):
                print_substep(f"Warning: Comment image not found: {img_path}", style="yellow")
                current_time += audio_clips_durations_standard[i] # Advance time by audio duration even if image missing
                continue

            comment_image = ffmpeg.input(img_path)["v"].filter("scale", screenshot_width, -1)
            overlay_start_time = current_time
            overlay_end_time = current_time + audio_clips_durations_standard[i]

            # Standard comments are screenshots, so opacity applies to the whole screenshot box
            processed_comment_image = comment_image.filter("colorchannelmixer", aa=opacity)

            background_clip = background_clip.overlay(
                processed_comment_image,
                enable=f"between(t,{overlay_start_time},{overlay_end_time})",
                x="(main_w-overlay_w)/2",
                y="(main_h-overlay_h)/2",
            )
            current_time = overlay_end_time

    title = re.sub(r"[^\w\s-]", "", reddit_obj["thread_title"])
    idx = re.sub(r"[^\w\s-]", "", reddit_obj["thread_id"])
    title_thumb = reddit_obj["thread_title"]

    filename = f"{name_normalize(title)[:251]}"
    subreddit = settings.config["reddit"]["thread"]["subreddit"]

    if not exists(f"./results/{subreddit}"):
        print_substep("The 'results' folder could not be found so it was automatically created.")
        os.makedirs(f"./results/{subreddit}")

    if not exists(f"./results/{subreddit}/OnlyTTS") and allowOnlyTTSFolder:
        print_substep("The 'OnlyTTS' folder could not be found so it was automatically created.")
        os.makedirs(f"./results/{subreddit}/OnlyTTS")

    # create a thumbnail for the video
    settingsbackground = settings.config["settings"]["background"]

    if settingsbackground["background_thumbnail"]:
        if not exists(f"./results/{subreddit}/thumbnails"):
            print_substep(
                "The 'results/thumbnails' folder could not be found so it was automatically created."
            )
            os.makedirs(f"./results/{subreddit}/thumbnails")
        # get the first file with the .png extension from assets/backgrounds and use it as a background for the thumbnail
        first_image = next(
            (file for file in os.listdir("assets/backgrounds") if file.endswith(".png")),
            None,
        )
        if first_image is None:
            print_substep("No png files found in assets/backgrounds", "red")

        else:
            font_family = settingsbackground["background_thumbnail_font_family"]
            font_size = settingsbackground["background_thumbnail_font_size"]
            font_color = settingsbackground["background_thumbnail_font_color"]
            thumbnail = Image.open(f"assets/backgrounds/{first_image}")
            width, height = thumbnail.size
            thumbnailSave = create_thumbnail(
                thumbnail,
                font_family,
                font_size,
                font_color,
                width,
                height,
                title_thumb,
            )
            thumbnailSave.save(f"./assets/temp/{reddit_id}/thumbnail.png")
            print_substep(f"Thumbnail - Building Thumbnail in assets/temp/{reddit_id}/thumbnail.png")

    text = f"Background by {background_config['video'][2]}"
    background_clip = ffmpeg.drawtext(
        background_clip,
        text=text,
        x=f"(w-text_w)",
        y=f"(h-text_h)",
        fontsize=5,
        fontcolor="White",
        fontfile=os.path.join("fonts", "Roboto-Regular.ttf"),
    )
    background_clip = background_clip.filter("scale", W, H)
    print_step("Rendering the video 🎥")
    from tqdm import tqdm

    pbar = tqdm(total=100, desc="Progress: ", bar_format="{l_bar}{bar}", unit=" %")

    def on_update_example(progress) -> None:
        status = round(progress * 100, 2)
        old_percentage = pbar.n
        pbar.update(status - old_percentage)

    defaultPath = f"results/{subreddit}"
    with ProgressFfmpeg(length, on_update_example) as progress:
        path = defaultPath + f"/{filename}"
        path = (
            path[:251] + ".mp4"
        )  # Prevent a error by limiting the path length, do not change this.
        try:
            ffmpeg.output(
                background_clip,
                final_audio,
                path,
                f="mp4",
                **{
                    "c:v": "h264",
                    "b:v": "20M",
                    "b:a": "192k",
                    "threads": multiprocessing.cpu_count(),
                },
            ).overwrite_output().global_args("-progress", progress.output_file.name).run(
                quiet=True,
                overwrite_output=True,
                capture_stdout=False,
                capture_stderr=False,
            )
        except ffmpeg.Error as e:
            print(e.stderr.decode("utf8"))
            exit(1)
    old_percentage = pbar.n
    pbar.update(100 - old_percentage)
    if allowOnlyTTSFolder:
        path = defaultPath + f"/OnlyTTS/{filename}"
        path = (
            path[:251] + ".mp4"
        )  # Prevent a error by limiting the path length, do not change this.
        print_step("Rendering the Only TTS Video 🎥")
        with ProgressFfmpeg(length, on_update_example) as progress:
            try:
                ffmpeg.output(
                    background_clip,
                    audio,
                    path,
                    f="mp4",
                    **{
                        "c:v": "h264",
                        "b:v": "20M",
                        "b:a": "192k",
                        "threads": multiprocessing.cpu_count(),
                    },
                ).overwrite_output().global_args("-progress", progress.output_file.name).run(
                    quiet=True,
                    overwrite_output=True,
                    capture_stdout=False,
                    capture_stderr=False,
                )
            except ffmpeg.Error as e:
                print(e.stderr.decode("utf8"))
                exit(1)

        old_percentage = pbar.n
        pbar.update(100 - old_percentage)
    pbar.close()
    save_data(subreddit, filename + ".mp4", title, idx, background_config["video"][2])
    print_step("Removing temporary files 🗑")
    cleanups = cleanup(reddit_id)
    print_substep(f"Removed {cleanups} temporary files 🗑")
    print_step("Done! 🎉 The video is in the results folder 📁")
