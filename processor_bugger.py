# region imports
# Standard library imports
import os
os.environ["GST_PLUGIN_FEATURE_RANK"] = "vaapidecodebin:NONE"

# Third-party imports
import gi

gi.require_version("Gst", "1.0")
import cv2

# Local application-specific imports
import hailo

#need timestamps
import time

from gi.repository import Gst

from hailo_apps.python.pipeline_apps.detection.detection_pipeline import GStreamerDetectionApp
from hailo_apps.python.core.common.buffer_utils import (
    get_caps_from_pad,
    get_numpy_from_buffer,
)

from hailo_apps.python.core.common.hailo_logger import get_logger
from hailo_apps.python.core.gstreamer.gstreamer_app import app_callback_class

hailo_logger = get_logger(__name__)
# endregion imports


# -----------------------------------------------------------------------------------------------
# User-defined class to be used in the callback function
# -----------------------------------------------------------------------------------------------
class user_app_callback_class(app_callback_class):
    def __init__(self):
        super().__init__()
        self.new_variable = 42 #idk what this is for
        
        self.detection_count_car = 0
        self.detection_count_truck = 0
        self.detection_count_bus = 0
        
    #want to make a zone where if a bus or car goes through then it will add to its tally
        self.zone_x_min = 0.48
        self.zone_y_min = 0
        self.zone_x_max = 0.52
        self.zone_y_max = 1
        
    #Debouncing variables/cooldown
        self.in_zone_frames = 0  #consecutive frames with object in zone
        self.out_zone_frames = 0 #consecutive frames without object in zone
        
    #variable for bus, car, and truck count that go over the line
        self.car_total_count = 0
        self.bus_total_count = 0 
        self.truck_total_count = 0
        
    #variable to calculate the time of the video
        self.time = 0
        self.live_time = 0
        
    #def new_function(self):        #idk what this does so I commented it out
        #return "The meaning of life is: "


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
        
        object_in_zone = False
        detection_string_car = "" #initializes variable
        detection_string_truck = ""
        detection_string_bus = ""
    
        for detection in detections:
            label = detection.get_label()            #gets the labels of all objects in frame
            confidence = detection.get_confidence()  #gets the confidence of all objects recognized
        
            if confidence > 0.4:

                    
                if label == "car":
                    
                    #get bounding box coordinates
                    bbox = detection.get_bbox()
                    
                    #call the coordiante methods
                    x_min = bbox.xmin()
                    y_min = bbox.ymin()
                    box_width = bbox.width()
                    box_height = bbox.height()
                    
                    #calculate max coordinates
                    x_max = x_min + box_width
                    y_max = y_min + box_height
                    
                    #calculate center point of car(normalized to 0-1)
                    center_x = x_min + (box_width / 2)
                    center_y = (y_min + (box_height / 2) * 0.22) * 1.83
                    
                    #debug print for coordinates
#                     detection_string_car += (f"{label.capitalize()} detected!\n"
#                                         f"position: center=({center_x:.2f}, {center_y:.2f})\n"
#                                         f"Bounds: xmin={x_min:.2f}, ymin={y_min:.2f}, xmax={x_max:.2f}, ymax={y_max:.2f}\n"
#                                         f"Confidence: {confidence:.2f}\n"
#                                         f"Box Width: {box_width:.2f}, Box Height: {box_height:.2f}\n"
#                                          )
                    
                    #check is objects center is in target zone
                    if (user_data.zone_x_min <= center_x <= user_data.zone_x_max and user_data.zone_y_min <= center_y <= user_data.zone_y_max):
                        object_in_zone = True
                        detection_string_car += f"car is in zone\n"
                    
                    
                        #counter for each car, this will loop until each car is accounted for
                        user_data.detection_count_car += 1
        
