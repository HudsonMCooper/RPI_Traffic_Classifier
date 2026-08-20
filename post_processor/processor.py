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



#creates a variable for the filename. If want to chnage filename then edit this line
# Each run gets its own timestamped subfolder so that
# starting the program again never overwrites a previous run's log/summary.
OUTPUT_BASE_DIR = "/home/cityofjackson//hailo-apps/traffic_counter/traffic_count_data"
RUN_START = datetime.now()  # also used for the recap's "Review"/"Date" header
RUN_DIR = os.path.join(OUTPUT_BASE_DIR, f"run_{RUN_START.strftime('%Y-%m-%d_%H-%M')}")
os.makedirs(RUN_DIR, exist_ok=True)

filename = os.path.join(RUN_DIR, "detections_log.txt")
SUMMARY_TXT_PATH = os.path.join(RUN_DIR, "summary.txt")
SUMMARY_CHART_PATH = os.path.join(RUN_DIR, "summary_chart.png")

# Classes that get their own individual timestamp listing in the end-of-run
# recap (in addition to the totals bar chart, which always covers all 9).
# Edit this list to change which classes matter enough to call out - e.g.
# swap to ["Sedan", "SUV"] if trucks stop being the thing you care about.
# Must use the display names below, not the raw model labels
# ("School Bus"/"Tanker Truck"/"Semi Truck", not "school bus"/etc.):
#   SUV, Bus, Sedan, Fire engine, Pickup truck,
#   Semi Truck, School Bus, Tanker Truck, Van
NOTABLE_CLASSES = ["School Bus", "Tanker Truck", "Semi Truck"]

# Single source of truth for every vehicle class this app knows about, in
# place of what used to be 9 nearly-identical `if label == "...":` blocks in
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

# Size (in seconds) of the window used to dedup the end-of-run recap: a
# vehicle sitting in the zone across multiple frames within the same window
# only counts once. At 30fps a vehicle in the zone for ~2 seconds would
# otherwise log ~60 raw per-frame hits - grouping into DEDUP_WINDOW_SECONDS
# windows collapses that back down to roughly "1 vehicle", while a vehicle
# that lingers past the window still registers again for the next one.
DEDUP_WINDOW_SECONDS = 2


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

# hef_path = "/home/cityofjackson/best_hailo_model/best.hef"

import yaml

#AI model path
MODEL_DIR = "/home/cityofjackson/best_hailo_model"

with open(f"{MODEL_DIR}/metadata.yaml") as f:
    data = yaml.safe_load(f)

class_names = data["names"]

hef_path = f"{MODEL_DIR}/best.hef"




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
        tracker_pipeline = TRACKER_PIPELINE(class_id=1)
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
    def __init__(self):
        super().__init__()
        self.new_variable = 42 #idk what this is for
        
#         self.detection_count_car = 0
#         self.detection_count_truck = 0
#         self.detection_count_bus = 0
        
        #initialize new variables
        for _, count_attr, _, _, _ in VEHICLE_CLASSES:
            setattr(self, count_attr, 0)
        
    #want to make a zone where if a bus or car goes through then it will add to its tally
        self.zone_x_min = 0.17
        self.zone_y_min = 0
        self.zone_x_max = 0.49
        self.zone_y_max = 1
        
    #Debouncing variables/cooldown
        self.in_zone_frames = 0  #consecutive frames with object in zone
        self.out_zone_frames = 0 #consecutive frames without object in zone

    #variable for bus, car, and truck count that go over the line
#         self.car_total_count = 0
#         self.bus_total_count = 0 
#         self.truck_total_count = 0
        
        
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
        
    #variable to calculate the time of the video
        self.time = 0
        self.live_time = 0
    #def new_function(self):        #idk what this does so I commented it out

        #set variables for minutes and seconds
        self.total_seconds = 0
        self.minutes = 0
        self.seconds = 0

        # Distinct DEDUP_WINDOW_SECONDS-sized windows (as an int bucket
        # index) each class was seen in the zone at least once - this is
        # what the end-of-run recap counts from, so a vehicle sitting in
        # the zone for many frames within one window is reported once, not
        # dozens of times. The live per-frame prints/log below are
        # untouched and still fire every single frame like before.
        self.seconds_seen = {count_attr: set() for _, count_attr, _, _, _ in VEHICLE_CLASSES}

# -----------------------------------------------------------------------------------------------
# User-defined callback function
# -----------------------------------------------------------------------------------------------


def app_callback(element, buffer, user_data):
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

            if confidence <= 0.3:
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

            #calculate center point of car(normalized to 0-1)
            center_x = x_min + (box_width / 2)
            center_y = y_min + (box_height / 2)

            #check is objects center is in target zone
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
            print("  Time:", minutes, ":", seconds, end='')
            print(" ", message, end='')

            #print the car data onto a file incase of unintentional shutdown
            with open(filename, 'a') as f:
                print(f"  {print_prefix} #:", count, file=f)
                print("  Time:", hours, ":", minutes, ":", seconds, file=f)
                print(" ", message, file=f)


def write_recap(user_data, input_path=None):
    """End-of-run recap: totals for every class (as text + a bar chart), and
    the full list of timestamps for the notable/rare classes. Totals here
    are deduped by DEDUP_WINDOW_SECONDS-second window (via
    user_data.seconds_seen) rather than the raw per-frame counts, so a
    vehicle sitting in the zone for many frames within one window is
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

    for cls in NOTABLE_CLASSES:
        count_attr = NOTABLE_NAME_TO_COUNT_ATTR[cls]
        stamps = [
            format_timestamp(bucket * DEDUP_WINDOW_SECONDS)
            for bucket in sorted(user_data.seconds_seen[count_attr])
        ]
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


def _actual_input_resolution(input_path):
    """Read the real width/height of the --input video file via OpenCV.

    GStreamerApp falls back to a hardcoded 1280x720 for self.video_width/
    self.video_height whenever --width/--height aren't passed on the CLI.
    center_crop_pipeline() uses those to compute crop pixel amounts, so for
    any input that isn't literally 1280x720 (e.g. the 640x480 clips in
    recorded_videos/) it crops the wrong number of pixels off each side,
    leaving a tiny off-center sliver instead of a centered square."""
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


def main():
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


if __name__ == "__main__":
    main()

