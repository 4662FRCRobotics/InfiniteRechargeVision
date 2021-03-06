#!/usr/bin/env python3
#----------------------------------------------------------------------------
# Copyright (c) 2018 FIRST. All Rights Reserved.
# Open Source Software - may be modified and shared by FRC teams. The code
# must be accompanied by the FIRST BSD license file in the root directory of
# the project.
#----------------------------------------------------------------------------

import json
import time
import sys
import cv2
import random
import math

import numpy as np

from cscore import CameraServer, VideoSource, UsbCamera, MjpegServer
from networktables import NetworkTablesInstance
import ntcore

from grip_high_goal import Pipeline as HighGoalPipeline
from grip_loading_station import Pipeline as LoadingStationPipeline

#   JSON format:
#   {
#       "team": <team number>,
#       "ntmode": <"client" or "server", "client" if unspecified>
#       "cameras": [
#           {
#               "name": <camera name>
#               "path": <path, e.g. "/dev/video0">
#               "pixel format": <"MJPEG", "YUYV", etc>   // optional
#               "width": <video mode width>              // optional
#               "height": <video mode height>            // optional
#               "fps": <video mode fps>                  // optional
#               "brightness": <percentage brightness>    // optional
#               "white balance": <"auto", "hold", value> // optional
#               "exposure": <"auto", "hold", value>      // optional
#               "properties": [                          // optional
#                   {
#                       "name": <property name>
#                       "value": <property value>
#                   }
#               ],
#               "stream": {                              // optional
#                   "properties": [
#                       {
#                           "name": <stream property name>
#                           "value": <stream property value>
#                       }
#                   ]
#               }
#           }
#       ]
#       "switched cameras": [
#           {
#               "name": <virtual camera name>
#               "key": <network table key used for selection>
#               // if NT value is a string, it's treated as a name
#               // if NT value is a double, it's treated as an integer index
#           }
#       ]
#   }

configFile = "/boot/frc.json"

class CameraConfig: pass

class CameraObject:  # Camera object to help with vision
    def __init__(self, config):
        inst = CameraServer.getInstance()
        camera = startCamera(config)
        w, h = 160, 120
        self.name = config.name
        self.source_name = config.name + " source"
        self.config = config
        self.camera = camera
        self.w = w
        self.h = h
        self.source = inst.putVideo(self.source_name, w, h)
        self.sink = inst.getVideo(name=self.name)
        self.img = np.zeros((h, w, 3), dtype=np.uint8)
        self.time = 0

    def updateFrame(self):
        self.time, self.img = self.sink.grabFrame(self.img)
        return self.time
    
    def getFrame(self):
        self.time, self.img = self.sink.grabFrame(self.img)
        return self.time, self.img

    def putFrame(self, img=None):
        if img is not None:
            self.img = img
        self.source.putFrame(self.img)

class HighGoal:
    def __init__(self, data=None):
        self.update(data)

    def update(self, data=None):
        self.data = data
        if data is None:
            self.x = None
            self.y = None
            self.w = None
            self.h = None
            self.center_x = None
            self.center_y = None
        else:
            x, y, w, h = data
            self.x = x
            self.y = y
            self.w = w
            self.h = h
            self.center_x = x + w / 2
            self.center_y = y + h / 2

team = None
server = False
cameraConfigs = []
switchedCameraConfigs = []
cameras = []

def parseError(str):
    """Report parse error."""
    print("config error in '" + configFile + "': " + str, file=sys.stderr)

def readCameraConfig(config):
    """Read single camera configuration."""
    cam = CameraConfig()

    # name
    try:
        cam.name = config["name"]
    except KeyError:
        parseError("could not read camera name")
        return False

    # path
    try:
        cam.path = config["path"]
    except KeyError:
        parseError("camera '{}': could not read path".format(cam.name))
        return False

    # stream properties
    cam.streamConfig = config.get("stream")

    cam.config = config

    cameraConfigs.append(cam)
    return True

def readSwitchedCameraConfig(config):
    """Read single switched camera configuration."""
    cam = CameraConfig()

    # name
    try:
        cam.name = config["name"]
    except KeyError:
        parseError("could not read switched camera name")
        return False

    # path
    try:
        cam.key = config["key"]
    except KeyError:
        parseError("switched camera '{}': could not read key".format(cam.name))
        return False

    switchedCameraConfigs.append(cam)
    return True

def readConfig():
    """Read configuration file."""
    global team
    global server

    # parse file
    try:
        with open(configFile, "rt", encoding="utf-8") as f:
            j = json.load(f)
    except OSError as err:
        print("could not open '{}': {}".format(configFile, err), file=sys.stderr)
        return False

    # top level must be an object
    if not isinstance(j, dict):
        parseError("must be JSON object")
        return False

    # team number
    try:
        team = j["team"]
    except KeyError:
        parseError("could not read team number")
        return False

    # ntmode (optional)
    if "ntmode" in j:
        str = j["ntmode"]
        if str.lower() == "client":
            server = False
        elif str.lower() == "server":
            server = True
        else:
            parseError("could not understand ntmode value '{}'".format(str))

    # cameras
    try:
        cameras = j["cameras"]
    except KeyError:
        parseError("could not read cameras")
        return False
    for camera in cameras:
        if not readCameraConfig(camera):
            return False

    # switched cameras
    if "switched cameras" in j:
        for camera in j["switched cameras"]:
            if not readSwitchedCameraConfig(camera):
                return False

    return True

