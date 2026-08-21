# =============================================================================
# VIDEO ON START - button-triggered Raspberry Pi camera recorder
# =============================================================================
# What this script does, in plain terms:
#   1. Runs automatically on boot (via the MyService.service systemd unit -
#      see README_video_on_start.txt for the full setup) and waits for the
#      button on GPIO pin 27 to be pressed.
#   2. On the first press, lights the LED (GPIO pin 18) for a few seconds
#      to confirm it started, then begins recording video from the
#      Raspberry Pi camera to an .mp4 file.
#   3. Recording stops either when a second button press happens, or when
#      RECORDING_SECONDS (below) elapses, whichever comes first.
#   4. The program then exits - it only records ONE video per run. To
#      record another video, the whole script/service has to run again
#      (e.g. by rebooting the Pi, since it's launched on boot via systemd).
#
# Where the output goes:
#   Each recording is saved to
#   /home/cityofjackson/hailo-apps/traffic_counter/recorded_videos/, named
#   with the date and time recording started (see `filename` below).
#
# Quick reference - things people most often want to tweak, and where to
# find them in this file:
#   - RECORDING_MINUTES   how long a recording runs before auto-stopping
#                          (below, near RECORDING_SECONDS)
#   - LED_ON_SECONDS       how long the confirmation LED stays lit
#   - button / led pins    which GPIO pins the button/LED are wired to
#                          (below, near "Set up hardware")
# =============================================================================

# -----------------------------
# Import libraries
# -----------------------------

# Allows the program to use physical hardware connected to the Raspberry Pi's GPIO pins.
# Button lets us detect when a push button is pressed.
from gpiozero import Button

# LED lets us turn an LED on and off through a GPIO pin.
from gpiozero import LED

# Imports the Raspberry Pi Camera library so we can control the camera.
from picamera2 import Picamera2

# Imports the video encoder that compresses the video into H.264 format.
from picamera2.encoders import H264Encoder

# Imports a class that saves the recorded video as an MP4 file using FFmpeg.
from picamera2.outputs import FfmpegOutput

#imports datetime to create filenames of each day
from datetime import datetime

# Event lets the button (running on its own callback thread) tell the main
# thread "stop now" without a race condition.
from threading import Event


# -----------------------------
# Set up hardware
# -----------------------------

# Creates a Button object connected to GPIO pin 27.
# bounce_time filters out electrical chatter from a single physical press so
# it can't be read as multiple presses (which previously caused a press to
# both start AND immediately stop the recording).
button = Button(27)

# Creates an LED object connected to GPIO pin 18.
led = LED(18)


# -----------------------------
# Set up the camera
# -----------------------------

# Creates the camera object.
picam2 = Picamera2()

# Configures the camera for recording video.
picam2.configure(picam2.create_video_configuration())

# Creates a video encoder.
# 10,000,000 is the bitrate (video quality).
# Higher numbers generally mean better quality and larger files.
encoder = H264Encoder(10000000)

# Starts the camera so it is ready when recording begins.
picam2.start()


# -----------------------------
# Recording duration
# -----------------------------

# How long to record for, in seconds, before stopping automatically.
RECORDING_MINUTES = 480

# Other durations (in seconds) - swap one in above when you want to extend
# the recording later:

# 6 hours = 360
# 7 hours = 420
# 8 hours = 480

RECORDING_SECONDS = (RECORDING_MINUTES * 60)

# How long to keep the LED lit after recording starts, just to confirm it
# started - then it turns off to save power for the rest of the recording.
LED_ON_SECONDS = 3


# -----------------------------
# Start/stop handling
# -----------------------------

# This program only ever records one video per run:
#   1. Wait for the first button press to start recording.
#   2. While recording, a second button press stops it early.
#   3. Either way (early press or RECORDING_SECONDS elapsing), stop and
#      exit - it does NOT start a new recording afterward.
start_event = Event()
stop_event = Event()


def request_start():
    """Button-press handler while waiting to start (see
    `button.when_pressed = request_start` below). Just flips start_event,
    which wakes up the `start_event.wait()` call further down and lets the
    program move on to actually starting the recording."""
    start_event.set()


def request_stop():
    """Button-press handler once recording is underway (wired up further
    below with `button.when_pressed = request_stop`, right after the first
    press is handled). Flips stop_event, which wakes up whichever
    `stop_event.wait(...)` call is currently blocking further down so the
    program can stop the recording early instead of waiting out the full
    RECORDING_SECONDS.

    Safe to call more than once (e.g. multiple button presses) -
    Event.set() is idempotent, and the actual picam2.stop_recording()
    call below only ever happens once."""
    stop_event.set()


# Before recording starts, a button press means "start".
button.when_pressed = request_start


# -----------------------------
# Record one video
# -----------------------------

print("Press the button to start recording.")

# Block here until the button is pressed for the first time.
start_event.wait()

# From here on, a button press means "stop" instead of "start".
button.when_pressed = request_stop

# Build the filename for this recording.
filename = f"/home/cityofjackson/hailo-apps/traffic_counter/recorded_videos/{datetime.now().strftime('%B_%d_%Y_%I-%M%p')}.mp4"
output = FfmpegOutput(filename)

print(f"Starting recording... ({filename})")

# Turn the LED on to confirm recording is starting.
led.on()

# Begin recording using the camera, encoder, and output file.
picam2.start_recording(encoder, output)

# Keep the LED on just long enough to confirm it started, then turn it off
# to conserve energy for the rest of the recording. If the button is
# pressed (early stop) during this window, stop_event.wait() returns right
# away instead of sitting through the full LED_ON_SECONDS.
stop_event.wait(timeout=LED_ON_SECONDS)
led.off()

# If we weren't already told to stop, keep waiting for whichever comes
# first: the rest of RECORDING_SECONDS, or an early button press.
if not stop_event.is_set():
    stop_event.wait(timeout=RECORDING_SECONDS - LED_ON_SECONDS)

# Stop saving to the file, whether we got here by timing out or by an
# early button press.
picam2.stop_recording()

print("Recording finished.")

# Release the camera and exit - this program only records once per run.
picam2.stop()
