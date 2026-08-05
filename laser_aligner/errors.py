class LaserAlignerError(Exception):
    """Base application exception."""


class CameraError(LaserAlignerError):
    """Camera could not be opened or read."""


class CalibrationError(LaserAlignerError):
    """Calibration data is missing or invalid."""


class MachineError(LaserAlignerError):
    """Motion controller operation failed."""


class SafetyError(MachineError):
    """Operation was blocked by a software safety gate."""


class SvgError(LaserAlignerError):
    """SVG could not be parsed or converted."""
