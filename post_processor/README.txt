======================================================================
 TRAFFIC COUNTER - POST PROCESSOR (processor2.py)
======================================================================

WHAT THIS PROGRAM IS FOR
----------------------------------------------------------------------
This program is meant for post-processing traffic recordings to find
the tanker trucks and school buses (and other vehicle types) that
travel over a certain checkpoint in the video.

It runs an AI vehicle-detection model (via the Hailo AI chip) over a
recorded video file (or a live camera feed), watches a rectangular
"zone" in the middle of the frame like an invisible line vehicles have
to cross, and counts vehicles by type (SUV, Bus, Sedan, Fire engine,
Pickup truck, Semi Truck, School Bus, Tanker Truck, Van) whenever their
center point lands inside that zone.


HOW TO RUN IT
----------------------------------------------------------------------
1. Open the terminal (the black box in the top left).

2. Type:
       cd hailo-apps
   This puts the terminal in the folder we'll be working from.

3. Type:
       source setup_env.sh
   This sets up the device to begin working with AI recognition.

4. Type:
       python traffic_counter/post_processor/processor2.py --input video.mp4 --frame-rate 30
   This starts the program and tells it to use your video. Make sure
   your video file is somewhere under the hailo-apps folder so the
   computer can find it (recorded videos land in
   traffic_counter/recorded_videos/ by default if you used
   video_on_start.py to record them).

5. Let the computer run until it finishes processing the video (this
   can take a while for long recordings - overnight is fine) so you can
   review the results in the morning.


WHERE THE OUTPUT GOES
----------------------------------------------------------------------
Every run gets its own timestamped folder under
traffic_counter/traffic_count_data/, named run_<date>_<time>, so
re-running the program never overwrites a previous run's files. Inside
that folder you'll find:

  detections_log.txt     Every single in-zone detection, logged live
                          frame-by-frame while the program runs.
  summary.txt             End-of-run totals per vehicle type, plus a
                          timestamp list for the "notable" classes
                          (School Bus, Tanker Truck, Semi Truck).
  summary_chart.png       A bar chart of the totals in summary.txt.
  screenshots/            One .jpg screenshot per notable-class
                          timestamp, pulled from the source video.
  review_slideshow.html   A click-through review page (open this in a
                          browser) that lets you page through the
                          screenshots above with Next/Prev buttons or
                          the arrow keys - built so reviewing a run
                          means clicking through a handful of pictures
                          instead of scrubbing the whole video by hand.

Totals in summary.txt (and the timestamps used for the slideshow) are
deduped by a few-second time window, so a vehicle sitting in the zone
for several seconds is reported once, not dozens of times. The live
detections_log.txt is NOT deduped - it logs every frame a vehicle is
seen in the zone.


HOW TO EDIT THE PROGRAM
----------------------------------------------------------------------
Open the program using Thonny: right-click on processor2.py and select
Thonny.

The file has detailed comments above each chunk of code (including a
"quick reference" block right at the top) explaining what it does and
what's safe to tweak - things like which vehicle classes are called
out in the recap, how many seconds count as "the same vehicle", where
the AI model files live, where the counting zone is positioned, and
how confident the AI has to be before it counts a detection.

Be sure to save the program (top left in Thonny) before running it.

Please know what you're doing before changing anything, so as not to
mess up the working version. It's recommended to duplicate the program
(or use a copy) to test changes first, rather than editing the version
you rely on for real runs.


KNOWN LIMITATIONS
----------------------------------------------------------------------
As of 6/18/2026: this program can detect school buses pretty well,
but there are times when one school bus gets registered as two or
more separate detections. Because of this, the program is not fully
self-sufficient yet - a human still needs to review the footage
(the review_slideshow.html output is meant to make that quick).


IMPROVEMENT IDEAS
----------------------------------------------------------------------
- A better/larger training data set that can reliably tell apart more
  specific vehicle types, not just the current set - this would need a
  newly trained model for those vehicle types.

- Object tracking across multiple frames. Right now the AI reviews
  each frame independently rather than tracking a specific vehicle
  as it moves - giving each vehicle a persistent ID across frames
  would fix the "one vehicle counted as multiple" problem and would
  likely make the program self-sufficient (no human review needed).

- A better way to record video on the Raspberry Pi 5 without needing a
  keyboard and monitor. (This has since been solved - see
  video_on_boot/video_on_start.py and its README, which starts/stops
  a recording with a single physical button press.)