#                     keep updating us on how many total cars there are
#                     string_to_print += (
#                         f"Label: {label} Confidence: {confidence:.2f} Car Count: {detection_count_car}\n" UNDO THIS TOP AND BOTTOM LINE FOR LIVE FRAME COUNT
#                     )

                if label == "truck":
                    
                    #get the box aroumnd the ai
                    bbox = detection.get_bbox()
                    
                    #call the coordiante methods
                    x_min = bbox.xmin()
                    y_min = bbox.ymin()
                    box_width = bbox.width()
                    box_height = bbox.height()
                    
                    #calculate max coordinates
                    x_max = x_min + box_width
                    y_max = y_min + box_height
                    
                    #calculate center point of car(normalized to 0-1)
                    center_x = x_min + (box_width / 2)
                    center_y = (y_min + (box_height / 2) * 0.22) * 1.83
                    
                    #debug print for coordinates
#                     detection_string += (f"{label.capitalize()} detected!\n"
#                                         f"position: center=({center_x:.2f}, {center_y:.2f})\n"
#                                         f"Bounds: xmin={x_min:.2f}, ymin={y_min:.2f}, xmax={x_max:.2f}, ymax={y_max:.2f}\n"
#                                         f"Confidence: {confidence:.2f}\n"
#                                         f"Box Width: {box_width:.2f}, Box Height: {box_height:.2f}\n"
#                                          )
                    
                    #check is objects center is in target zone
                    if (user_data.zone_x_min <= center_x <= user_data.zone_x_max and user_data.zone_y_min <= center_y <= user_data.zone_y_max):
                        object_in_zone = True
                        detection_string_truck += f"truck is in zone\n"
                        
                        user_data.detection_count_truck += 1
                    #string_to_print += (
                    #    f"Label: {label} Confidence: {confidence:.2f} Truck Count: {detection_count_truck}\
                    #)

                if label == "bus":
                    
                                        
                    #get the box aroumnd the ai
                    bbox = detection.get_bbox()
                    
                    #call the coordiante methods
                    x_min = bbox.xmin()
                    y_min = bbox.ymin()
                    box_width = bbox.width()
                    box_height = bbox.height()
                    
                    #calculate max coordinates
                    x_max = x_min + box_width
                    y_max = y_min + box_height
                    
                    #calculate center point of car(normalized to 0-1)
                    center_x = x_min + (box_width / 2)
                    center_y = (y_min + (box_height / 2) * 0.22) * 1.83
                    
#                     debug print for coordinates
#                     detection_string += (f"{label.capitalize()} detected!\n"
#                                         f"position: center=({center_x:.2f}, {center_y:.2f})\n"
#                                         f"Bounds: xmin={x_min:.2f}, ymin={y_min:.2f}, xmax={x_max:.2f}, ymax={y_max:.2f}\n"
#                                         f"Confidence: {confidence:.2f}\n"
#                                         f"Box Width: {box_width:.2f}, Box Height: {box_height:.2f}\n"
#                                          )
                    
                    #check is objects center is in target zone
                    if (user_data.zone_x_min <= center_x <= user_data.zone_x_max and user_data.zone_y_min <= center_y <= user_data.zone_y_max):
                        object_in_zone = True
                        detection_string_bus += f"bus is in zone\n"
                        
                        user_data.detection_count_bus += 1
#                     string_to_print += (
#                         f"Label: {label} Confidence: {confidence:.2f} Bus Count: {detection_count_bus}\n"
#                     )
    
    
#         print(string_to_print)
        if detection_string_car:
            print("  Car #:", user_data.detection_count_car, end='')
            print("  Time:", (frame_idx / 30 ), end='')
            print(" ", detection_string_car, end='')
            
            
        if detection_string_bus:
            print("  Car #:", user_data.detection_count_bus, end='')
            print("  Time:", (frame_idx / 30 ), end='')
            print(" ", detection_string_bus, end='')
            
            
        if detection_string_truck:
            print("  Car #:", user_data.detection_count_truck, end='')
            print("  Time:", (frame_idx / 30 ), end='')
            print(" ", detection_string_truck, end='')


def main():
    hailo_logger.info("Starting Detection App.")
    user_data = user_app_callback_class()
    app = GStreamerDetectionApp(app_callback, user_data)
    app.run()


if __name__ == "__main__":
    main()


