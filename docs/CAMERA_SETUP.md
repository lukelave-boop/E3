# Logitech C920 setup

## Mounting

Mount the camera to the enclosure or a rigid structure independent of the moving bed and gantry. Centering the lens over the bed is helpful but not mandatory. Rigidity and repeatability matter more than perfect visual alignment.

Do not rely on the C920 monitor clip. Use its tripod thread or a rigid printed cradle. Mark the mount so accidental movement is obvious.

## Stable Linux device name

Prefer `/dev/v4l/by-id/...` over `/dev/video0`, because video indices can change after reboots or when another camera is connected.

```bash
ls -l /dev/v4l/by-id/
python tools/camera_probe.py
```

Put the stable path in `config/local.json`.

## Resolution and compression

The default requests MJPEG at 1920 × 1080 and 15 frames/s. Positioning uses still images, so a high live-frame rate is unnecessary. MJPEG normally reduces USB bandwidth compared with uncompressed 1080p video.

On supported Linux persistent device paths, E3 uses a native V4L2 MMAP reader
so the sole camera stream can retain the original MJPEG packet for the remote
Live Monitor and decode it once for normal camera consumers. OpenCV's
`CAP_PROP_FORMAT=-1` raw mode is not used; physical C920 testing showed that it
still returned decoded BGR frames on the target Pi.

Inspect formats actually exposed by the camera:

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext
```

## Focus and exposure

The default configuration contains aliases because C920 revisions and Linux kernels do not expose identical control names. The application lists the available controls, applies only matching names, and reports skipped controls.

Inspect the camera directly:

```bash
v4l2-ctl -d /dev/video0 --list-ctrls-menus
```

Use **Test focus range…** on the Camera page to compare manual focus values
without disturbing the active calibration. The test takes three fresh
measurements per value, reports median sharpness, and restores the original
focus afterward. Keep the scene, lighting, camera pose, and work-plane height
unchanged while comparing values; scores from different scenes are not
comparable.

**Apply** changes the live focus for inspection. **Save focus** writes the
selected locked focus as the startup setting. Neither action reuses the sweep's
temporary values. Calibration is stored in a separate profile for each
configured resolution and manual-focus value. Restart after **Save focus** to
activate the matching profile. A new focus starts with an empty calibration
stack; complete Lens, Bed mapping, Fine registration, and Accuracy validation
once for that focus. Returning to a previously calibrated focus and restarting
reopens its saved lens model, bed map, corrections, validation sessions, and
honeycomb reference rather than overwriting them.

The profile key cannot detect a physically moved camera. A camera remount or a
change in work-plane height still requires a fresh calibration even when the
resolution and focus number match an existing profile.

## Lighting

Use fixed, diffuse illumination attached to the enclosure. Avoid sunlight changes, bright point reflections, and gantry shadows. Automatic workpiece detection is optional; manual design placement remains available when edges are difficult to detect.
