import sys
import re
import os
import shutil
import time

TARGET_FILE = '/usr/share/perl5/PVE/QemuServer.pm'
BACKUP_FILE = '/usr/share/perl5/PVE/QemuServer.pm.bak'

PATCH_MARKER = 'proxmox-rmem: Override memory from external file if available'

def _ensure_backup():
    """Create a backup without clobbering an existing .bak."""
    if os.path.exists(BACKUP_FILE):
        ts = time.strftime('%Y%m%d%H%M%S')
        extra = f"{BACKUP_FILE}.{ts}"
        shutil.copy2(TARGET_FILE, extra)
        print(f"Backup already exists, additional backup created at {extra}")
        return

    shutil.copy2(TARGET_FILE, BACKUP_FILE)
    print(f"Backup created at {BACKUP_FILE}")


def _build_patch_code(indent: str) -> str:
    """Build patch code that runs after balloon/QMP processing."""
    ind = indent
    ind2 = indent + '    '
    ind3 = ind2 + '    '
    ind4 = ind3 + '    '
    return (
        f"\n{ind}# {PATCH_MARKER}\n"
        f"{ind}foreach my $vmid (keys %$res) {{\n"
        f"{ind2}if (-f \"/tmp/pve-vm-$vmid-mem-override\") {{\n"
        f"{ind3}if (open(my $fh, '<', \"/tmp/pve-vm-$vmid-mem-override\")) {{\n"
        f"{ind4}my $override_mem = <$fh>;\n"
        f"{ind4}chomp $override_mem;\n"
        f"{ind4}if ($override_mem && $override_mem =~ /^\\d+$/) {{\n"
        f"{ind4}    $res->{{$vmid}}->{{mem}} = $override_mem;\n"
        f"{ind4}}}\n"
        f"{ind4}close($fh);\n"
        f"{ind3}}}\n"
        f"{ind2}}}\n"
        f"{ind}}}\n\n"
    )


def _find_post_qmp_return_insertion(content: str):
    """Find insertion point just before the vmstatus() return $res; after QMP execution."""
    qmp_idx = content.find('$qmpclient->queue_execute(undef, 2);')
    if qmp_idx == -1:
        return None

    m = re.search(r'\n(?P<indent>[ \t]*)return\s+\$res;\n\}', content[qmp_idx:])
    if not m:
        return None

    insert_pos = qmp_idx + m.start() + 1  # after the leading newline
    return insert_pos, m.group('indent'), qmp_idx


def _remove_legacy_patch(content: str) -> str:
    """Remove the older in-loop patch (if present) so we can re-insert at the correct location."""
    # Legacy patch shape: marker + if (-f "/tmp/pve-vm-$vmid-mem-override") { ... }
    pattern = re.compile(
        r"\n[ \t]*#\s*" + re.escape(PATCH_MARKER) +
        r"\n[ \t]*if\s*\(\s*-f\s*\"/tmp/pve-vm-\$vmid-mem-override\"\s*\)\s*\{\n"
        r".*?\n[ \t]*\}\n\n",
        re.S,
    )
    return pattern.sub('\n', content, count=1)

def main():
    if not os.path.exists(TARGET_FILE):
        print(f"Error: {TARGET_FILE} not found.")
        sys.exit(1)

    with open(TARGET_FILE, 'r') as f:
        content = f.read()

    insertion = _find_post_qmp_return_insertion(content)
    if not insertion:
        print("Error: Could not find vmstatus post-QMP return insertion point in QemuServer.pm")
        sys.exit(1)

    insert_pos, indent, qmp_idx = insertion

    marker_idx = content.find(PATCH_MARKER)
    if marker_idx != -1:
        # If the marker is already after QMP execution, assume it is the newer patch location.
        if marker_idx > qmp_idx:
            print("Already patched.")
            sys.exit(0)

        print("Existing proxmox-rmem patch detected in legacy location; relocating...")
        _ensure_backup()
        content = _remove_legacy_patch(content)

        # Recompute insertion positions after removal.
        insertion = _find_post_qmp_return_insertion(content)
        if not insertion:
            print("Error: Could not re-find insertion point after removing legacy patch")
            sys.exit(1)
        insert_pos, indent, _ = insertion

    else:
        _ensure_backup()

    patch_code = _build_patch_code(indent)
    new_content = content[:insert_pos] + patch_code + content[insert_pos:]

    with open(TARGET_FILE, 'w') as f:
        f.write(new_content)
    
    print("Patch applied successfully.")
    print("Location: before 'return $res;' in vmstatus(), after QMP/balloon processing")
    # Note: Service restart is handled by install.sh to avoid disconnecting web console

if __name__ == "__main__":
    main()
