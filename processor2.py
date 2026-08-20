# =============================================================================
# TRAFFIC COUNTER - Hailo AI vehicle detection & counting script
# =============================================================================
# What this script does, in plain terms:
#   1. Runs an AI vehicle-detection model (via the Hailo chip) over a video
#      file or camera feed.
#   2. Watches a rectangular "zone" in the middle of the frame - like an
#      invisible line vehicles have to cross - and counts vehicles by type
#      (SUV, Bus, Sedan, etc.) whenever their center point lands inside it.
#   3. Writes a live, frame-by-frame log to detections_log.txt as it runs.
#   4. When the pipeline stops (end of video, or Ctrl+C), writes a short
#      summary (summary.txt) and a bar chart (summary_chart.png). Those
#      totals are deduped by time window so a vehicle sitting in the zone
#      for several seconds is reported once, not dozens of times.
#
# Where the output goes:
#   Every run gets its own folder under OUTPUT_BASE_DIR, named with the
#   date/time the run started, so re-running never overwrites a previous
#   run's files.
#
# Quick reference - things people most often want to tweak, and where to
# find them in this file:
#   - NOTABLE_CLASSES        which vehicle types get their own timestamp
#                            list in the recap (below, near VEHICLE_CLASSES)
#   - VEHICLE_CLASSES        the full list of vehicle types this app knows
#                            about, and how to add a new one
#   - DEDUP_WINDOW_SECONDS   how many seconds count as "the same vehicle"
#                            in the end-of-run recap
#   - MODEL_DIR              where the AI model files live
#   - zone_x_min/x_max/      the counting zone's position and size (in
#     zone_y_min/y_max       user_app_callback_class.__init__, below)
#   - confidence threshold   how sure the AI must be before it counts a
#                            detection (search this file for "confidence <=")
# =============================================================================
# region imports
# Standard library imports
import os
import sys
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# Third-party imports
import gi

gi.require_version("Gst", "1.0")
import cv2

# Local application-specific imports
import hailo

#need timestamps
import time


#imports datetime to create filenames of each day
from datetime import datetime

from gi.repository import Gst

from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp
from hailo_apps.python.core.common.buffer_utils import (
    get_caps_from_pad,
    get_numpy_from_buffer,
)

from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class
from hailo_apps.python.core.gstreamer.gstreamer_helper_pipelines import (
    DISPLAY_PIPELINE,
    INFERENCE_PIPELINE,
    INFERENCE_PIPELINE_WRAPPER,
    QUEUE,
    TRACKER_PIPELINE,
    USER_CALLBACK_PIPELINE,
)



# TUNABLE: change this if you want run folders written somewhere else.
#creates a variable for the filename. If want to chnage filename then edit this line
# Each run gets its own timestamped subfolder so that
# starting the program again never overwrites a previous run's log/summary.
OUTPUT_BASE_DIR = "/home/cityofjackson//hailo-apps/traffic_counter/traffic_count_data"
RUN_START = datetime.now()  # also used for the recap's "Review"/"Date" header
RUN_DIR = os.path.join(OUTPUT_BASE_DIR, f"run_{RUN_START.strftime('%Y-%m-%d_%H-%M')}")  # e.g. .../run_2026-08-10_09-15
os.makedirs(RUN_DIR, exist_ok=True)

# The files every run produces, all saved inside RUN_DIR above:
filename = os.path.join(RUN_DIR, "detections_log.txt")          # every single in-zone detection, logged live frame-by-frame
SUMMARY_TXT_PATH = os.path.join(RUN_DIR, "summary.txt")          # end-of-run totals + notable-class timestamps (the "recap")
SUMMARY_CHART_PATH = os.path.join(RUN_DIR, "summary_chart.png")  # bar-chart version of the totals
SCREENSHOTS_DIR = os.path.join(RUN_DIR, "screenshots")           # one .jpg per notable-class timestamp, for the slideshow below
SLIDESHOW_HTML_PATH = os.path.join(RUN_DIR, "review_slideshow.html")  # click-through review page - open this in a browser

