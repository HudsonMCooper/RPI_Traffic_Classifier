# region imports
import os
import sys
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import argparse
import glob
import time

import gi
gi.require_version("Gst", "1.0")
import cv2

import hailo

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

hailo_logger = get_logger(__name__)
# endregion imports

# Deployment bundle for the model trained on real intersection-camera images
# (best_hailo_model/, which processor.py points at, doesn't exist on this
# machine - this is the model that's actually present).
DEPLOY_DIR = "/home/cityofjackson/hailo-apps/traffic_counter/rpi_deploy"
HEF_PATH = os.path.join(DEPLOY_DIR, "custom.hef")
LABELS_JSON_PATH = os.path.join(DEPLOY_DIR, "labels.json")

# Raw model labels (from rpi_deploy/labels.json) to save training frames for.
TARGET_LABELS = {"Semi Truck", "Tanker Truck", "School Bus"}
CONFIDENCE_THRESHOLD = 0.5

# Minimum seconds between two saved frames of the *same* class, so a truck
# sitting in view for several seconds doesn't produce a burst of near-duplicate
# training frames.
MIN_GAP_SECONDS = 3


def center_crop_pipeline(video_width, video_height, name="center_crop"):
    """Center-crop to a square before the model, matching this model's
    close-up/roughly-square training screenshots (see rpi_deploy/README.txt)."""
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


class user_app_callback_class(app_callback_class):
    def __init__(self, output_dir, segment_name, save_quota):
        super().__init__()
        self.output_dir = output_dir
        self.segment_name = segment_name
        self.save_quota = save_quota
        self.saved_count = 0
        self.last_saved_time = {}
        self.app = None  # set after CenterCropDetectionApp is constructed


def label_slug(label):
    return label.lower().replace(" ", "_")


def app_callback(element, buffer, user_data):
    if buffer is None:
        return

    frame_idx = user_data.get_count()
    user_data.use_frame = True
    pad = element.get_static_pad("src")
    format, width, height = get_caps_from_pad(pad)

    if not (format is not None and width is not None and height is not None):
        return

    roi = hailo.get_roi_from_buffer(buffer)
    detections = roi.get_objects_typed(hailo.HAILO_DETECTION)

    total_seconds = frame_idx / 30.0
    frame = None

    for detection in detections:
        label = detection.get_label()
        confidence = detection.get_confidence()

        if label not in TARGET_LABELS or confidence < CONFIDENCE_THRESHOLD:
            continue

        last_saved = user_data.last_saved_time.get(label, -1e9)
        if total_seconds - last_saved < MIN_GAP_SECONDS:
            continue

        if frame is None:
            frame = get_numpy_from_buffer(buffer, format, width, height)
            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        out_path = os.path.join(
            user_data.output_dir,
            f"{label_slug(label)}__{user_data.segment_name}__f{frame_idx:06d}__c{int(confidence * 100):03d}.jpg",
        )
        cv2.imwrite(out_path, frame)
        user_data.last_saved_time[label] = total_seconds
        user_data.saved_count += 1
        print(f"  saved [{user_data.saved_count}/{user_data.save_quota}] {label} ({confidence:.2f}) -> {out_path}")

        if user_data.saved_count >= user_data.save_quota:
            print("  quota reached for this segment, shutting down pipeline early")
            if user_data.app is not None:
                user_data.app.shutdown()
            break


def _actual_input_resolution(input_path):
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
    arg_parser = argparse.ArgumentParser(add_help=False)
    arg_parser.add_argument("--input", required=True)
    arg_parser.add_argument("--output-dir", required=True)
    arg_parser.add_argument("--max-total", type=int, default=100)
    known_args, remaining = arg_parser.parse_known_args()

    os.makedirs(known_args.output_dir, exist_ok=True)
    existing = len(glob.glob(os.path.join(known_args.output_dir, "*.jpg")))
    remaining_quota = known_args.max_total - existing
    if remaining_quota <= 0:
        print(f"Already have {existing} frames in {known_args.output_dir}, nothing to do.")
        return

    segment_name = os.path.splitext(os.path.basename(known_args.input))[0]

    sys.argv = [sys.argv[0]] + remaining
    sys.argv += ["--input", known_args.input]
    sys.argv += ["--hef-path", HEF_PATH]
    sys.argv += ["--labels-json", LABELS_JSON_PATH]
    sys.argv += ["--disable-sync"]

    resolution = _actual_input_resolution(known_args.input)
    if resolution is not None and not any(f in sys.argv for f in ("--width", "-W", "--height", "-H")):
        width, height = resolution
        sys.argv += ["--width", str(width), "--height", str(height)]

    user_data = user_app_callback_class(known_args.output_dir, segment_name, remaining_quota)
    app = CenterCropDetectionApp(app_callback, user_data)
    app.video_sink = "fakesink"  # headless: we only need the callback, not a display
    user_data.app = app
    app.run()


if __name__ == "__main__":
    main()
