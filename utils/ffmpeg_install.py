import os
import subprocess
import zipfile

import requests


def ffmpeg_install_windows():
    try:
        ffmpeg_url = (
            "https://github.com/GyanD/codexffmpeg/releases/download/6.0/ffmpeg-6.0-full_build.zip"
        )
        ffmpeg_zip_filename = "ffmpeg.zip"
        ffmpeg_extracted_folder = "ffmpeg"

        if os.path.exists(ffmpeg_zip_filename):
            os.remove(ffmpeg_zip_filename)

        r = requests.get(ffmpeg_url)
        with open(ffmpeg_zip_filename, "wb") as f:
            f.write(r.content)

        if os.path.exists(ffmpeg_extracted_folder):
            for root, dirs, files in os.walk(ffmpeg_extracted_folder, topdown=False):
                for file in files:
                    os.remove(os.path.join(root, file))
                for dir in dirs:
                    os.rmdir(os.path.join(root, dir))
            os.rmdir(ffmpeg_extracted_folder)

        with zipfile.ZipFile(ffmpeg_zip_filename, "r") as zip_ref:
            zip_ref.extractall()
        os.remove("ffmpeg.zip")

        os.rename(f"{ffmpeg_extracted_folder}-6.0-full_build", ffmpeg_extracted_folder)
        for file in os.listdir(os.path.join(ffmpeg_extracted_folder, "bin")):
            os.rename(
                os.path.join(ffmpeg_extracted_folder, "bin", file),
                os.path.join(".", file),
            )
        os.rmdir(os.path.join(ffmpeg_extracted_folder, "bin"))
        for file in os.listdir(os.path.join(ffmpeg_extracted_folder, "doc")):
            os.remove(os.path.join(ffmpeg_extracted_folder, "doc", file))
        for file in os.listdir(os.path.join(ffmpeg_extracted_folder, "presets")):
            os.remove(os.path.join(ffmpeg_extracted_folder, "presets", file))
        os.rmdir(os.path.join(ffmpeg_extracted_folder, "presets"))
        os.rmdir(os.path.join(ffmpeg_extracted_folder, "doc"))
        os.remove(os.path.join(ffmpeg_extracted_folder, "LICENSE"))
        os.remove(os.path.join(ffmpeg_extracted_folder, "README.txt"))
        os.rmdir(ffmpeg_extracted_folder)

        print(
            "FFmpeg installed successfully! Please restart your computer and then re-run the program."
        )
    except Exception as e:
        print(
            "An error occurred while trying to install FFmpeg. Please try again. Otherwise, please install FFmpeg manually and try again."
        )
        print(e)
        exit()


def ffmpeg_install_linux():
    try:
        subprocess.run(
            "sudo apt install ffmpeg",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as e:
        print(
            "An error occurred while trying to install FFmpeg. Please try again. Otherwise, please install FFmpeg manually and try again."
        )
        print(e)
        exit()
    print("FFmpeg installed successfully! Please re-run the program.")
    exit()


def ffmpeg_install_mac():
    try:
        subprocess.run(
            "brew install ffmpeg",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        print(
            "Homebrew is not installed. Please install it and try again. Otherwise, please install FFmpeg manually and try again."
        )
        exit()
    print("FFmpeg installed successfully! Please re-run the program.")
    exit()


def ffmpeg_install():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        if os.path.exists("./ffmpeg.exe"):
            print(
                "FFmpeg is installed on this system! If you are seeing this error for the second time, restart your computer."
            )
        print("FFmpeg is not installed on this system.")
        resp = input(
            "We can try to automatically install it for you. Would you like to do that? (y/n): "
        )
        if resp.lower() == "y":
            print("Installing FFmpeg...")
            if os.name == "nt":
                ffmpeg_install_windows()
            elif os.name == "posix":
                ffmpeg_install_linux()
            elif os.name == "mac":
                ffmpeg_install_mac()
            else:
                print("Your OS is not supported. Please install FFmpeg manually and try again.")
                exit()
        else:
            print("Please install FFmpeg manually and try again.")
            exit()
    except Exception as e:
        print(
            "Welcome fellow traveler! You're one of the few who have made it this far. We have no idea how you got at this error, but we're glad you're here. Please report this error to the developer, and we'll try to fix it as soon as possible. Thank you for your patience!"
        )
        print(e)
    return None
