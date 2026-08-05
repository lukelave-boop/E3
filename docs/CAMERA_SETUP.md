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

Use the Camera page to apply the configured controls. Adjust `focus_absolute` and exposure values in `config/local.json` until the entire work plane is sharp and highlights are not clipped. Recalibrate the lens after any optical change, focus change that moves lens elements substantially, resolution change, or camera remount.

## Lighting

Use fixed, diffuse illumination attached to the enclosure. Avoid sunlight changes, bright point reflections, and gantry shadows. Automatic workpiece detection is optional; manual design placement remains available when edges are difficult to detect.