def startCamera(config):
    """Start running the camera."""
    print("Starting camera '{}' on {}".format(config.name, config.path))
    inst = CameraServer.getInstance()
    camera = UsbCamera(config.name, config.path)
    server = inst.startAutomaticCapture(camera=camera, return_server=False)

    camera.setConfigJson(json.dumps(config.config))
    camera.setConnectionStrategy(VideoSource.ConnectionStrategy.kKeepOpen)

    if config.streamConfig is not None:
        server.setConfigJson(json.dumps(config.streamConfig))

    return camera

def startSwitchedCamera(config):
    """Start running the switched camera."""
    print("Starting switched camera '{}' on {}".format(config.name, config.key))
    server = CameraServer.getInstance().addSwitchedCamera(config.name)

    def listener(fromobj, key, value, isNew):
        if isinstance(value, float):
            i = int(value)
            if i >= 0 and i < len(cameras):
              server.setSource(cameras[i])
        elif isinstance(value, str):
            for i in range(len(cameraConfigs)):
                if value == cameraConfigs[i].name:
                    server.setSource(cameras[i])
                    break

    NetworkTablesInstance.getDefault().getEntry(config.key).addListener(
        listener,
        ntcore.constants.NT_NOTIFY_IMMEDIATE |
        ntcore.constants.NT_NOTIFY_NEW |
        ntcore.constants.NT_NOTIFY_UPDATE)

    return server

CAM_FOV_Y = 44
TARGET_HEIGHT = 17
CAMERA_OFFSET = 32
deg_per_pixel = None

def calculate_distance(target_pixel_height):
    radians = math.radians(deg_per_pixel * target_pixel_height)
    distance = TARGET_HEIGHT / math.tan(radians)

    return distance

if __name__ == "__main__":

    inst = CameraServer.getInstance()

    if len(sys.argv) >= 2:
        configFile = sys.argv[1]

    # read configuration
    if not readConfig():
        sys.exit(1)

    # start NetworkTables
    ntinst = NetworkTablesInstance.getDefault()
    if server:
        print("Setting up NetworkTables server")
        ntinst.startServer()
    else:
        print("Setting up NetworkTables client for team {}".format(team))
        ntinst.startClientTeam(team)

    # start cameras
    for config in cameraConfigs: 
        camera = CameraObject(config)
        cameras.append(camera)

    # start switched cameras
    for config in switchedCameraConfigs:
        startSwitchedCamera(config)

    PI_ADDRESS = "10.46.62.10"
    num_cameras = len(cameras)

    cam0, cam1 = cameras
    g_LoadingStation = LoadingStationPipeline()
    g_HighGoal = HighGoalPipeline()

    print("Getting vision table")
    visionTable = ntinst.getTable("Vision")
    print("Got vision table")

    e_loadingStationAligned = visionTable.getEntry("isLoadingStationAligned")
    e_highGoalAligned = visionTable.getEntry("isHighGoalAligned")

    e_highGoalOffset = visionTable.getEntry("highGoalOffset")
    e_highGoalDistance = visionTable.getEntry("highGoalDistance")
    e_highGoalIsVisible = visionTable.getEntry("highGoalVisible")

    e_visionOn = visionTable.getEntry("isVisionOn")

    highGoal = HighGoal()

    deg_per_pixel = CAM_FOV_Y / cam0.h

    print("Looping")
    # loop forever
    while True:
        bIsVisionOn = e_visionOn.getBoolean(False)

        time0 = cam0.updateFrame()
        time1 = cam1.updateFrame()

        if time0 == 0:
            continue
        
        g_HighGoal.process(cam1.img)

        highGoalMatches = g_HighGoal.filter_contours_output

        numHighGoalMatches = len(highGoalMatches)
        if 1 <= numHighGoalMatches <= 2:
            e_highGoalVisible.setBoolean(True)

            # First and largest match
            r1 = cv2.boundingRect(highGoalMatches[0])
            x, y, w, h = r1
            cv2.rectangle(cam1.img, (x, y), (x + w, y + h), (255, 0, 0))
            highGoal.update(r1)

            for highGoalMatch in highGoalMatches[1:]:
                r1 = cv2.boundingRect(highGoalMatch)
                x, y, w, h = r1
                cv2.rectangle(cam1.img, (x, y), (x + w, y + h), (255, 255, 0))

            highGoal.offset = highGoal.center_x - (cam1.w / 2)
            highGoal.distance = calculate_distance(highGoal.h)
            
            #highGoal_x_avg = highGoal_x_sum / num_highGoalMatches
            #isHighGoalAligned = 

            e_highGoalAligned.setBoolean(abs(highGoal.offset) < 10)
            e_highGoalOffset.setNumber(highGoal.offset)
            e_highGoalDistance.setNumber(highGoal.distance)
        else:
            e_highGoalVisible.setBoolean(False)
            for entry in (e_loadingStationAligned, e_highGoalAligned):
                entry.setBoolean(False)
        
        # cv2.putText(cam1.img, ''.join(map(chr, [73, 32, 108, 111, 115, 116, 32, 116, 104, 101, 32, 103, 97, 109, 101])), (1, 50), cv2.FONT_HERSHEY_TRIPLEX, 0.5, (0, 0, 0))
        
        cam0.putFrame()
        cam1.putFrame()
