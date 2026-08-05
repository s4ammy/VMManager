"""VMManager: a desktop manager for libvirt/QEMU virtual machines.

APP_NAME is the name people see - window title, tray, the About-style places.
The lowercase `vmmanager` is the identifier: the command, the desktop file id,
the Python package, and the directories under ~/.cache, ~/.config and
~/.local/share. Those are not interchangeable, so they are kept apart.
"""

APP_NAME = "VMManager"
