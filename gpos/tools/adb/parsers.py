"""Strict parsers and validators for the ADB evidence adapter (Phase 2C-3).

Pure functions over the exact bytes a target returned (the process boundary's private raw capture) and
over the two caller-supplied tokens that reach a command line. They start no process and read no file.

Everything here fails closed: output that is not exactly understood raises `TargetOutputError`, and a
token that is not exactly the documented form is refused, never repaired.
"""

import re
import struct

# ---------------------------------------------------------------- caller tokens

# A local ADB serial: a USB device serial or an emulator serial such as `emulator-5554`. Deliberately
# narrower than every string ADB accepts: no `:` (a TCP/IP `host:port` or `usb:` path target), no `.`
# (an IPv4 address or an mDNS service name such as `adb-XXXX._adb-tls-connect._tcp`), no whitespace, quote,
# path or shell syntax, and no leading `-`, so the value can never read as an option.
SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")

# An Android application id: two or more dot-separated segments, each starting with a letter and made of
# letters, digits and underscores. No `:` (a process-name suffix such as `:remote` is out of scope), no
# whitespace, quotes, slashes, shell metacharacters or leading `-`. The remote shell receives it inside a
# command string, so nothing outside this grammar may ever reach it.
PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+")
MAX_PACKAGE = 255


def serial_problem(value):
    """None when `value` is a usable local ADB serial, else the reason it is refused."""
    if not isinstance(value, str) or not value:
        return ("the adb_serial input must name the exact ADB serial of the target; a target is never chosen "
                "automatically")
    if ":" in value or "." in value:
        return (f"device {value[:80]!r} is a network (TCP/IP or wireless mDNS) target; wireless ADB is not "
                f"supported in this phase, only local USB and emulator serials")
    if not SERIAL.fullmatch(value):
        return (f"device {value[:80]!r} is not a local ADB serial (letters, digits, '-' and '_', starting with a "
                f"letter or digit, at most 64 characters)")
    return None


def package_problem(value):
    """None when `value` is a usable Android application id, else the reason it is refused."""
    if not isinstance(value, str) or not value:
        return "package_name is required: the exact application id whose memory is captured"
    if len(value) > MAX_PACKAGE:
        return f"package_name is longer than {MAX_PACKAGE} characters"
    if not PACKAGE.fullmatch(value):
        return (f"package_name {value[:80]!r} is not an Android application id (dot-separated segments of "
                f"letters, digits and '_', each starting with a letter)")
    return None


class TargetOutputError(ValueError):
    """The target's output is not the complete, expected form; nothing may be reported from it."""


# ---------------------------------------------------------------- getprop -> device report

