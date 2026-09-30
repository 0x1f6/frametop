// Tracking-camera calibration from the headset's factory files (see tracker/calib.py
// for the conventions): Kannala-Brandt fisheye intrinsics, and each camera's pose in the
// head frame (OpenVR's: +x right, +y up, -z forward), metres.
#pragma once

#include "geom.h"

#include <map>
#include <string>
#include <vector>

struct Camera {
    std::string name;
    int width = 0, height = 0;
    double fx = 1, fy = 1, cx = 0, cy = 0, k[4] = {};
    double R[3][3] = {};   // camera axes (columns) in the head frame
    V3 origin{};           // camera centre in the head frame

    V2 project_cam(V3 p) const;               // camera frame -> pixels
    V3 unproject(V2 uv) const;                // pixels -> unit ray, camera frame
    V3 ray(V2 uv) const;                      // pixels -> unit ray, head frame
    V2 project(V3 head, double *depth) const; // head frame -> pixels; depth along the optical axis
    double off_axis(V2 uv) const;             // degrees between the pixel's ray and the axis
};

// Loads /persist/xrservice.json and /persist/device_config.json. Keyed by calibration
// name: slam_left, slam_right, upper_left, upper_right.
bool load_calibration(std::map<std::string, Camera> &out, std::string &err);

// The point closest to several rays (weighted), and its rms distance to them.
V3 triangulate(const V3 *origins, const V3 *dirs, const double *weights, int n, double *rms);