# TUNABLE: classes that get their own individual timestamp listing in the
# end-of-run recap (in addition to the totals bar chart, which always
# covers all 9). Edit this list to change which classes matter enough to
# call out - e.g.
# swap to ["Sedan", "SUV"] if trucks stop being the thing you care about.
# Must use the display names below, not the raw model labels
# ("School Bus"/"Tanker Truck"/"Semi Truck", not "school bus"/etc.):
#   SUV, Bus, Sedan, Fire engine, Pickup truck,
#   Semi Truck, School Bus, Tanker Truck, Van
NOTABLE_CLASSES = ["School Bus", "Tanker Truck", "Semi Truck"]

# TUNABLE (advanced): single source of truth for every vehicle class this
# app knows about, in place of what used to be 9 nearly-identical
# `if label == "...":` blocks in
# app_callback plus 9 matching print blocks. Each row is:
#   (raw model label, count attribute name, print-line prefix,
#    in-zone log message, display name used in the recap/NOTABLE_CLASSES)
# To add a new class: add one row here and one `detection_count_*`
# attribute in user_app_callback_class.__init__ - nothing else to touch.
VEHICLE_CLASSES = [
    ("SUV",          "detection_count_SUV",          "SUV",          "SUV is in zone",          "SUV"),
    ("bus",          "detection_count_bus",          "Bus",          "bus is in zone",          "Bus"),
    ("sedan",        "detection_count_sedan",        "Sedan",        "sedan is in zone",        "Sedan"),
    ("fire engine",  "detection_count_fire_engine",  "Fire engine",  "fire engine is in zone",  "Fire engine"),
    ("pickup truck", "detection_count_pickup_truck", "Pickup truck", "pickup truck is in zone", "Pickup truck"),
    ("Semi Truck",   "detection_count_semi_truck",   "Semi truck",   "semi truck is in zone",   "Semi Truck"),
    ("School Bus",   "detection_count_school_bus",   "School bus",   "school bus is in zone",   "School Bus"),
    ("Tanker Truck", "detection_count_tanker_truck", "Tanker truck", "tanker truck is in zone", "Tanker Truck"),
    ("Van",          "detection_count_van",          "Van",          "van is in zone",          "Van"),
]
LABEL_TO_CLASS_INFO = {row[0]: row for row in VEHICLE_CLASSES}
NOTABLE_NAME_TO_COUNT_ATTR = {row[4]: row[1] for row in VEHICLE_CLASSES}

# TUNABLE: size (in seconds) of the window used to dedup the end-of-run
# recap - this does NOT affect the live per-frame log (detections_log.txt),
# only the totals/timestamps in summary.txt. A vehicle sitting in the zone
# across multiple frames within the same window only counts once in the
# recap. At 30fps a vehicle in the zone for a few seconds would otherwise
# log dozens of raw per-frame hits - grouping into DEDUP_WINDOW_SECONDS-wide
# windows collapses that back down to roughly "1 vehicle", while a vehicle
# that lingers past the window still registers again for the next one. As
# set below (3), a vehicle in the zone for up to 3 straight seconds counts
# once; if it lingers into a 4th second it starts a new window and counts
# again. Raise this number if vehicles are still being over-counted;
# lower it if slow-moving vehicles that really are separate aren't getting
# separate counts.
DEDUP_WINDOW_SECONDS = 3


