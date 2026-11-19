"""Pick a real OpenVINO device for a "GPU" setting.

OpenVINO's GPU plugin can also see NVIDIA and AMD cards through OpenCL. CopyThat
is tuned for Intel GPUs, and the models run slowly or fail elsewhere, so "GPU"
means the first Intel GPU, or CPU when there is none.
"""


def resolve_device(device, core=None, log=print):
    """Return the device to compile on for a configured device name."""
    if device != "GPU":
        return device
    try:
        if core is None:
            import openvino as ov
            core = ov.Core()
        gpus = [d for d in core.available_devices if d.split(".")[0] == "GPU"]
        names = {d: str(core.get_property(d, "FULL_DEVICE_NAME")) for d in gpus}
    except Exception as e:
        log(f"  Could not list GPUs ({e}); using GPU as set")
        return device
    for gpu in gpus:
        if "intel" in names[gpu].lower():
            if gpu != device:
                log(f"  Using {gpu} ({names[gpu]}) for GPU")
            return gpu
    if gpus:
        log(f"  No Intel GPU ({', '.join(names.values())}); using CPU instead")
    else:
        log("  No GPU found; using CPU instead")
    return "CPU"
