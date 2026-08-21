# Linux installation

The supported starting point is a normal graphical Linux Mint, Ubuntu, or Debian installation on a 64-bit Intel/AMD computer.

Linux supports direct local hardware. Windows supports authenticated Raspberry
Pi bridge endpoints; the remaining platform boundary is recorded in
[../CURRENT_STATE.md](../CURRENT_STATE.md). It does not change these Linux
installation instructions.

## 1. Test the old computer before erasing it

Boot the Linux installer USB in live mode and confirm that display, keyboard, mouse, Ethernet/Wi-Fi, the C920, and the internal drive are visible. The project itself does not require a dedicated GPU.

Useful live-session checks:

```bash
lscpu
free -h
lsblk
lsusb
```

## 2. Install the project

```bash
git clone YOUR_REPOSITORY_URL laser-camera-aligner
cd laser-camera-aligner
./install.sh
```

The installer creates an isolated `.venv`, installs the tested Python dependencies (including the OpenCV contrib build needed for ArUco support), installs this repository in editable mode, runs the test suite, creates a real-machine setup template at `config/local.json`, and adds the current user to the `video` and `dialout` groups.

Python 3.10 or newer is required. The installer stops with a clear message rather than partially installing on an older Linux release.

Log out and back in after a group change.

## 3. Configure and start E3

```bash
./run.sh
```

Complete real machine/controller and camera endpoint configuration before
starting. Open `http://127.0.0.1:8080` if the browser does not open automatically.
Unavailable hardware remains visibly offline; E3 does not substitute fake devices.

## 4. Common permission checks

```bash
groups
ls -l /dev/video*
ls -l /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

The current user should normally be a member of `video` for the camera and `dialout` for the serial controller.

## 5. Optional service installation

The included systemd unit is only an example. Replace the user and paths first:

```bash
sed 's/REPLACE_USER/luke/g' systemd/laser-camera-aligner.service | \
  sudo tee /etc/systemd/system/laser-camera-aligner.service
sudo systemctl daemon-reload
sudo systemctl enable --now laser-camera-aligner.service
```

Do not add `--hardware` to an unattended service during initial bring-up. Laser jobs must remain attended.