def format_timestamp(total_seconds):
    """Whole seconds since the video started -> "H:MM:SS" (or "M:SS" under
    an hour), for the recap's per-window timestamp listing."""
    total_seconds = int(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

hailo_logger = get_logger(__name__)
# endregion imports





# import yaml
# with open("metadata.yaml") as f:
#     data = yaml.safe_load(f)
    
# class_names = data["names"]
# print(class_names)


import yaml

#AI model path
# TUNABLE: where the trained AI model files (best.hef + metadata.yaml)
# live. Change this if you retrain the model or move it elsewhere.
MODEL_DIR = "/home/cityofjackson/hailo-apps/traffic_counter/rpi_deploy"

with open(f"{MODEL_DIR}/metadata.yaml") as f:
    data = yaml.safe_load(f)

class_names = data["names"]

hef_path = f"{MODEL_DIR}/model.hef"




def center_crop_pipeline(video_width, video_height, name="center_crop"):
    """Center-crop the frame to a square before it reaches the model, instead
    of it getting stretched/letterboxed to a non-native aspect ratio. The
    training screenshots for this model were framed close-up and roughly
    square, not wide 16:9 shots, so cropping in to match that framing (rather
    than squeezing or padding the full 16:9 frame down to 640x640) is what
    should actually match what the model learned."""
    if video_width == video_height:
        return ""
    if video_width > video_height:
        excess = video_width - video_height
        left, right, top, bottom = excess // 2, excess - excess // 2, 0, 0
    else:
        excess = video_height - video_width
        left, right, top, bottom = 0, 0, excess // 2, excess - excess // 2
    return (
        f"videocrop name={name} left={left} right={right} top={top} bottom={bottom} ! "
        f"{QUEUE(name=f'{name}_q')} ! "
    )


class CenterCropDetectionApp(GStreamerDetectionApp):
    """GStreamerDetectionApp, but center-crops the frame to a square before
    it reaches the model - see center_crop_pipeline() above."""

    def get_pipeline_string(self):
        """Build the full GStreamer pipeline string for this app, as one
        long "!"-joined chain of elements the way GStreamer expects.

        Order of stages: video source -> center-crop (see
        center_crop_pipeline() above) -> Hailo AI inference -> object
        tracker -> our app_callback() (via USER_CALLBACK_PIPELINE) ->
        on-screen display/output. This overrides the parent class's
        version (GStreamerDetectionApp.get_pipeline_string) only to splice
        in the center-crop step before inference; everything else is the
        same stock detection pipeline. Called once, automatically, when
        app.run() (see main() near the bottom of this file) starts the
        pipeline."""
        source_pipeline = self.get_source_pipeline()
        crop_pipeline = center_crop_pipeline(self.video_width, self.video_height)
        detection_pipeline = INFERENCE_PIPELINE(
            hef_path=self.hef_path,
            post_process_so=self.post_process_so,
            post_function_name=self.post_function_name,
            batch_size=self.batch_size,
            config_json=self.labels_json,
            additional_params=self.thresholds_str,
        )
        detection_pipeline_wrapper = INFERENCE_PIPELINE_WRAPPER(detection_pipeline)
        tracker_pipeline = TRACKER_PIPELINE(class_id=-1)
        user_callback_pipeline = USER_CALLBACK_PIPELINE()
        display_pipeline = DISPLAY_PIPELINE(
            video_sink=self.video_sink, sync=self.sync, show_fps=self.show_fps
        )

        pipeline_string = (
            f"{source_pipeline} ! "
            f"{crop_pipeline}"
            f"{detection_pipeline_wrapper} ! "
            f"{tracker_pipeline} ! "
            f"{user_callback_pipeline} ! "
            f"{display_pipeline}"
        )
        hailo_logger.debug("Pipeline string: %s", pipeline_string)
        return pipeline_string


# -----------------------------------------------------------------------------------------------
# User-defined class to be used in the callback function
# -----------------------------------------------------------------------------------------------
class user_app_callback_class(app_callback_class):
    """Holds all the state that needs to persist across video frames -
    per-class vehicle counts, the counting zone's position, and the
    dedup-by-time-window tracking used for the end-of-run recap. One
    instance of this is created in main() and passed into every call of
    app_callback() below as `user_data`, so anything stored on `self` here
    is readable/writable from app_callback() on every frame."""

    def __init__(self):
        """Runs once, when user_app_callback_class() is created in main()
        (before the pipeline starts). Sets up: one detection_count_* and
        one seconds_seen entry per class in VEHICLE_CLASSES, the counting
        zone's coordinates (zone_x_min/max, zone_y_min/max), and a handful
        of legacy/unused attributes kept around from earlier versions of
        this script (see the NOTE comments below each one)."""
        super().__init__()
        self.new_variable = 42  # NOTE: unused leftover - not read anywhere in this file, safe to ignore
        
#         self.detection_count_car = 0
#         self.detection_count_truck = 0
#         self.detection_count_bus = 0
        
        #initialize new variables
        for _, count_attr, _, _, _ in VEHICLE_CLASSES:
            setattr(self, count_attr, 0)
        
    # TUNABLE: the "counting zone" - a rectangle in normalized 0-1
    # coordinates, where (0,0) is the top-left corner of the cropped
    # square video frame and (1,1) is the bottom-right corner. A vehicle
    # only gets counted when its bounding-box center falls inside this
    # rectangle. As set below it's a vertical strip through the middle
    # ~26% of the frame's width (0.37 to 0.63) spanning the full height
    # (0 to 1) - basically a vertical line vehicles have to cross.
    # To move the zone left/right: shift both zone_x_min and zone_x_max by
    # the same amount. To make it wider/narrower: spread/shrink them apart.
    # To only count vehicles in part of the frame vertically (e.g. ignore
    # the top of the frame): set zone_y_min above 0 and/or zone_y_max below 1.
    #want to make a zone where if a bus or car goes through then it will add to its tally
        self.zone_x_min = 0.3
        self.zone_y_min = 0
        self.zone_x_max = 0.7
        self.zone_y_max = 1

    #Debouncing variables/cooldown
    # NOTE: these two are currently unused - nothing in app_callback()
    # below reads or updates them. They look like they were meant for
    # frame-by-frame debouncing (only count a vehicle once it's been seen
    # in the zone for N consecutive frames), but that logic was never
    # written. DEDUP_WINDOW_SECONDS above is what actually prevents one
    # lingering vehicle from being counted dozens of times - but only in
    # the end-of-run recap; the live per-frame counters below still fire
    # every single frame.
        self.in_zone_frames = 0  #consecutive frames with object in zone
        self.out_zone_frames = 0 #consecutive frames without object in zone

    #variable for bus, car, and truck count that go over the line
#         self.car_total_count = 0
#         self.bus_total_count = 0 
#         self.truck_total_count = 0
        
        
    # NOTE: these *_total_count attributes are unused/dead - the real, live
    # counters are the detection_count_* attributes set in the loop above
    # (driven by VEHICLE_CLASSES). Nothing increments these, so they'll
    # always read 0. Safe to ignore/remove.
    #variables for car counts
        self.SUV_total_count = 0
        self.bus_total_count = 0
        self.sedan_total_count = 0
        self.fire_enginer_total_count = 0
        self.pickup_truck_total_count = 0
        self.semi_truck_total_count = 0
        self.school_bus_total_count = 0
        self.tanker_truck_total_count = 0
        self.van_total_count = 0
        
    # NOTE: self.time / self.live_time / self.total_seconds / self.minutes /
    # self.seconds below are also unused - app_callback() below recomputes
    # total_seconds/minutes/seconds itself from frame_idx every single
    # frame rather than reading these. Safe to ignore/remove.
    #variable to calculate the time of the video
        self.time = 0
        self.live_time = 0
    #def new_function(self):        #idk what this does so I commented it out

        #set variables for minutes and seconds
        self.total_seconds = 0
        self.minutes = 0
        self.seconds = 0

        # Distinct DEDUP_WINDOW_SECONDS-sized windows (as an int bucket
        # index) each class was seen in the zone at least once. A single
        # truck sitting in the zone for many frames within one window only
        # adds one entry here - this is what the end-of-run recap counts
        # from, so the report says "1 school bus" instead of "60", while the
        # live per-frame prints/log below are untouched and still fire every
        # single frame like before.
        self.seconds_seen = {count_attr: set() for _, count_attr, _, _, _ in VEHICLE_CLASSES}

# -----------------------------------------------------------------------------------------------
# User-defined callback function
# -----------------------------------------------------------------------------------------------


def app_callback(element, buffer, user_data):
    """The main per-frame worker - GStreamer calls this automatically for
    every frame that passes through USER_CALLBACK_PIPELINE (wired up in
    CenterCropDetectionApp.get_pipeline_string() above). This is where all
    of the actual "is a vehicle in the zone right now" logic lives:

      1. Pull the list of AI detections for this frame off the buffer.
      2. For each detection: skip it if the model isn't confident enough
         (see the "confidence <= 0.5" check below), or if its label isn't
         one of the known VEHICLE_CLASSES.
      3. Otherwise, compute the detection's center point and check it
         against the counting zone (user_data.zone_x_min/x_max/y_min/
         y_max, set in user_app_callback_class.__init__ above).
      4. If it's in the zone: bump that class's running total
         (user_data.detection_count_*), record this DEDUP_WINDOW_SECONDS
         window as "seen" for the end-of-run recap (user_data.seconds_seen),
         and queue up a log line.
      5. Print/log one line per class that was in-zone this frame, to both
         the terminal and detections_log.txt (see `filename` near the top
         of this file).

    `element`/`buffer` are handed in by the GStreamer framework itself;
    `user_data` is the user_app_callback_class instance created in main()."""
    if buffer is None:
        hailo_logger.warning("Received None buffer.")
        return
        
    
    # Note: Frame counting is handled automatically by the framework wrapper
    frame_idx = user_data.get_count()
    #string_to_print = f"Frame count: {user_data.get_count()}\n"
    user_data.use_frame = True
    pad = element.get_static_pad("src")
    format, width, height = get_caps_from_pad(pad)
    

    
    frame = None
    # TUNABLE: process every Nth frame instead of every single frame, to
    # reduce load. Currently N=1 (frame_idx % 1 == 0 is always True, so
    # every frame is processed - nothing is actually being skipped). To
    # process every other frame, change the 1 to a 2; every 3rd frame,
    # change it to a 3; etc. Note: raising this makes the fps-based math in
    # total_seconds below (which assumes 30fps, not "every Nth frame at
    # 30fps") slightly less accurate for the printed timestamps.
    if frame_idx % 1 == 0: #this skips every _ number of frames
       
        if user_data.use_frame and format is not None and width is not None and height is not None:
            frame = get_numpy_from_buffer(buffer, format, width, height)

        roi = hailo.get_roi_from_buffer(buffer)
        detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

        #math for making it easier to find minute and second - moved above
        #the detection loop so it's available for the seconds_seen tracking
        #below, not just for the print statements at the end
        total_seconds = frame_idx / 30
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        seconds = seconds // 1
        hours = minutes // 60
        minutes = minutes % 60
        current_bucket_id = int(total_seconds // DEDUP_WINDOW_SECONDS)  #which DEDUP_WINDOW_SECONDS-wide window this frame falls in - the dedup key for the recap

        object_in_zone = False
        # count_attr -> accumulated "X is in zone\n" message(s) for this frame
        # (a class can appear more than once if the model reports multiple
        # detections of it in the same frame).
        in_zone_messages = {}

        for detection in detections:
            label = detection.get_label()            #gets the labels of all objects in frame
            confidence = detection.get_confidence()  #gets the confidence of all objects recognized

            # TUNABLE: minimum AI confidence (0-1) required to count a
            # detection. Raise this (e.g. 0.7-0.8) to cut down on false
            # positives / misclassified vehicles; lower it (e.g. 0.4-0.5)
            # if real vehicles are being missed because the model isn't
            # quite confident enough about them.
            if confidence <= 0.5:
                continue

            class_info = LABEL_TO_CLASS_INFO.get(label)
            if class_info is None:
                continue

            _, count_attr, _, log_message, _ = class_info

            #get bounding box coordinates
            bbox = detection.get_bbox()
            x_min = bbox.xmin()
            y_min = bbox.ymin()
            box_width = bbox.width()
            box_height = bbox.height()

            #calculate center point of car (normalized 0-1, where (0,0) is
            #the top-left corner of the cropped square frame and (1,1) is
            #the bottom-right corner - same coordinate system as the zone)
            center_x = x_min + (box_width / 2)
            center_y = y_min + (box_height / 2)

            #check if the object's center is in the target zone
            #(zone_x_min/zone_x_max/zone_y_min/zone_y_max are set in
            #user_app_callback_class.__init__ above - that's where to go to
            #move or resize the counting zone)
            if (user_data.zone_x_min <= center_x <= user_data.zone_x_max and user_data.zone_y_min <= center_y <= user_data.zone_y_max):
                object_in_zone = True
                in_zone_messages[count_attr] = in_zone_messages.get(count_attr, "") + f"{log_message}\n"

                #counter for each car, this will loop until each car is accounted for
                setattr(user_data, count_attr, getattr(user_data, count_attr) + 1)

                #dedup-by-window tracker for the recap - a set, so many hits
                #within the same window only add one entry
                user_data.seconds_seen[count_attr].add(current_bucket_id)

        #print to the terminal (and log to file) for every class that was in zone this frame
        for raw_label, count_attr, print_prefix, log_message, notable_name in VEHICLE_CLASSES:
            message = in_zone_messages.get(count_attr)
            if not message:
                continue

            count = getattr(user_data, count_attr)
            print(f"  {print_prefix} #:", count, end='')
            print("  Time:", hours, ":", minutes, ":", seconds, end='')
            print(" ", message, end='')

            #print the car data onto a file incase of unintentional shutdown
            with open(filename, 'a') as f:
                print(f"  {print_prefix} #:", count, file=f)
                print("  Time:", hours, ":", minutes, ":", seconds, file=f)
                print(" ", message, file=f)


# -----------------------------------------------------------------------------------------------
# End-of-run recap - writes summary.txt + summary_chart.png. Runs once, when
# the pipeline stops (see the `finally:` block in main() below), not every
# frame like app_callback() above.
# -----------------------------------------------------------------------------------------------


def get_notable_timestamps(user_data):
    """For each class in NOTABLE_CLASSES, the list of (seconds, "H:MM:SS")
    it was seen in the zone - one entry per DEDUP_WINDOW_SECONDS-wide dedup
    window (see seconds_seen in user_app_callback_class.__init__). Shared by
    write_recap() (for the summary.txt text) and write_review_slideshow()
    (for which video moments to screenshot), so both list the exact same
    timestamps."""
    result = {}
    for cls in NOTABLE_CLASSES:
        count_attr = NOTABLE_NAME_TO_COUNT_ATTR[cls]
        result[cls] = [
            (bucket * DEDUP_WINDOW_SECONDS, format_timestamp(bucket * DEDUP_WINDOW_SECONDS))
            for bucket in sorted(user_data.seconds_seen[count_attr])
        ]
    return result


def write_recap(user_data, input_path=None):
    """End-of-run recap: totals for every class (as text + a bar chart), and
    the full list of timestamps for the notable/rare classes. Totals here
    are deduped by DEDUP_WINDOW_SECONDS-second window (via
    user_data.seconds_seen) rather than the raw per-frame counts, so a
    truck sitting in the zone for many frames within one window is
    reported once, not dozens of times."""
    counts = {
        notable_name: len(user_data.seconds_seen[count_attr])
        for _, count_attr, _, _, notable_name in VEHICLE_CLASSES
    }

    input_name = os.path.basename(input_path) if input_path else "(unknown)"
    lines = [
        f"Review: {os.path.basename(RUN_DIR)}",
        f"Date: {RUN_START.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Input file: {input_name}",
        "",
        "===== RUN RECAP =====",
        "",
        "Totals:",
    ]
    for name, count in counts.items():
        lines.append(f"  {name:14s}: {count}")

    notable_timestamps = get_notable_timestamps(user_data)
    for cls in NOTABLE_CLASSES:
        stamps = [stamp_text for _, stamp_text in notable_timestamps[cls]]
        lines.append("")
        lines.append(f"{cls} timestamps ({len(stamps)}):")
        if stamps:
            lines.extend(f"  - {stamp}" for stamp in stamps)
        else:
            lines.append("  (none)")

    recap_text = "\n".join(lines)
    print(recap_text)

    with open(SUMMARY_TXT_PATH, "w") as f:
        f.write(recap_text + "\n")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 5))
        plt.bar(list(counts.keys()), list(counts.values()), color="#4C72B0")
        plt.ylabel("Count")
        plt.title("Vehicle counts for this run")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        plt.savefig(SUMMARY_CHART_PATH)
        plt.close()
    except Exception as e:
        hailo_logger.warning("Could not generate summary chart: %s", e)
        SUMMARY_CHART_PATH_MSG = "(chart generation failed - see log)"
    else:
        SUMMARY_CHART_PATH_MSG = SUMMARY_CHART_PATH

    print(f"\nSummary written to: {SUMMARY_TXT_PATH}")
    print(f"Chart written to: {SUMMARY_CHART_PATH_MSG}")
    print(f"Full detection log: {filename}")


