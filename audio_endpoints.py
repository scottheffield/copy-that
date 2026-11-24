"""Windows' current default speaker, asked of Windows itself each time.

PortAudio (and so PyAudioWPatch and sounddevice) reads the device list once
when it starts, so its idea of the default output never changes while the
app runs. Meeting recording follows the default speaker, so it asks the Core
Audio API instead. The caller's thread must have COM initialized.
"""
import ctypes
from ctypes import POINTER, Structure, c_ulong, c_ushort, c_void_p, c_wchar_p

from comtypes import COMMETHOD, GUID, HRESULT, IUnknown, CoCreateInstance, CLSCTX_ALL

E_RENDER, E_CAPTURE = 0, 1  # Speakers, microphones
E_CONSOLE = 0                 # The role Windows' sound settings call the default


class PROPERTYKEY(Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(Structure):
    # Only the string case is read: vt == VT_LPWSTR (31), value is a wide string pointer.
    _fields_ = [("vt", c_ushort), ("r1", c_ushort), ("r2", c_ushort), ("r3", c_ushort),
                ("value", c_void_p), ("extra", c_void_p)]


PKEY_DEVICE_FRIENDLY_NAME = PROPERTYKEY(GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)
VT_LPWSTR = 31


class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_ulong), "count")),
        COMMETHOD([], HRESULT, "GetAt", (["in"], c_ulong, "index"), (["out"], POINTER(PROPERTYKEY), "key")),
        COMMETHOD([], HRESULT, "GetValue", (["in"], POINTER(PROPERTYKEY), "key"),
                  (["out"], POINTER(PROPVARIANT), "value")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID), "iid"), (["in"], c_ulong, "context"),
                  (["in"], c_void_p, "params"), (["out"], POINTER(c_void_p), "interface")),
        COMMETHOD([], HRESULT, "OpenPropertyStore", (["in"], c_ulong, "access"),
                  (["out"], POINTER(POINTER(IPropertyStore)), "store")),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_wchar_p), "id")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], c_ulong, "flow"), (["in"], c_ulong, "mask"),
                  (["out"], POINTER(c_void_p), "devices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], c_ulong, "flow"), (["in"], c_ulong, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "device")),
    ]


CLSID_MM_DEVICE_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")


class DefaultSpeaker:
    """Reads the default output's friendly name, e.g. "Speakers (EMEET OfficeCore M0 Plus)".
    Create and use it on one thread."""

    FLOW = E_RENDER

    def __init__(self):
        self._enumerator = CoCreateInstance(CLSID_MM_DEVICE_ENUMERATOR, IMMDeviceEnumerator, CLSCTX_ALL)

    def name(self):
        device = self._enumerator.GetDefaultAudioEndpoint(self.FLOW, E_CONSOLE)
        store = device.OpenPropertyStore(0)  # STGM_READ
        value = store.GetValue(ctypes.byref(PKEY_DEVICE_FRIENDLY_NAME))
        try:
            if value.vt != VT_LPWSTR or not value.value:
                return None
            return ctypes.wstring_at(value.value)
        finally:
            ctypes.oledll.ole32.PropVariantClear(ctypes.byref(value))


class DefaultMicrophone(DefaultSpeaker):
    """The default input's friendly name, e.g. "Microphone (EMEET OfficeCore M0 Plus)"."""

    FLOW = E_CAPTURE


def product(name):
    """The hardware part of an endpoint name: "Speakers (6- Cirrus Logic XU)" -> "cirrus logic xu".
    A headset's mic and headphones share it; a laptop's array and speakers don't."""
    import re
    if not name:
        return ""
    inner = re.search(r"\((.*)\)\s*$", name)
    text = inner.group(1) if inner else name
    return re.sub(r"^\d+\s*-\s*", "", text).strip().casefold()


def same_device(mic, speaker):
    return bool(product(mic)) and product(mic) == product(speaker)
