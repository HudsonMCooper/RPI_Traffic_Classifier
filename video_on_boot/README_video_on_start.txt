======================================================================
 VIDEO ON START - button-triggered recorder (video_on_start.py)
======================================================================

WHAT THIS PROGRAM IS FOR
----------------------------------------------------------------------
video_on_start.py is used for starting a recording with a single press
of a physical button. This makes it easy to start a video out in the
field without needing a keyboard or monitor attached to the Raspberry
Pi.

It waits for a button press to start recording, lights an LED briefly
to confirm it started, then records video from the Raspberry Pi camera
until either the button is pressed again or the recording time limit
is reached - whichever happens first. The video is saved as an .mp4
file. The program only records once per run - it does not go back to
waiting for another "start" press afterward.


HOW TO USE IT (IN THE FIELD)
----------------------------------------------------------------------
1. Make sure the Raspberry Pi is powered off.
2. Power on the Raspberry Pi and wait around 15 seconds for it to boot
   up fully.
3. Press the button.
4. The LED will come on for a few seconds (to confirm recording
   started), then stay off while recording continues for a set amount
   of time (currently configured in the code - see RECORDING_MINUTES
   in video_on_start.py). Pressing the button again at any point while
   recording stops it early.
5. Close the lid carefully to avoid pinching any wires, and come back
   once the video is complete.
6. See README.txt in
   /home/cityofjackson/hailo-apps/traffic_counter/post_processor/
   for how to post-process the recorded video (find the timestamps
   where vehicles crossed the checkpoint).


WHERE THE VIDEO IS SAVED
----------------------------------------------------------------------
Recordings are saved to:
    /home/cityofjackson/hailo-apps/traffic_counter/recorded_videos/
named with the date and time the recording started, e.g.
    August_19_2026_09-15AM.mp4


HOW THIS PROGRAM STARTS AUTOMATICALLY (systemd)
----------------------------------------------------------------------
This program is set up to run automatically on boot using systemd -
the system service manager that can make a script run at start-up
without anyone needing to open a terminal.

To edit the systemd service, use:
    sudo nano /etc/systemd/system/MyService.service

Here's a breakdown of what's in that file:

[Unit]
Description=video at push of button   # quick description of the service
After=network-online.target           # wait until the Pi is online before starting

[Service]
Type=simple
User=cityofjackson                    # the user the script runs as - update this (and WorkingDirectory/ExecStart below) if the username ever changes
WorkingDirectory=/home/cityofjackson
ExecStart=/usr/bin/python3 /home/cityofjackson/hailo-apps/traffic_counter/video_on_boot/video_on_start.py
                                       # the program that gets run - always use the full path to avoid errors
Restart=on-failure                    # if the program crashes, systemd restarts it
RestartSec=10s                        # how long systemd waits before restarting

[Install]
WantedBy=multi-user.target            # run once the Pi is fully booted and ready for normal use

After changing anything in the service file, you must run:
    1. sudo systemctl daemon-reload
    2. sudo systemctl enable MyService.service
    3. sudo systemctl start MyService.service

To check that it's running correctly:
    sudo systemctl status MyService.service
Look for any red-colored error messages - if there are none, the
system should be ready to go.


HOW TO EDIT THE PROGRAM
----------------------------------------------------------------------
video_on_start.py has detailed comments above each section explaining
what it does, including which GPIO pins the button/LED are wired to
and where to change the recording length (RECORDING_MINUTES). As with
any script that's set up to run on boot, know what you're changing
before editing it, and consider testing on a copy first.


AFTER RECORDING
----------------------------------------------------------------------
Once a recording is finished, go to:
    /home/cityofjackson/hailo-apps/traffic_counter/post_processor
to begin post-processing the recorded video (see the README.txt there
for full instructions).
