import os
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

import gi
gi.require_version("Gst", "1.0")

import yaml
from datetime import datetime

import hailo

from gi.repository import Gst

from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp

from hailo_apps.python.core.common.buffer_utils import (
    get_caps_from_pad,
    get_numpy_from_buffer,
)

from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class


# -------------------------------------------------
# Model files
# -------------------------------------------------

MODEL_DIR = "/home/cityofjackson/best_hailo_model"

with open(f"{MODEL_DIR}/metadata.yaml") as f:
    metadata = yaml.safe_load(f)

class_names = metadata["names"]

# convert yaml keys to usable labels
class_names = {
    int(k): v for k, v in class_names.items()
}

print("Loaded classes:")
for k, v in class_names.items():
    print(k, v)


filename = (
    f"/home/cityofjackson/traffic_count_data/"
    f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}.txt"
)


hailo_logger = get_logger(__name__)


# -------------------------------------------------
# User callback class
# -------------------------------------------------

class user_app_callback_class(app_callback_class):

    def __init__(self):
        super().__init__()

        # Create counters for all classes automatically
        self.class_counts = {
            name: 0 for name in class_names.values()
        }


        # Detection zone
        self.zone_x_min = 0.49
        self.zone_x_max = 0.51

        self.zone_y_min = 0
        self.zone_y_max = 1



# -------------------------------------------------
# Callback
# -------------------------------------------------

def app_callback(element, buffer, user_data):

    if buffer is None:
        hailo_logger.warning("Received None buffer.")
        return


    frame_idx = user_data.get_count()

    user_data.use_frame = True


    pad = element.get_static_pad("src")

    fmt, width, height = get_caps_from_pad(pad)


    if frame_idx % 3 != 0:
        return


    if (
        user_data.use_frame
        and fmt is not None
        and width is not None
        and height is not None
    ):
        frame = get_numpy_from_buffer(
            buffer,
            fmt,
            width,
            height
        )


    roi = hailo.get_roi_from_buffer(buffer)

    detections = roi.get_objects_typed(
        hailo.HAILO_DETECTION
    )


    for detection in detections:

        print(
            "LABEL:",
            detection.get_label(),
            "CONF:",
            detection.get_confidence()
        )
        confidence = detection.get_confidence()
        class_id = detection.get_class_id()
        print("RAW CLASS ID:", class_id)
        label = detection.get_label()    
        if confidence < 0.30:
            continue
        print(
            "LABEL:",
            label,
            "ID:",
            detection.get_class_id(),
            "CONF:",
            confidence
        )




        # Ignore unknown labels
        if label not in user_data.class_counts:
            print(
                "Unknown label:",
                label
            )
            continue



        bbox = detection.get_bbox()


        x_min = bbox.xmin()
        y_min = bbox.ymin()

        box_width = bbox.width()
        box_height = bbox.height()


        center_x = x_min + (box_width / 2)

        center_y = (
            y_min + (box_height / 2) * 0.22
        ) * 1.83



        # Check line/zone crossing

        if (
            user_data.zone_x_min <= center_x <= user_data.zone_x_max
            and
            user_data.zone_y_min <= center_y <= user_data.zone_y_max
        ):


            user_data.class_counts[label] += 1


            total_seconds = frame_idx / 30

            minutes = int(total_seconds // 60)

            seconds = int(total_seconds % 60)



            output = (
                f"{label}: "
                f"{user_data.class_counts[label]}"
                f"   Time {minutes}:{seconds:02d}"
                f"   Confidence {confidence:.2f}"
            )


            print(output)


            with open(filename, "a") as f:
                f.write(output + "\n")



# -------------------------------------------------
# Main
# -------------------------------------------------

def main():

    hailo_logger.info(
        "Starting Detection App."
    )


    user_data = user_app_callback_class()


    app = GStreamerDetectionApp(
        app_callback,
        user_data
    )


    app.run()



if __name__ == "__main__":
    main()