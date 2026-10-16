---
name: disk_usage_linux
when: User asks about disk space, free space, biggest files, directory sizes (on this Linux host)
needs: [df]
---

# Disk space and usage (Linux)

Free space across mounts (human-readable, real filesystems only):

```
local_shell:
df -h -x tmpfs -x devtmpfs -x overlay
```

Size of a directory:

```
local_shell:
du -sh /some/dir
```

Top 10 biggest files in a directory tree:

```
local_shell:
find /some/dir -xdev -type f -printf '%s %p\n' 2>/dev/null | sort -rn | head -10 | awk '{printf "%.1f MB  %s\n", $1/1048576, $2}'
```
