# Runtime files

Runtime state is app-folder local only.

Expected runtime files:

```text
./config.json
./m3u_picker.db
./master_playlist_cache.m3u
./exports/custom.m3u
```

This build intentionally does not use `~/Library/Application Support`, appdirs, platformdirs, `Path.home()`, or the shell current working directory for runtime state.