# -----------------------------------------------------------------------------------------------
# Review slideshow - grabs a screenshot from the source video at every
# notable-class (School Bus/Tanker Truck/Semi Truck) timestamp and builds a
# single HTML page to click through them, so reviewing a run in the morning
# means clicking Next through a handful of screenshots instead of scrubbing
# the whole overnight video by hand. Runs once, alongside write_recap(),
# when the pipeline stops (see the `finally:` block in main() below).
# -----------------------------------------------------------------------------------------------


def write_review_slideshow(user_data, input_path=None):
    """Re-opens the source video with OpenCV, seeks to each notable-class
    timestamp from get_notable_timestamps() and saves a screenshot, then
    writes review_slideshow.html (self-contained - no internet/extra
    installs needed) so the screenshots can be clicked through in a
    browser with the class + timestamp shown as a caption."""
    notable_timestamps = get_notable_timestamps(user_data)
    total_stamps = sum(len(stamps) for stamps in notable_timestamps.values())
    if total_stamps == 0:
        hailo_logger.info("No notable-class hits this run - skipping review slideshow.")
        return
    if not input_path or not os.path.isfile(input_path):
        hailo_logger.warning("Can't find input video (%s) to grab review screenshots.", input_path)
        return

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        hailo_logger.warning("Could not reopen %s to grab review screenshots.", input_path)
        return

    # TUNABLE: falls back to 30 (matching the 30fps assumed everywhere else
    # in this file) if OpenCV can't read the video's real frame rate.
    fps = cap.get(cv2.CAP_PROP_FPS) or 30

    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    slides = []  # (image filename, caption) in the order they'll appear in the slideshow
    for cls in NOTABLE_CLASSES:
        for seconds, stamp_text in notable_timestamps[cls]:
            frame_number = int(seconds * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ok, frame = cap.read()
            if not ok:
                hailo_logger.warning("Could not grab a screenshot at %s (%s).", stamp_text, cls)
                continue
            image_name = f"{cls.replace(' ', '_')}_{stamp_text.replace(':', '-')}.jpg"
            cv2.imwrite(os.path.join(SCREENSHOTS_DIR, image_name), frame)
            slides.append((image_name, f"{cls} @ {stamp_text}"))

    cap.release()

    if not slides:
        hailo_logger.warning("No screenshots could be captured - skipping review slideshow.")
        return

    with open(SLIDESHOW_HTML_PATH, "w") as f:
        f.write(_slideshow_html(slides))

    print(f"Review slideshow ({len(slides)} screenshots): {SLIDESHOW_HTML_PATH}")


def _slideshow_html(slides):
    """Builds the review_slideshow.html page body. `slides` is a list of
    (image filename, caption) tuples; image paths are relative to RUN_DIR
    (where both this HTML file and the screenshots/ folder live), so the
    page keeps working if the whole run folder is copied/moved elsewhere."""
    slides_json = "[" + ",".join(
        "{\"src\": %r, \"caption\": %r}" % (f"screenshots/{name}", caption)
        for name, caption in slides
    ) + "]"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Review slideshow - {os.path.basename(RUN_DIR)}</title>
<style>
  body {{ font-family: sans-serif; background: #222; color: #eee; text-align: center; margin: 0; padding: 20px; }}
  h1 {{ font-size: 18px; font-weight: normal; color: #aaa; }}
  #frame {{ max-width: 95vw; max-height: 70vh; border: 2px solid #555; border-radius: 4px; }}
  #caption {{ font-size: 22px; margin: 14px 0; }}
  #counter {{ color: #999; margin-bottom: 14px; }}
  .controls button {{ font-size: 20px; padding: 10px 24px; margin: 0 8px; cursor: pointer;
                       border: none; border-radius: 4px; background: #4C72B0; color: white; }}
  .controls button:hover {{ background: #5b84c4; }}
  .controls button:disabled {{ background: #555; cursor: default; }}
  #thumbs {{ margin-top: 24px; display: flex; flex-wrap: wrap; justify-content: center; gap: 6px; }}
  #thumbs img {{ height: 60px; border: 2px solid transparent; border-radius: 3px; cursor: pointer; opacity: 0.6; }}
  #thumbs img.active {{ border-color: #4C72B0; opacity: 1; }}
</style>
</head>
<body>
  <h1>{os.path.basename(RUN_DIR)} &mdash; use the buttons or &larr;/&rarr; arrow keys</h1>
  <div id="counter"></div>
  <img id="frame" src="">
  <div id="caption"></div>
  <div class="controls">
    <button id="prevBtn">&larr; Prev</button>
    <button id="nextBtn">Next &rarr;</button>
  </div>
  <div id="thumbs"></div>

<script>
  const slides = {slides_json};
  let i = 0;

  const frame = document.getElementById("frame");
  const caption = document.getElementById("caption");
  const counter = document.getElementById("counter");
  const thumbs = document.getElementById("thumbs");
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");

  slides.forEach((s, idx) => {{
    const t = document.createElement("img");
    t.src = s.src;
    t.addEventListener("click", () => show(idx));
    thumbs.appendChild(t);
  }});

  function show(idx) {{
    i = Math.max(0, Math.min(idx, slides.length - 1));
    frame.src = slides[i].src;
    caption.textContent = slides[i].caption;
    counter.textContent = (i + 1) + " / " + slides.length;
    prevBtn.disabled = (i === 0);
    nextBtn.disabled = (i === slides.length - 1);
    [...thumbs.children].forEach((el, idx2) => el.classList.toggle("active", idx2 === i));
  }}

  prevBtn.addEventListener("click", () => show(i - 1));
  nextBtn.addEventListener("click", () => show(i + 1));
  document.addEventListener("keydown", (e) => {{
    if (e.key === "ArrowLeft") show(i - 1);
    if (e.key === "ArrowRight") show(i + 1);
  }});

  show(0);
</script>
</body>
</html>
"""


# -----------------------------------------------------------------------------------------------
# Helper used by main() below to fix up the crop/zone alignment for input
# videos that aren't exactly 1280x720 - see its docstring for the full story
# -----------------------------------------------------------------------------------------------


def _actual_input_resolution(input_path):
    """Read the real width/height of the --input video file via OpenCV.

    GStreamerApp falls back to a hardcoded 1280x720 for self.video_width/
    self.video_height whenever --width/--height aren't passed on the CLI.
    center_crop_pipeline() uses those to compute crop pixel amounts, so for
    any input that isn't literally 1280x720 (e.g. the 640x480 clips in
    recorded_videos/) it crops the wrong number of pixels off each side,
    leaving a tiny off-looking sliver instead of a centered square."""
    if not input_path or not os.path.isfile(input_path):
        return None

    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        cap.release()
        return None
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return (width, height) if width > 0 and height > 0 else None


# -----------------------------------------------------------------------------------------------
# Entry point - wires everything above together and starts the pipeline.
# Run with: python3 processor2.py --input <video file or "usb"> --frame-rate 30
# -----------------------------------------------------------------------------------------------


def main():
    """Entry point - run as: python3 processor2.py --input <video file or "usb"> --frame-rate 30

    Ties everything above together, in order:
      1. Make sure the trained model (hef_path, from MODEL_DIR) actually
         gets used, by injecting --hef-path into sys.argv if it's missing.
      2. Pull --input/-i out of sys.argv (just to remember it for the
         recap later - GStreamerApp will also read it normally).
      3. If the caller didn't pass --width/--height, detect the real
         input video resolution via _actual_input_resolution() and inject
         it into sys.argv, so CenterCropDetectionApp.get_pipeline_string()
         crops the correct number of pixels for non-1280x720 videos.
      4. Build the user_app_callback_class (the per-run state, see its
         docstring above) and CenterCropDetectionApp, then call app.run()
         to start the actual GStreamer pipeline - this call blocks until
         the video ends or the user hits Ctrl+C.
      5. Whether the run finished normally or was interrupted, the
         `finally:` block below always writes the end-of-run recap
         (write_recap) and review slideshow (write_review_slideshow)
         before the program exits."""
    hailo_logger.info("Starting Detection App.")
    # hef_path (from MODEL_DIR/best.hef, computed above) was never reaching the
    # pipeline - the app was silently falling back to the stock yolov8m COCO
    # model. GStreamerDetectionApp reads --hef-path from sys.argv itself
    # (inside its own __init__), so inject it here if the user didn't pass
    # one explicitly, before the app parses args and builds the pipeline.
    if "--hef-path" not in sys.argv:
        sys.argv += ["--hef-path", hef_path]

    # Grab the --input value here (before GStreamerApp consumes argv) so the
    # recap can print which video this run was for.
    input_path = None
    for flag in ("--input", "-i"):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                input_path = sys.argv[idx + 1]
            break

    if not any(flag in sys.argv for flag in ("--width", "-W", "--height", "-H")):
        resolution = _actual_input_resolution(input_path)
        if resolution is not None:
            width, height = resolution
            hailo_logger.info("Detected input resolution %dx%d; using it for center-crop.", width, height)
            sys.argv += ["--width", str(width), "--height", str(height)]

    user_data = user_app_callback_class()
    app = CenterCropDetectionApp(app_callback, user_data)
    try:
        app.run()
    finally:
        write_recap(user_data, input_path)
        write_review_slideshow(user_data, input_path)


if __name__ == "__main__":
    main()