# Exactly the properties the device report carries: identity of the target, its OS build and boot state.
# Nothing else is ever copied, whatever else the target prints (serial numbers, network, radio, account
# and user properties included).
REPORT_PROPERTIES = {
    "manufacturer": "ro.product.manufacturer",
    "model": "ro.product.model",
    "device_codename": "ro.product.device",
    "primary_abi": "ro.product.cpu.abi",
    "android_release": "ro.build.version.release",
    "api_level": "ro.build.version.sdk",
    "security_patch": "ro.build.version.security_patch",
    "build_fingerprint": "ro.build.fingerprint",
    "boot_completed": "sys.boot_completed",
}
MAX_VALUE = 256
MIN_API, MAX_API = 1, 1000
_RECORD = re.compile(rb"\[([A-Za-z0-9_.\-:@]+)\]: \[(.*)")
_PATCH = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def parse_getprop(raw):
    """{property: value bytes} for every record in `getprop` output. Raises TargetOutputError.

    Each record is `[name]: [value]`. A value may span lines (real targets print, for example, a boot
    reason history over several lines), so a record ends at the first line that ends with `]`. A line
    outside any record, an unterminated final record or a name printed twice is an error, never a guess.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TargetOutputError("getprop output must be the exact captured bytes")
    records, name, value = {}, None, None
    for line in bytes(raw).replace(b"\r\n", b"\n").split(b"\n"):
        if name is None:
            if not line:
                continue
            match = _RECORD.fullmatch(line)
            if not match:
                raise TargetOutputError("getprop output has a line that is not a property record")
            name, value = match.group(1).decode("ascii"), match.group(2)
        else:
            value += b"\n" + line
        if value.endswith(b"]"):
            if name in records:
                raise TargetOutputError("getprop output names a property twice")
            records[name] = value[:-1]
            name = value = None
    if name is not None:
        raise TargetOutputError("getprop output ends inside a property record")
    return records


def device_report(raw):
    """The normalized device report from `getprop` output. Raises TargetOutputError.

    Only `REPORT_PROPERTIES` are read; every one is required, and every value is checked: printable text
    of bounded length, an integer API level in range, a dated security patch, and a completed boot.
    """
    records = parse_getprop(raw)
    report = {}
    for key, prop in REPORT_PROPERTIES.items():
        if prop not in records:
            raise TargetOutputError(f"the target did not report {prop}")
        try:
            text = records[prop].decode("utf-8")
        except UnicodeDecodeError:
            raise TargetOutputError(f"{prop} is not valid UTF-8") from None
        if not text or len(text) > MAX_VALUE or not text.isprintable() or text != text.strip():
            raise TargetOutputError(f"{prop} is empty, too long or contains control characters")
        report[key] = text
    if not re.fullmatch(r"[0-9]{1,4}", report["api_level"]) or not MIN_API <= int(report["api_level"]) <= MAX_API:
        raise TargetOutputError("ro.build.version.sdk is not an API level")
    report["api_level"] = int(report["api_level"])
    if not _PATCH.fullmatch(report["security_patch"]):
        raise TargetOutputError("ro.build.version.security_patch is not a YYYY-MM-DD date")
    if report["boot_completed"] != "1":
        raise TargetOutputError("sys.boot_completed is not 1")
    report["boot_completed"] = True
    return report


# ---------------------------------------------------------------- physical target and its GPOS identity

# Read to classify the target, never copied. The Android emulator (goldfish / ranchu) sets ro.kernel.qemu and
# ro.boot.qemu to 1 and its build characteristics to `emulator`; Cuttlefish and Genymotion virtual devices
# report their own virtual hardware names. Real targets observed: ro.hardware `qcom`, no qemu property set.
EMULATOR_PROPERTIES = ("ro.kernel.qemu", "ro.boot.qemu", "ro.hardware", "ro.boot.hardware", "ro.build.characteristics")
VIRTUAL_HARDWARE = {"goldfish", "ranchu", "vbox86", "cutf_cvm"}
MAX_IDENTITY = 200


def target_kind(records):
    """"physical", "emulator", or None when the properties cannot establish either.

    Physical is concluded only positively: a hardware name is reported, it is not a known virtual one, no
    qemu property is set to anything but 0, and the build does not call itself an emulator. Any indication
    of emulation is an emulator; anything that cannot be read is unknown, never assumed physical.
    """
    def value(prop):
        raw = records.get(prop, b"")
        return raw.decode("ascii", errors="replace").strip() if isinstance(raw, (bytes, bytearray)) else ""
    qemu = {value("ro.kernel.qemu"), value("ro.boot.qemu")} - {""}
    hardware = {value("ro.hardware"), value("ro.boot.hardware")} - {""}
    if "1" in qemu or hardware & VIRTUAL_HARDWARE or \
            "emulator" in {c.strip() for c in value("ro.build.characteristics").split(",")}:
        return "emulator"
    if not value("ro.hardware") or qemu - {"0"}:
        return None
    return "physical"


def canonical_device_identity(report):
    """The GPOS reference-device identity of a physical target, from its public device report:

        <manufacturer> <model> / Android <release> (API <api level>)

    e.g. `ExampleCorp PhoneX / Android 12 (API 31)`. Deterministic, at most 200 characters, and built only
    from allowlisted report fields: never a serial, fingerprint, Android ID or network identifier. This is
    the value a request's `device` must equal, the value provenance records, and the value a project lists
    in `reference_devices`.
    """
    identity = (f"{report['manufacturer']} {report['model']} / Android {report['android_release']} "
                f"(API {report['api_level']})")
    if len(identity) > MAX_IDENTITY:
        raise TargetOutputError("the target's canonical device identity is longer than 200 characters")
    return identity


# ---------------------------------------------------------------- screencap -> PNG

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
MAX_DIMENSION = 16384


def png_dimensions(raw):
    """(width, height) of a complete PNG stream. Raises TargetOutputError.

    Structure only: the signature, a well-formed IHDR as the first chunk, bounded dimensions and the IEND
    chunk as the last twelve bytes (so a stream cut short is refused). The image is never decoded,
    rendered or inspected.
    """
    data = bytes(raw) if isinstance(raw, (bytes, bytearray)) else b""
    if len(data) < 8 + 25 + 12 or not data.startswith(PNG_SIGNATURE):
        raise TargetOutputError("the screenshot is not a PNG stream")
    length, kind = struct.unpack(">I4s", data[8:16])
    if length != 13 or kind != b"IHDR":
        raise TargetOutputError("the PNG does not begin with a valid IHDR chunk")
    width, height = struct.unpack(">II", data[16:24])
    if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
        raise TargetOutputError(f"the PNG dimensions {width}x{height} are out of range")
    if not data.endswith(PNG_IEND):
        raise TargetOutputError("the PNG stream does not end with IEND; it is incomplete")
    return width, height


# ---------------------------------------------------------------- dumpsys meminfo -s

class ProcessNotRunning(Exception):
    """The target reported no process for the requested package: there is no snapshot to report."""


_NOT_RUNNING = re.compile(r"No process found for: (\S+)")
_HEADER = re.compile(r"\*\* MEMINFO in pid ([0-9]{1,10}) \[([^\]]+)\] \*\*")
_TOTAL_PSS = re.compile(r"\s*TOTAL PSS:\s+[0-9]+\b.*")


def meminfo_snapshot(raw, package):
    """(normalized text, pid) for `dumpsys meminfo -s <package>` output. Raises ProcessNotRunning or
    TargetOutputError.

    The text must be printable ASCII lines (line endings normalized to `\\n`). It must describe exactly
    one process, and that process must be the requested package: another process, several processes
    with the name, or no memory totals are all refused rather than reported. The body is kept as the
    report; it is not reinterpreted, because Android documents that its details vary between versions.
    """
    if not isinstance(raw, (bytes, bytearray)):
        raise TargetOutputError("meminfo output must be the exact captured bytes")
    try:
        text = bytes(raw).decode("ascii")
    except UnicodeDecodeError:
        raise TargetOutputError("meminfo output is not text") from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip() or any(not (c == "\n" or c == "\t" or " " <= c <= "~") for c in text):
        raise TargetOutputError("meminfo output is empty or contains control characters")
    lines = text.split("\n")
    for line in lines:
        missing = _NOT_RUNNING.fullmatch(line.strip())
        if missing:
            if missing.group(1) != package:
                raise TargetOutputError("meminfo reported a missing process for a different name")
            raise ProcessNotRunning(package)
    headers = [m for m in (_HEADER.fullmatch(line.strip()) for line in lines) if m]
    if len(headers) != 1:
        raise TargetOutputError(f"meminfo describes {len(headers)} processes; exactly one is required")
    pid, name = headers[0].groups()
    if name != package:
        raise TargetOutputError("meminfo describes a different process than the one requested")
    if not any(_TOTAL_PSS.fullmatch(line) for line in lines):
        raise TargetOutputError("meminfo has no memory totals; it is not a usable snapshot (a busy process may "
                                "not answer the memory dump in time)")
    return text.rstrip("\n") + "\n", int(pid)
