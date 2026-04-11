"""Input validation for shell command parameters.

Every user-supplied value that ends up in a shell command MUST pass through
one of these validators first.  They raise ValueError on invalid input so
the caller can return a safe error to the client.
"""

import re

# ---------------------------------------------------------------------------
# Allowed character patterns (whitelists)
# ---------------------------------------------------------------------------

# ZFS pool/dataset/snapshot names: alphanumeric, _, -, ., :, /
# Snapshot separator @ is allowed for full snapshot names like pool/ds@snap
_ZFS_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_./@:-]*$')

# Pool names only (no / or @)
_POOL_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]*$')

# ZFS property names: lowercase, dots, colons, hyphens
_ZFS_PROP_RE = re.compile(r'^[a-z][a-z0-9_:.-]*$')

# ZFS property values: alphanumeric plus some safe chars (no shell metacharacters)
_ZFS_VALUE_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_./:@=, -]*$')

# VMID: numeric only
_VMID_RE = re.compile(r'^[0-9]+$')

# VM type: qemu or lxc only
_VM_TYPES = {"qemu", "lxc"}

# File paths: no shell metacharacters, no null bytes, no ..
_SAFE_PATH_RE = re.compile(r'^[a-zA-Z0-9/_.@ -]+$')

# Integer limits
_LIMIT_RE = re.compile(r'^[0-9]+$')

MAX_INPUT_LENGTH = 512


# ---------------------------------------------------------------------------
# Validator functions
# ---------------------------------------------------------------------------

def _check_length(value, name="input"):
    """Reject excessively long inputs (DoS prevention)."""
    if len(value) > MAX_INPUT_LENGTH:
        raise ValueError(f"{name} too long (max {MAX_INPUT_LENGTH} chars)")


def validate_pool_name(value):
    """Validate a ZFS pool name."""
    if not value or not isinstance(value, str):
        raise ValueError("Pool name is required")
    value = value.strip()
    _check_length(value, "Pool name")
    if not _POOL_NAME_RE.match(value):
        raise ValueError(f"Invalid pool name: only alphanumeric, _, -, . allowed")
    return value


def validate_zfs_name(value, label="ZFS name"):
    """Validate a ZFS dataset, snapshot, or full path name (pool/dataset@snap)."""
    if not value or not isinstance(value, str):
        raise ValueError(f"{label} is required")
    value = value.strip()
    _check_length(value, label)
    if not _ZFS_NAME_RE.match(value):
        raise ValueError(f"Invalid {label}: only alphanumeric, _, -, ., :, /, @ allowed")
    return value


def validate_zfs_property(prop):
    """Validate a ZFS property name."""
    if not prop or not isinstance(prop, str):
        raise ValueError("Property name is required")
    prop = prop.strip()
    _check_length(prop, "Property name")
    if not _ZFS_PROP_RE.match(prop):
        raise ValueError(f"Invalid property name: only lowercase, digits, _, :, ., - allowed")
    return prop


def validate_zfs_value(value):
    """Validate a ZFS property value."""
    if value is None:
        raise ValueError("Property value is required")
    value = str(value).strip()
    _check_length(value, "Property value")
    if not _ZFS_VALUE_RE.match(value):
        raise ValueError(f"Invalid property value: contains disallowed characters")
    return value


def validate_vmid(value):
    """Validate a Proxmox VMID (numeric)."""
    if not value:
        raise ValueError("VMID is required")
    value = str(value).strip()
    if not _VMID_RE.match(value):
        raise ValueError(f"Invalid VMID: must be numeric")
    return value


def validate_vm_type(value):
    """Validate VM type (qemu or lxc)."""
    if not value or value not in _VM_TYPES:
        raise ValueError(f"Invalid VM type: must be 'qemu' or 'lxc'")
    return value


def validate_path(value, label="Path"):
    """Validate a filesystem path (no shell metacharacters, no traversal)."""
    if not value or not isinstance(value, str):
        raise ValueError(f"{label} is required")
    value = value.strip()
    _check_length(value, label)
    # Block path traversal
    if ".." in value:
        raise ValueError(f"{label}: path traversal (..) not allowed")
    # Block null bytes
    if "\x00" in value:
        raise ValueError(f"{label}: null bytes not allowed")
    if not _SAFE_PATH_RE.match(value):
        raise ValueError(f"{label}: contains disallowed characters")
    return value


def validate_limit(value, default=200, maximum=10000):
    """Validate a numeric limit parameter."""
    if value is None:
        return default
    value = str(value).strip()
    if not _LIMIT_RE.match(value):
        raise ValueError("Limit must be a positive integer")
    n = int(value)
    if n > maximum:
        raise ValueError(f"Limit too large (max {maximum})")
    return n


def validate_snapshot_name(value):
    """Validate a snapshot name (the part after @)."""
    return validate_zfs_name(value, "Snapshot name")


def validate_dataset_name(value):
    """Validate a dataset name (pool/path)."""
    return validate_zfs_name(value, "Dataset name")


def validate_clone_name(value):
    """Validate a clone target name."""
    return validate_zfs_name(value, "Clone name")
