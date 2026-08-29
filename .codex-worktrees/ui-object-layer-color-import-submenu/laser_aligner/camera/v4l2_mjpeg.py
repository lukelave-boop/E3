from __future__ import annotations

import ctypes
import errno
import fcntl
import mmap
import os
import select
import typing
from dataclasses import dataclass

V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
V4L2_MEMORY_MMAP = 1
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_STREAMING = 0x04000000
V4L2_CAP_DEVICE_CAPS = 0x80000000
V4L2_FIELD_ANY = 0
V4L2_PIX_FMT_MJPEG = int.from_bytes(b"MJPG", "little")
_MAX_JPEG_BYTES = 4 * 1024 * 1024


def _ioc(direction: int, type_: str, number: int, structure: type[ctypes.Structure]) -> int:
    size = ctypes.sizeof(structure)
    return (direction << 30) | (size << 16) | (ord(type_) << 8) | number


class _Capability(ctypes.Structure):
    _fields_ = [
        ("driver", ctypes.c_uint8 * 16),
        ("card", ctypes.c_uint8 * 32),
        ("bus_info", ctypes.c_uint8 * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    ]


class _PixFormat(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("pixelformat", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("bytesperline", ctypes.c_uint32),
        ("sizeimage", ctypes.c_uint32),
        ("colorspace", ctypes.c_uint32),
        ("priv", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("ycbcr_enc", ctypes.c_uint32),
        ("quantization", ctypes.c_uint32),
        ("xfer_func", ctypes.c_uint32),
    ]


class _FormatUnion(ctypes.Union):
    _fields_ = [
        ("pix", _PixFormat),
        ("raw", ctypes.c_uint8 * 200),
        ("alignment", ctypes.c_uint64),
    ]


class _Format(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", _FormatUnion)]


class _Fraction(ctypes.Structure):
    _fields_ = [("numerator", ctypes.c_uint32), ("denominator", ctypes.c_uint32)]


class _CaptureParm(ctypes.Structure):
    _fields_ = [
        ("capability", ctypes.c_uint32),
        ("capturemode", ctypes.c_uint32),
        ("timeperframe", _Fraction),
        ("extendedmode", ctypes.c_uint32),
        ("readbuffers", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 4),
    ]


class _StreamParmUnion(ctypes.Union):
    _fields_ = [("capture", _CaptureParm), ("raw", ctypes.c_uint8 * 200)]


class _StreamParm(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("parm", _StreamParmUnion)]


class _RequestBuffers(ctypes.Structure):
    _fields_ = [
        ("count", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("flags", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


class _TimeCode(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("frames", ctypes.c_uint8),
        ("seconds", ctypes.c_uint8),
        ("minutes", ctypes.c_uint8),
        ("hours", ctypes.c_uint8),
        ("userbits", ctypes.c_uint8 * 4),
    ]


class _BufferMemory(ctypes.Union):
    _fields_ = [("offset", ctypes.c_uint32), ("userptr", ctypes.c_ulong), ("fd", ctypes.c_int32)]


class _Buffer(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("timestamp_seconds", ctypes.c_long),
        ("timestamp_microseconds", ctypes.c_long),
        ("timecode", _TimeCode),
        ("sequence", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("m", _BufferMemory),
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_int32),
    ]


VIDIOC_QUERYCAP = _ioc(2, "V", 0, _Capability)
VIDIOC_S_FMT = _ioc(3, "V", 5, _Format)
VIDIOC_REQBUFS = _ioc(3, "V", 8, _RequestBuffers)
VIDIOC_QUERYBUF = _ioc(3, "V", 9, _Buffer)
VIDIOC_QBUF = _ioc(3, "V", 15, _Buffer)
VIDIOC_DQBUF = _ioc(3, "V", 17, _Buffer)
VIDIOC_STREAMON = _ioc(1, "V", 18, ctypes.c_int)
VIDIOC_STREAMOFF = _ioc(1, "V", 19, ctypes.c_int)
VIDIOC_S_PARM = _ioc(3, "V", 22, _StreamParm)


class _System(typing.Protocol):
    def open(self, path: str, flags: int) -> int: ...
    def close(self, fd: int) -> None: ...
    def ioctl(self, fd: int, request: int, value: object) -> object: ...
    def mmap(self, fd: int, length: int, offset: int) -> mmap.mmap: ...
    def readable(self, fd: int, timeout: float) -> bool: ...


class _LinuxSystem:
    open = staticmethod(os.open)
    close = staticmethod(os.close)

    @staticmethod
    def ioctl(fd: int, request: int, value: object) -> object:
        return fcntl.ioctl(fd, request, value)

    @staticmethod
    def mmap(fd: int, length: int, offset: int) -> mmap.mmap:
        return mmap.mmap(
            fd,
            length,
            flags=mmap.MAP_SHARED,
            prot=mmap.PROT_READ | mmap.PROT_WRITE,
            offset=offset,
        )

    @staticmethod
    def readable(fd: int, timeout: float) -> bool:
        readable, _, _ = select.select([fd], [], [], timeout)
        return bool(readable)


@dataclass(slots=True)
class _MappedBuffer:
    mapping: mmap.mmap
    length: int


class NativeV4L2MjpegCapture:
    """Single-owner V4L2 MMAP reader for validated MJPEG camera packets."""

    def __init__(
        self,
        device: str,
        width: int,
        height: int,
        fps: float,
        *,
        system: _System | None = None,
        buffer_count: int = 4,
        read_timeout: float = 2.0,
        max_packet_bytes: int = _MAX_JPEG_BYTES,
    ) -> None:
        self.device = device
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.negotiated_fps = 0.0
        self._system = system or _LinuxSystem()
        self._read_timeout = float(read_timeout)
        self._max_packet_bytes = int(max_packet_bytes)
        self._fd = -1
        self._buffers: list[_MappedBuffer] = []
        self._streaming = False
        try:
            self._fd = self._system.open(device, os.O_RDWR | os.O_NONBLOCK)
            self._configure(buffer_count)
        except Exception:
            self.release()
            raise

    def _ioctl(self, request: int, value: object) -> None:
        self._system.ioctl(self._fd, request, value)

    def _configure(self, requested_buffers: int) -> None:
        capability = _Capability()
        self._ioctl(VIDIOC_QUERYCAP, capability)
        caps = (
            capability.device_caps
            if capability.capabilities & V4L2_CAP_DEVICE_CAPS
            else capability.capabilities
        )
        required = V4L2_CAP_VIDEO_CAPTURE | V4L2_CAP_STREAMING
        if caps & required != required:
            raise OSError(errno.ENOTSUP, "V4L2 device lacks capture or streaming capability")

        format_ = _Format()
        format_.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        format_.fmt.pix.width = self.width
        format_.fmt.pix.height = self.height
        format_.fmt.pix.pixelformat = V4L2_PIX_FMT_MJPEG
        format_.fmt.pix.field = V4L2_FIELD_ANY
        self._ioctl(VIDIOC_S_FMT, format_)
        if (
            format_.fmt.pix.width != self.width
            or format_.fmt.pix.height != self.height
            or format_.fmt.pix.pixelformat != V4L2_PIX_FMT_MJPEG
        ):
            raise OSError(errno.ENOTSUP, "V4L2 device did not accept the requested MJPEG profile")

        parameters = _StreamParm()
        parameters.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        parameters.parm.capture.timeperframe.numerator = 1000
        parameters.parm.capture.timeperframe.denominator = max(1, round(self.fps * 1000))
        self._ioctl(VIDIOC_S_PARM, parameters)
        fraction = parameters.parm.capture.timeperframe
        if fraction.numerator and fraction.denominator:
            self.negotiated_fps = fraction.denominator / fraction.numerator

        request = _RequestBuffers()
        request.count = max(2, int(requested_buffers))
        request.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        request.memory = V4L2_MEMORY_MMAP
        self._ioctl(VIDIOC_REQBUFS, request)
        if request.count < 2:
            raise OSError(errno.ENOMEM, "V4L2 device supplied fewer than two capture buffers")
        for index in range(request.count):
            buffer = self._buffer(index)
            self._ioctl(VIDIOC_QUERYBUF, buffer)
            mapping = self._system.mmap(self._fd, buffer.length, buffer.m.offset)
            self._buffers.append(_MappedBuffer(mapping, int(buffer.length)))
            self._ioctl(VIDIOC_QBUF, buffer)
        buffer_type = ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE)
        self._ioctl(VIDIOC_STREAMON, buffer_type)
        self._streaming = True

    @staticmethod
    def _buffer(index: int = 0) -> _Buffer:
        buffer = _Buffer()
        buffer.index = index
        buffer.type = V4L2_BUF_TYPE_VIDEO_CAPTURE
        buffer.memory = V4L2_MEMORY_MMAP
        return buffer

    def isOpened(self) -> bool:  # noqa: N802 - mirrors cv2.VideoCapture
        return self._fd >= 0 and self._streaming

    def read(self) -> tuple[bool, bytes | None]:
        if not self.isOpened() or not self._system.readable(self._fd, self._read_timeout):
            return False, None
        buffer = self._buffer()
        try:
            self._ioctl(VIDIOC_DQBUF, buffer)
        except BlockingIOError:
            return False, None
        if buffer.index >= len(self._buffers):
            raise OSError(errno.EIO, "V4L2 returned an invalid capture buffer index")
        mapped = self._buffers[buffer.index]
        try:
            if buffer.bytesused > mapped.length:
                raise OSError(errno.EIO, "V4L2 packet exceeds its mapped buffer")
            if buffer.bytesused > self._max_packet_bytes:
                raise OSError(errno.EOVERFLOW, "V4L2 packet exceeds the bounded JPEG limit")
            return True, bytes(mapped.mapping[: buffer.bytesused])
        finally:
            self._ioctl(VIDIOC_QBUF, buffer)

    def release(self) -> None:
        fd = self._fd
        if fd < 0:
            return
        self._fd = -1
        if self._streaming:
            try:
                self._system.ioctl(fd, VIDIOC_STREAMOFF, ctypes.c_int(V4L2_BUF_TYPE_VIDEO_CAPTURE))
            except OSError:
                pass
            self._streaming = False
        for buffer in self._buffers:
            try:
                buffer.mapping.close()
            except (BufferError, OSError):
                pass
        self._buffers.clear()
        self._system.close(fd)


__all__ = ["NativeV4L2MjpegCapture"]
